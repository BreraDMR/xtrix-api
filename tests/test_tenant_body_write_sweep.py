"""Outsiders send every write route that needs a body, stuffed with another salon's ids.

The body-less write sweep and the GET canary sweep leave a gap: routes with a required body
stop at validation there. Here a minimal valid body is built from the OpenAPI schema itself,
every `*_id` field and path param points at salon A, and another owner plus a solo specialist
send it - once with all ids, once with only the required ones, and the other owner also once
"for their own salon". A path with A's id never gives a 2xx. A body-only route may answer 2xx
when it quietly works in the caller's own salon, but then nothing of salon A may change and no
row of another salon may end up pointing at A's rows. Finally A's own owner replays the same
requests: a good share has to pass, otherwise the bodies are junk and the sweep proves nothing.
"""
import re
from datetime import timedelta

from sqlalchemy import text

from app.api.core.clock import utcnow
from app.db.models.user import User
from tests.test_visit_financial_privacy import ok

# Anyone may call these by design (public booking, visitor accounts, login and friends),
# they don't run as a salon member, so "an outsider got 2xx" means nothing there
OPEN_PREFIXES = ('/public/', '/visitor/auth/', '/auth/', '/users/login', '/users/refresh', '/users/logout')

# Bodies the schema can't express: "either this or that" rules checked in a validator
BODY_EXTRAS = {'PUT /visits/{visit_id}/price': {'legacy_price': '10.00'},
               'POST /visits/{visit_id}/price-corrections': {'legacy_price': '10.00'}}

# Salon A also gets one of each smaller object, so path ids lead to real rows instead of 404s.
# Bodies come from the same generator; the answer's `id` fills the matching path param.
SEEDS = (
    ('POST', '/businesses/me/campaigns', 'campaign_id'),
    ('POST', '/businesses/me/questionnaires/', 'template_id'),
    ('POST', '/loyalty/program', None),
    ('POST', '/loyalty/levels', 'level_id'),
    ('POST', '/loyalty/promotions', 'promotion_id'),
    ('POST', '/expenses/categories', 'category_id'),
    ('POST', '/expenses/', 'expense_id'),
    ('POST', '/products/', 'product_id'),
    ('POST', '/clients/{client_id}/notes', 'note_id'),
    ('POST', '/schedules/blocks', 'block_id'),
    ('POST', '/schedules/exceptions', 'exception_id'),
    ('POST', '/schedules/work', 'ws_id'),
    ('POST', '/employees/{employee_id}/salary/calculate', 'salary_id'),
    ('POST', '/assistant/conversations', 'conversation_id'),
)

