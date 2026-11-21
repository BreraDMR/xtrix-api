"""Several phones and emails per client: up to 5 phones and 3 emails, one primary of each.

clients.contact_phone / contact_email stay and keep holding the primary values. Existing values
are copied over as the primary contacts.
"""
from alembic import op
import sqlalchemy as sa

revision = '0095_contacts_client_contacts'
down_revision = '0094_visitor_push_tokens'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('client_contacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('value', sa.String(255), nullable=False),
        sa.Column('label', sa.String(50), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('notify', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.UniqueConstraint('client_id', 'kind', 'value', name='uq_client_contacts_value'),
        sa.CheckConstraint("kind IN ('phone','email')", name='ck_client_contacts_kind'),
        sa.CheckConstraint('length(value) > 0', name='ck_client_contacts_value'))
    op.create_index('ix_client_contacts_client_id', 'client_contacts', ['client_id'])
    op.create_index('ix_client_contacts_lookup', 'client_contacts', ['kind', 'value'])
    op.create_index('uq_client_contacts_primary', 'client_contacts', ['client_id', 'kind'], unique=True,
                    postgresql_where=sa.text('is_primary'))

    # the app checks the limits too, this is the backstop. Locking the client row makes two
    # parallel inserts for the same card wait for each other instead of both slipping through.
    op.execute("""
        CREATE FUNCTION client_contacts_limit() RETURNS trigger AS $$
        DECLARE
            taken integer;
            allowed integer := CASE NEW.kind WHEN 'phone' THEN 5 ELSE 3 END;
        BEGIN
            PERFORM 1 FROM clients WHERE id = NEW.client_id FOR UPDATE;
            SELECT count(*) INTO taken FROM client_contacts
             WHERE client_id = NEW.client_id AND kind = NEW.kind AND id <> NEW.id;
            IF taken >= allowed THEN
                RAISE EXCEPTION 'client % already has % % contacts', NEW.client_id, allowed, NEW.kind
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_client_contacts_limit';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER client_contacts_limit BEFORE INSERT OR UPDATE OF client_id, kind ON client_contacts
        FOR EACH ROW EXECUTE FUNCTION client_contacts_limit()
    """)

    # phones become +digits when they were written in international form; anything else keeps
    # its digits so lookups still find it
    op.execute("""
        INSERT INTO client_contacts (client_id, kind, value, is_primary, notify, created_at)
        SELECT id, 'phone',
               CASE WHEN left(btrim(contact_phone), 1) = '+'
                    THEN '+' || regexp_replace(contact_phone, '[^0-9]', '', 'g')
                    ELSE regexp_replace(contact_phone, '[^0-9]', '', 'g') END,
               true, true, COALESCE(created_at, timezone('utc', now()))
          FROM clients
         WHERE regexp_replace(COALESCE(contact_phone, ''), '[^0-9]', '', 'g') <> ''
    """)
    op.execute("""
        INSERT INTO client_contacts (client_id, kind, value, is_primary, notify, created_at)
        SELECT id, 'email', lower(btrim(contact_email)), true, true, COALESCE(created_at, timezone('utc', now()))
          FROM clients
         WHERE btrim(COALESCE(contact_email, '')) <> ''
    """)


def downgrade():
    # the extra numbers are lost; the primary ones are still on clients
    op.execute('DROP TRIGGER IF EXISTS client_contacts_limit ON client_contacts')
    op.execute('DROP FUNCTION IF EXISTS client_contacts_limit()')
    op.drop_index('uq_client_contacts_primary', table_name='client_contacts')
    op.drop_index('ix_client_contacts_lookup', table_name='client_contacts')
    op.drop_index('ix_client_contacts_client_id', table_name='client_contacts')
    op.drop_table('client_contacts')
