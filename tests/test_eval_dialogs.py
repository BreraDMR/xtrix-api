"""The dialog evals' own checks, without a model: the file loads, every key is real,
and the judge catches the outcomes it is there to catch."""
from datetime import date

import pytest

from evals.assistant import dialogs as D
from evals.assistant import fixture as F

TODAY = date(2026, 11, 13)


def test_dialog_file_loads_and_covers_every_locale_per_scenario():
    loaded = D.load_dialogs()
    assert len(loaded) >= 30
    by_name = {}
    for dialog in loaded:
        by_name.setdefault(dialog.id.split('-', 1)[1], set()).add(dialog.locale)
    assert all(locales == {'ru', 'cz', 'en'} for locales in by_name.values()), by_name


def test_bad_dialogs_are_refused(tmp_path):
    path = tmp_path / 'd.yaml'
    path.write_text('dialogs:\n- {id: ru-x, locale: ru, turns: ["hi"], expect: {created: [{client: nobody, service: haircut}]}}\n')
    with pytest.raises(D.DialogError):
        D.load_dialogs(path)
    path.write_text('dialogs:\n- {id: ru-x, locale: ru, turns: ["hi"], expect: {done: true}}\n')
    with pytest.raises(D.DialogError):
        D.load_dialogs(path)


@pytest.mark.parametrize('text,bad', [
    ('Запишу Анну на завтра в 12:00.', []),
    ('Kterou Terezu myslíte - Svobodovou, nebo Svobodnou?', []),
    ('client_not_found', ['internal code']),
    ('{"error": "x"}', ['raw data']),
    ('Traceback (most recent call last)', ['traceback']),
    ('Server answered HTTP 500', ['http error']),
])
def test_unreadable_text_is_spotted(text, bad):
    assert D.unreadable(text) == bad


def fx():
    return F.fake_fixture()


def visit(fx_, biz, client, service, day, hhmm, spec, alive=True, minutes=60):
    start = D._local(day, hhmm, TODAY)
    from datetime import timedelta
    return dict(biz=biz, client=fx_.ids[f'client.{client}'], services={fx_.ids[f'service.{service}']},
                start=start, end=start + timedelta(minutes=minutes), alive=alive, specialist=fx_.ids[f'spec.{spec}'])


def dialog(expect, confirm=True):
    return D.Dialog(id='ru-t', locale='ru', turns=['x'], expect=expect if isinstance(expect, list) else [expect],
                    confirm=confirm)


def test_judge_accepts_the_expected_visit_and_rejects_the_wrong_time():
    f = fx()
    before = {1: visit(f, 'a', 'anna_petrova', 'haircut', 1, '10:00', 'olga')}
    after = {**before, 2: visit(f, 'a', 'elena_kuznetsova', 'haircut', 1, '14:00', 'olga')}
    turns = [D.Turn('x', 200, summary='ok', actions=[{'action': 'create_visit'}])]
    want = {'created': [{'client': 'elena_kuznetsova', 'service': 'haircut', 'day': 1, 'time': '14:00'}]}
    reasons, _ = D.judge(dialog(want), turns, before, after, {}, {}, f, TODAY, executed=True)
    assert reasons == []
    want['created'][0]['time'] = '12:00'
    reasons, _ = D.judge(dialog(want), turns, before, after, {}, {}, f, TODAY, executed=True)
    assert reasons and 'no new visit' in reasons[0]


def test_judge_catches_double_booking_and_the_other_salon():
    f = fx()
    before = {1: visit(f, 'a', 'anna_petrova', 'haircut', 1, '10:00', 'olga'),
              9: visit(f, 'b', 'b_klara', 'b_massage', 1, '10:00', 'b_brigitte')}
    after = {**before, 2: visit(f, 'a', 'john_smith', 'haircut', 1, '10:30', 'olga'),
             9: visit(f, 'b', 'b_klara', 'b_massage', 1, '10:00', 'b_brigitte', alive=False)}
    turns = [D.Turn('x', 200, summary='ok', actions=[{'action': 'create_visit'}])]
    reasons, _ = D.judge(dialog({'created': [{'client': 'john_smith', 'service': 'haircut'}]}), turns,
                         before, after, {}, {}, f, TODAY, executed=True)
    assert any('double booking' in r for r in reasons)
    assert any('other salon' in r for r in reasons)


def test_judge_ask_and_nothing_proposed():
    f = fx()
    state = {1: visit(f, 'a', 'anna_petrova', 'haircut', 1, '10:00', 'olga')}
    asking = [D.Turn('x', 200, summary='', questions=['На какое время?'])]
    proposing = [D.Turn('x', 200, summary='Запишу', actions=[{'action': 'create_visit'}])]
    assert D.judge(dialog({'ask': True}, False), asking, state, state, {}, {}, f, TODAY, False)[0] == []
    assert D.judge(dialog({'ask': True}, False), proposing, state, state, {}, {}, f, TODAY, False)[0]
    never = {'nothing_proposed': True, 'unchanged': True}
    assert D.judge(dialog(never, False), asking, state, state, {}, {}, f, TODAY, False)[0] == []
    assert D.judge(dialog(never, False), proposing, state, state, {}, {}, f, TODAY, False)[0]


def test_judge_takes_either_shape_of_two_procedures():
    f = fx()
    both = visit(f, 'a', 'elena_kuznetsova', 'manicure', 1, '11:00', 'jana')
    both['services'] = {f.ids['service.manicure'], f.ids['service.pedicure']}
    split = [visit(f, 'a', 'elena_kuznetsova', 'manicure', 1, '11:00', 'jana'),
             visit(f, 'a', 'elena_kuznetsova', 'pedicure', 1, '12:00', 'jana')]
    expect = [
        {'created': [{'client': 'elena_kuznetsova', 'service': 'manicure', 'day': 1, 'time': '11:00'},
                     {'client': 'elena_kuznetsova', 'service': 'pedicure', 'day': 1, 'time': '12:00'}]},
        {'created': [{'client': 'elena_kuznetsova', 'service': ['manicure', 'pedicure'], 'day': 1, 'time': '11:00'}]},
    ]
    turns = [D.Turn('x', 200, summary='ok', actions=[{'action': 'create_visit'}])]
    for after in ({5: both}, {5: split[0], 6: split[1]}):
        assert D.judge(dialog(expect), turns, {}, after, {}, {}, f, TODAY, executed=True)[0] == []
    # only the manicure got booked
    assert D.judge(dialog(expect), turns, {}, {5: split[0]}, {}, {}, f, TODAY, executed=True)[0]