PRICE = '98765.43'
CLIENT_NAME = 'Bodycanary Wrobelzq'
SALON_NAME = 'Body Canary Salon Zq'
FUTURE = (utcnow() + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


def _resolve(spec, schema):
    while '$ref' in schema:
        name = schema['$ref'].rsplit('/', 1)[-1]
        schema = spec['components']['schemas'][name]
    return schema


def _kinds(spec, schema):
    # every JSON type a (possibly nested anyOf) schema allows
    schema = _resolve(spec, schema)
    found = {schema['type']} if 'type' in schema else set()
    for key in ('anyOf', 'oneOf', 'allOf'):
        for option in schema.get(key, ()):
            found |= _kinds(spec, option)
    return found


def _pick_id(spec, name, schema, ids):
    """Salon A's id for an id-looking field, if the field takes a number at all (UUID ids don't)."""
    kinds = _kinds(spec, schema)
    if name.endswith('_ids') and 'array' in kinds:
        single = ids.get(name[:-1])
        return [single] if single is not None else None
    if (name in ids or name.endswith('_id')) and kinds & {'integer', 'number'}:
        return ids.get(name, ids['fallback'])
    return None


def _from_pattern(pattern):
    # just enough for the shapes the API uses: optional literal prefix + one char class + {n}
    match = re.fullmatch(r'\^([\w-]*)\[([^\]]+)\]\{(\d+)(,\d*)?\}\$', pattern)
    if not match:
        return None
    prefix, chars, count, _ = match.groups()
    return prefix + chars.lstrip('^')[0] * int(count)


def _string(name, schema):
    fmt = schema.get('format')
    lowered = name.lower()
    later = lowered.startswith('end') or lowered.endswith(('_end', 'end_time', '_to', '_until'))
    moment = FUTURE + timedelta(hours=1) if later else FUTURE
    if fmt == 'date-time':
        return moment.isoformat()
    if fmt == 'date':
        return moment.date().isoformat()
    if fmt == 'time':
        return moment.strftime('%H:%M:%S')
    if fmt == 'email':
        return 'body.sweep@example.com'
    if fmt == 'uuid':
        return '00000000-0000-4000-8000-000000000001'
    if fmt in ('uri', 'url'):
        return 'https://example.com/x'
    if 'currency' in lowered:
        return 'CZK'
    if schema.get('pattern') and _from_pattern(schema['pattern']):
        return _from_pattern(schema['pattern'])
    if 'phone' in lowered:
        return '+420777000111'
    if 'email' in lowered:
        return 'body.sweep@example.com'
    if lowered == 'role':
        return 'specialist'
    if lowered.endswith('time'):
        return moment.strftime('%H:%M')
    if 'date' in lowered or lowered.endswith('_at'):
        return moment.date().isoformat()
    if 'password' in lowered:
        return 'Str0ng!Pass-word'
    size = max(schema.get('minLength', 1), 1)
    value = 'Sweep' + 'x' * max(size - 5, 0)
    return value[:schema['maxLength']] if 'maxLength' in schema else value


def _number(schema, integer):
    low = schema.get('minimum', schema.get('exclusiveMinimum', 0))
    value = low + 1 if 'exclusiveMinimum' in schema or low <= 0 else low
    if 'maximum' in schema:
        value = min(value, schema['maximum'])
    return int(value) if integer else float(value)


def build_value(spec, schema, ids, name='', depth=0, extra_ids=True):
    """Smallest value the schema accepts, with every id-looking field aimed at salon A.

    extra_ids=False leaves optional ids out: a foreign category or client in the body often gets
    refused first and would hide a missing check on the path id itself.
    """
    schema = _resolve(spec, schema)
    picked = _pick_id(spec, name, schema, ids)
    if picked is not None:
        return picked
    if 'const' in schema:
        return schema['const']
    if 'enum' in schema:
        # plain text answers can be searched for salon A's markers, zipped spreadsheets can't
        return 'csv' if 'csv' in schema['enum'] else schema['enum'][0]
    if 'default' in schema and schema['default'] is not None and depth > 0:
        return schema['default']
    if 'allOf' in schema:
        merged = {}
        for part in schema['allOf']:
            merged.update(_resolve(spec, part))
        return build_value(spec, merged, ids, name, depth, extra_ids)
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            options = [o for o in schema[key] if _resolve(spec, o).get('type') != 'null']
            return build_value(spec, options[0], ids, name, depth, extra_ids) if options else None
    kind = schema.get('type')
    if kind == 'object' or 'properties' in schema:
        required = set(schema.get('required', ()))
        properties = schema.get('properties') or {}
        body = {}
        for prop, sub in properties.items():
            # optional fields stay out, except ids - those are the whole point
            if (prop in required or prop == 'format'
                    or (extra_ids and depth < 3 and _pick_id(spec, prop, sub, ids) is not None)):
                body[prop] = build_value(spec, sub, ids, prop, depth + 1, extra_ids)
        if not body and properties and depth == 0:
            # an all-optional update body still needs one change, or it stops at "nothing to update"
            plain = [(k, v) for k, v in properties.items() if _pick_id(spec, k, v, ids) is None]
            prop, sub = (plain or list(properties.items()))[0]
            body[prop] = build_value(spec, sub, ids, prop, depth + 1, extra_ids)
        return body
    if kind == 'array':
        count = schema.get('minItems', 0)
        return [build_value(spec, schema.get('items', {}), ids, name, depth + 1, extra_ids)
                for _ in range(count)]
    if kind == 'string':
        return _string(name, schema)
    if kind == 'integer':
        return _number(schema, integer=True)
    if kind == 'number':
        return _number(schema, integer=False)
    if kind == 'boolean':
        return False
    return None


def _query(spec, operation, ids):
    # required query params get a value too, and any id-looking one points at salon A
    params = {'business_id': ids['business_id']}
    for param in operation.get('parameters', ()):
        if param.get('in') != 'query':
            continue
        schema = param.get('schema', {})
        value = _pick_id(spec, param['name'], schema, ids)
        if value is None and param.get('required'):
            value = build_value(spec, schema, ids, param['name'], 1)
        if value is not None:
            params[param['name']] = value
    return params


def _body_schema(operation):
    content = (operation.get('requestBody') or {}).get('content', {})
    if 'application/json' in content:
        return 'json', content['application/json']['schema']
    if 'multipart/form-data' in content:
        return 'form', content['multipart/form-data']['schema']
    return None, None


def _url(spec, path, operation, ids):
    # path ids point at salon A as well; UUID ones (assistant conversations) get a valid dummy
    schemas = {p['name']: p.get('schema', {}) for p in operation.get('parameters', ()) if p.get('in') == 'path'}

    def fill(match):
        name = match.group(1)
        value = ids.get(name) if name in ids else _pick_id(spec, name, schemas.get(name, {}), ids)
        return str(value if value is not None else build_value(spec, schemas.get(name, {}), ids, name, 1))
    return re.sub(r'\{(\w+)(:[^}]*)?\}', fill, path)


def _has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_has_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_has_key(v, key) for v in value)
    return False


