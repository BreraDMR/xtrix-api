"""Reserve each item's master and buffered interval, including legacy writers."""
from alembic import op
import sqlalchemy as sa

revision = '0077_visit_item_slots'
down_revision = '0076_gift_order_terms'
branch_labels = None
depends_on = None


def _slot(alias):
    return (f"tsrange({alias}.date_start - {alias}.buffer_before_minutes * interval '1 minute', "
            f"{alias}.date_end + {alias}.buffer_after_minutes * interval '1 minute', '[)')")


def _parent_slot(alias):
    return (f"tsrange({alias}.date_start, greatest({alias}.date_start, "
            f"coalesce({alias}.date_end, {alias}.date_start + interval '1 hour')), '[)')")


def _refuse(rows, message):
    if rows:
        raise RuntimeError(message + ': ' + '; '.join(str(tuple(row)) for row in rows))


def upgrade():
    conn = op.get_bind()
    op.execute('LOCK TABLE visits, visit_items IN ACCESS EXCLUSIVE MODE')
    invalid = conn.execute(sa.text('''
        SELECT id, visit_id FROM visit_items
        WHERE date_end <= date_start OR duration_minutes <= 0
           OR buffer_before_minutes < 0 OR buffer_after_minutes < 0 LIMIT 20
    ''')).fetchall()
    _refuse(invalid, 'Invalid visit item intervals (item, visit); correct them before retrying')
    op.add_column('visit_items', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('visits', sa.Column('reservation_revision', sa.BigInteger(), nullable=False, server_default='0'))
    # These are ordinary single items. ORM chain replacement can update them in place.
    op.execute('''
        INSERT INTO visit_items (visit_id, position, specialist_id, service_id, service_name,
            price, duration_minutes, date_start, date_end, buffer_before_minutes, buffer_after_minutes)
        SELECT v.id, 0, v.specialist_id, v.service_id, v.service_name, round(v.price::numeric, 2),
            greatest(1, ceil(extract(epoch FROM (
                CASE WHEN v.date_end > v.date_start THEN v.date_end
                     ELSE v.date_start + interval '1 hour' END - v.date_start)) / 60))::int,
            v.date_start, CASE WHEN v.date_end > v.date_start THEN v.date_end
                              ELSE v.date_start + interval '1 hour' END, 0, 0
        FROM visits v WHERE NOT EXISTS (SELECT 1 FROM visit_items i WHERE i.visit_id=v.id)
    ''')
    op.execute('''
        UPDATE visit_items i SET specialist_id=coalesce(i.specialist_id,v.specialist_id),
            active=(v.deleted_at IS NULL AND v.status IS DISTINCT FROM 'cancelled')
        FROM visits v WHERE i.visit_id=v.id
    ''')
    clashes = conn.execute(sa.text(f'''
        SELECT a.visit_id, a.id, b.visit_id, b.id, a.specialist_id,
               {_slot('a')}::text, {_slot('b')}::text
        FROM visit_items a JOIN visit_items b
          ON a.specialist_id=b.specialist_id AND a.visit_id<b.visit_id
         AND {_slot('a')} && {_slot('b')}
        WHERE a.active AND b.active ORDER BY a.visit_id,b.visit_id LIMIT 20
    ''')).fetchall()
    _refuse(clashes, 'Buffered visit conflicts (visit,item,visit,item,master,ranges); move or cancel before retrying')
    op.alter_column('visit_items', 'specialist_id', nullable=False)
    op.create_check_constraint('ck_visit_items_interval', 'visit_items',
        'date_end > date_start AND duration_minutes > 0 AND buffer_before_minutes >= 0 AND buffer_after_minutes >= 0')
    op.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    # Items of the same chain may share their own buffers. Other visits may not.
    op.execute('''
        ALTER TABLE visit_items ADD CONSTRAINT ex_visit_items_specialist_slot
        EXCLUDE USING gist (specialist_id WITH =, visit_id WITH <>,
            (tsrange(date_start-buffer_before_minutes*interval '1 minute',
                     date_end+buffer_after_minutes*interval '1 minute','[)')) WITH &&)
        WHERE (active) DEFERRABLE INITIALLY IMMEDIATE
    ''')
    op.execute('''
        CREATE FUNCTION visit_items_lock_parent() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE old_id integer; new_id integer; parent_id integer; v visits%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN old_id := OLD.visit_id; END IF;
            IF TG_OP <> 'DELETE' THEN new_id := NEW.visit_id; END IF;
            FOR parent_id IN SELECT DISTINCT id FROM (VALUES (old_id),(new_id)) ids(id)
                             WHERE id IS NOT NULL ORDER BY id LOOP
                -- A version write also serializes child changes under repeatable read.
                UPDATE visits SET reservation_revision=reservation_revision+1 WHERE id=parent_id;
            END LOOP;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            SELECT * INTO v FROM visits WHERE id=NEW.visit_id;
            NEW.active := FOUND AND v.deleted_at IS NULL AND v.status IS DISTINCT FROM 'cancelled';
            NEW.specialist_id := coalesce(NEW.specialist_id,v.specialist_id);
            PERFORM pg_advisory_xact_lock(hashtextextended('visit-slot:' || NEW.specialist_id::text,0));
            RETURN NEW;
        END $$;
        CREATE TRIGGER visit_items_parent_lock BEFORE INSERT OR UPDATE OR DELETE ON visit_items
            FOR EACH ROW EXECUTE FUNCTION visit_items_lock_parent();

        CREATE FUNCTION visit_items_ensure(parent_id integer) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v visits%ROWTYPE; finish timestamp;
        BEGIN
            UPDATE visits SET reservation_revision=reservation_revision+1 WHERE id=parent_id RETURNING * INTO v;
            IF NOT FOUND THEN RETURN; END IF;
            IF NOT EXISTS (SELECT 1 FROM visit_items WHERE visit_id=parent_id) THEN
                finish := CASE WHEN v.date_end>v.date_start THEN v.date_end
                               ELSE v.date_start+interval '1 hour' END;
                INSERT INTO visit_items (visit_id,position,specialist_id,service_id,service_name,price,
                    duration_minutes,date_start,date_end,buffer_before_minutes,buffer_after_minutes)
                VALUES (v.id,0,v.specialist_id,v.service_id,v.service_name,round(v.price::numeric,2),
                    greatest(1,ceil(extract(epoch FROM (finish-v.date_start))/60))::int,
                    v.date_start,finish,0,0);
            END IF;
        END $$;

        CREATE FUNCTION visit_items_parent_ensure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM visit_items_ensure(NEW.id); RETURN NEW; END $$;
        CREATE CONSTRAINT TRIGGER visits_ensure_items AFTER INSERT ON visits
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION visit_items_parent_ensure();

        CREATE FUNCTION visit_items_child_ensure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN PERFORM visit_items_ensure(OLD.visit_id); END IF;
            IF TG_OP <> 'DELETE' AND (TG_OP='INSERT' OR NEW.visit_id IS DISTINCT FROM OLD.visit_id) THEN
                PERFORM visit_items_ensure(NEW.visit_id);
            END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER visit_items_ensure_parent AFTER INSERT OR DELETE OR UPDATE OF visit_id ON visit_items
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION visit_items_child_ensure();

        CREATE FUNCTION visit_items_sync_active() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE visit_items SET active=(NEW.deleted_at IS NULL AND NEW.status IS DISTINCT FROM 'cancelled')
            WHERE visit_id=NEW.id AND active IS DISTINCT FROM
                (NEW.deleted_at IS NULL AND NEW.status IS DISTINCT FROM 'cancelled');
            RETURN NEW;
        END $$;
        CREATE TRIGGER visits_items_active AFTER UPDATE OF status,deleted_at ON visits
            FOR EACH ROW EXECUTE FUNCTION visit_items_sync_active();

        CREATE FUNCTION visit_items_sync_geometry() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE v visits%ROWTYPE; n integer; i visit_items%ROWTYPE; finish timestamp;
                starts timestamp; ends timestamp; first_master integer;
        BEGIN
            UPDATE visits SET reservation_revision=reservation_revision+1 WHERE id=NEW.id RETURNING * INTO v;
            IF NOT FOUND THEN RETURN NULL; END IF;
            PERFORM visit_items_ensure(v.id);
            SELECT count(*),min(date_start),max(date_end) INTO n,starts,ends FROM visit_items WHERE visit_id=v.id;
            IF n=1 THEN
                SELECT * INTO i FROM visit_items WHERE visit_id=v.id;
                finish := CASE WHEN v.date_end>v.date_start THEN v.date_end
                               ELSE v.date_start+i.duration_minutes*interval '1 minute' END;
                IF (i.date_start,i.date_end,i.specialist_id) IS DISTINCT FROM (v.date_start,finish,v.specialist_id) THEN
                    UPDATE visit_items SET date_start=v.date_start,date_end=finish,specialist_id=v.specialist_id,
                        duration_minutes=greatest(1,ceil(extract(epoch FROM (finish-v.date_start))/60))::int
                    WHERE id=i.id;
                END IF;
            ELSE
                SELECT specialist_id INTO first_master FROM visit_items WHERE visit_id=v.id ORDER BY position LIMIT 1;
                IF starts<>v.date_start OR (v.date_end IS NOT NULL AND ends<>v.date_end) OR first_master<>v.specialist_id THEN
                    RAISE EXCEPTION 'Update visit % and all of its chain items in the same transaction',v.id
                        USING ERRCODE='23514',CONSTRAINT='ck_visits_items_geometry';
                END IF;
            END IF;
            RETURN NULL;
        END $$;
        -- Run after ORM has finished replacing a single service with a chain.
        CREATE CONSTRAINT TRIGGER visits_items_geometry AFTER UPDATE OF date_start,date_end,specialist_id ON visits
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN ((OLD.date_start,OLD.date_end,OLD.specialist_id) IS DISTINCT FROM
                  (NEW.date_start,NEW.date_end,NEW.specialist_id))
            EXECUTE FUNCTION visit_items_sync_geometry();
    ''')
    op.execute('ALTER TABLE visits DROP CONSTRAINT ex_visits_specialist_slot')


def downgrade():
    op.execute('LOCK TABLE visits, visit_items IN ACCESS EXCLUSIVE MODE')
    clashes = op.get_bind().execute(sa.text(f'''
        SELECT a.id,b.id FROM visits a JOIN visits b ON a.specialist_id=b.specialist_id AND a.id<b.id
        AND {_parent_slot('a')} && {_parent_slot('b')}
        WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL
          AND a.status IS DISTINCT FROM 'cancelled' AND b.status IS DISTINCT FROM 'cancelled' LIMIT 20
    ''')).fetchall()
    _refuse(clashes, 'Old parent-slot constraint cannot represent these valid chains (visit pairs); move or cancel before downgrade')
    op.execute(f'''
        ALTER TABLE visits ADD CONSTRAINT ex_visits_specialist_slot EXCLUDE USING gist
        (specialist_id WITH =, ({_parent_slot('visits')}) WITH &&)
        WHERE (deleted_at IS NULL AND status IS DISTINCT FROM 'cancelled')
    ''')
    for name, table in [('visits_ensure_items','visits'),('visits_items_active','visits'),
                        ('visits_items_geometry','visits'),('visit_items_parent_lock','visit_items'),
                        ('visit_items_ensure_parent','visit_items')]:
        op.execute(f'DROP TRIGGER {name} ON {table}')
    for name in ['visit_items_lock_parent()', 'visit_items_parent_ensure()', 'visit_items_child_ensure()',
                 'visit_items_sync_active()', 'visit_items_sync_geometry()', 'visit_items_ensure(integer)']:
        op.execute(f'DROP FUNCTION {name}')
    op.drop_constraint('ex_visit_items_specialist_slot','visit_items')
    op.drop_constraint('ck_visit_items_interval','visit_items')
    op.alter_column('visit_items','specialist_id',nullable=True)
    op.drop_column('visit_items','active')
    op.drop_column('visits','reservation_revision')