def _with_business(value, business_id):
    if isinstance(value, dict):
        return {k: business_id if k == 'business_id' else _with_business(v, business_id)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_with_business(v, business_id) for v in value]
    return value


def _points_at_salon(value, ids):
    wanted = {v for k, v in ids.items() if k != 'fallback'}
    if isinstance(value, dict):
        return any(_points_at_salon(v, ids) for k, v in value.items()
                   if k.endswith(('_id', '_ids')) or isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(_points_at_salon(v, ids) for v in value)
    return value in wanted


def _salon_digest(db, business_id, user_ids):
    # one hash per table over salon A's rows only: other salons may write their own stuff freely,
    # but nothing that belongs to A may move
    tables = db.execute(text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'business_id' ORDER BY table_name")).scalars().all()
    result = {}
    for table in tables:
        result[table] = db.execute(text(
            f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t "
            "WHERE business_id = :b"), {'b': business_id}).scalar()
    for table, where in (('businesses', 'id = :b'), ('users', 'id = ANY(:u)'),
                         ('visit_items', 'visit_id IN (SELECT id FROM visits WHERE business_id = :b)')):
        result[table] = db.execute(text(
            f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t "
            f"WHERE {where}"), {'b': business_id, 'u': list(user_ids)}).scalar()
    db.rollback()
    return result


FOREIGN_KEYS = """
SELECT kcu.table_name, kcu.column_name, ccu.table_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' AND ccu.column_name = 'id'
"""


def _links_into_salon(db, business_id, owned):
    """How many rows of other salons point at a row of salon A, per foreign key.

    A write that "succeeded" inside the caller's own salon but hangs off A's client, category,
    visit or staff is still a cross-salon leak, and A's own rows wouldn't change - so count those
    links. `owned` names A's rows in tables that carry no business_id (clients, users, employees).
    """
    with_business = set(db.execute(text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'business_id'")).scalars())
    counts = {}
    for child, column, parent in db.execute(text(FOREIGN_KEYS)).all():
        if child not in with_business or column == 'business_id':
            continue
        if parent in with_business:
            target = f'SELECT id FROM {parent} WHERE business_id = :b'
        elif parent in owned:
            target = f'SELECT id FROM {parent} WHERE id = ANY(:owned_{parent})'
        else:
            continue
        counts[f'{child}.{column} -> {parent}'] = db.execute(text(
            f"SELECT count(*) FROM {child} WHERE business_id IS DISTINCT FROM :b "
            f"AND {column} IN ({target})"),
            {'b': business_id, **{f'owned_{t}': list(v) for t, v in owned.items()}}).scalar()
    db.rollback()
    return counts


def _body_routes(spec):
    for path, operations in sorted(spec['paths'].items()):
        if path.startswith(OPEN_PREFIXES):
            continue
        for method in ('post', 'put', 'patch'):
            operation = operations.get(method)
            if operation and (operation.get('requestBody') or {}).get('required'):
                yield path, method, operation


def test_outsiders_cannot_run_body_writes_on_another_salon(client, db, make_user, headers_for):
    from app.main import app

    owner = make_user('business_owner')
    owner_headers = headers_for(owner)
    salon = ok(client.post('/businesses/', json={'name': SALON_NAME, 'business_type': 'salon'},
                           headers=owner_headers))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Body Canary Staff', 'email': 'body.canary.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False},
    }, headers=owner_headers))
    staff = db.get(User, employee['employee_user_id'])
    customer = ok(client.post('/clients/', json={'full_name': CLIENT_NAME}, headers=owner_headers))
    service = ok(client.post('/services/', json={
        'name': 'Body Canary Service', 'price': float(PRICE), 'duration_minutes': 30, 'specialist_id': staff.id,
    }, headers=owner_headers))
    visit = ok(client.post('/visits/', json={
        'client_id': customer['id'], 'specialist_id': staff.id, 'date_start': FUTURE.isoformat(),
        'service_id': service['id'], 'status': 'planned',
    }, headers=owner_headers))

    ids = {'visit_id': visit['id'], 'client_id': customer['id'], 'service_id': service['id'],
           'business_id': salon['id'], 'employee_id': employee['id'], 'user_id': owner.id,
           'specialist_id': staff.id, 'owner_id': owner.id, 'fallback': 1}

    other_headers = headers_for(make_user('business_owner'))
    other_salon = ok(client.post('/businesses/', json={'name': 'Other Body Salon', 'business_type': 'salon'},
                                 headers=other_headers))
    # caller -> (headers, their own salon or None)
    callers = {'other_owner': (other_headers, other_salon['id']),
               'solo_specialist': (headers_for(make_user('specialist')), None)}

    spec = app.openapi()
    seeded = {}
    for method, path, key in SEEDS:
        operation = spec['paths'][path][method.lower()]
        kind, schema = _body_schema(operation)
        response = client.request(method, _url(spec, path, operation, ids), headers=owner_headers,
                                  params=_query(spec, operation, ids), json=build_value(spec, schema, ids))
        seeded[path] = response.status_code
        if key and response.status_code < 300:
            ids[key] = response.json()['id']
    assert all(status < 300 for status in seeded.values()), seeded

    requests = []
    for path, method, operation in _body_routes(spec):
        kind, schema = _body_schema(operation)
        query = _query(spec, operation, ids)
        id_params = {k: v for k, v in query.items() if k != 'business_id'}
        route = f'{method.upper()} {path}'
        variants = []
        for extra_ids in (True, False):
            body = build_value(spec, schema, ids, extra_ids=extra_ids) if schema else None
            if isinstance(body, dict):
                body.update(BODY_EXTRAS.get(route, {}))
            if body not in variants:
                variants.append(body)
        for body in variants:
            if '{' not in path and not _points_at_salon(body, ids) and not id_params:
                continue
            payload = {'json': body} if kind == 'json' else {'data': body or {}}
            requests.append((route, method.upper(), _url(spec, path, operation, ids), query, payload))

    owned = {'clients': [customer['id']], 'users': [owner.id, staff.id], 'employees': [employee['id']]}
    before = _salon_digest(db, salon['id'], (owner.id, staff.id))
    links_before = _links_into_salon(db, salon['id'], owned)
    failures, reached, stuck, own_scope = [], set(), {}, []
    attempts = []
    for route, method, url, query, payload in requests:
        for who, (headers, own_salon) in callers.items():
            attempts.append((who, headers, route, method, url, query, payload))
            # a body naming A's salon is refused right away and hides what the other ids would do,
            # so the outsider also tries it "for their own salon" with the rest still aimed at A
            if own_salon and ('business_id' in query or _has_key(payload, 'business_id')):
                attempts.append((who, headers, route, method, url, _with_business(query, own_salon),
                                 _with_business(payload, own_salon)))
    for who, headers, route, method, url, query, payload in attempts:
        response = client.request(method, url, headers=headers, params=query, **payload)
        status = response.status_code
        if status == 422:
            stuck[route] = response.text[:300]
            continue
        reached.add(route)
        if status >= 300:
            continue
        markers = [m for m in (CLIENT_NAME, SALON_NAME, PRICE, '98765') if m in response.text]
        if '{' in route or markers:
            # A's id sits in the path, or A's data came back: either way it went through
            failures.append(f'{who} {route} -> {status} {markers} {response.text[:200]}')
        else:
            # the handler ignored the foreign ids and worked inside the caller's own scope;
            # the digest and the cross-link check below make sure that's really all it did
            own_scope.append(f'{who} {route}')

    assert not failures, '\n'.join(failures)
    assert _salon_digest(db, salon['id'], (owner.id, staff.id)) == before
    links_after = _links_into_salon(db, salon['id'], owned)
    assert links_after == links_before, {k: (links_before[k], v) for k, v in links_after.items()
                                         if v != links_before[k]}
    stuck = {route: why for route, why in stuck.items() if route not in reached}

    # control: A's own owner sends the very same requests - many must go through,
    # otherwise the bodies are junk and the refusals above prove nothing
    accepted = {route for route, method, url, query, payload in requests
                if client.request(method, url, headers=owner_headers, params=query, **payload).status_code < 300}
    print('\nbody sweep: sent', len(requests), '| reached', len(reached), '| owner accepted', len(accepted),
          '| stuck at 422:', '\n'.join(f'{r}: {w}' for r, w in sorted(stuck.items())) or 'none',
          '| own scope 2xx:', own_scope)
    # the sweep must really get past validation on most routes, not just bounce off 422
    assert len(reached) >= 60, (sorted(reached), sorted(stuck))
    assert len(accepted) >= 25, sorted(accepted)
