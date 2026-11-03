"""Explicit campus identity for delivery slots without guessing shared history.

Revision ID: e3f5a7b9c011
Revises: d2e4f6a8b010
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3f5a7b9c011'
down_revision = 'd2e4f6a8b010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('delivery_slots', sa.Column('campus_id', sa.String(64), nullable=True))
    op.create_foreign_key('fk_delivery_slots_campus_id_campuses', 'delivery_slots', 'campuses',
                          ['campus_id'], ['id'])
    op.create_index('ix_delivery_slots_campus_id', 'delivery_slots', ['campus_id'])
    # Consider all references, including finished orders and trash. A single
    # remaining active assignment is insufficient evidence of historic scope.
    # Unknown addresses count as ambiguity rather than disappearing from COUNT.
    op.execute(sa.text("""
        WITH scope_refs AS (
            SELECT requested_slot_id AS slot_id, l.campus_id
              FROM orders o LEFT JOIN delivery_locations l ON l.id = o.location_id
            UNION ALL
            SELECT confirmed_slot_id, l.campus_id
              FROM orders o LEFT JOIN delivery_locations l ON l.id = o.location_id
             WHERE confirmed_slot_id IS NOT NULL
            UNION ALL SELECT slot_id, campus_id FROM delivery_groups
            UNION ALL
            SELECT COALESCE(b.slot_id, b.data->>'slotId', b.data->>'slot_id', b.data->'slotSnapshot'->>'id'),
                   COALESCE(b.campus_id, (
                       SELECT CASE WHEN COUNT(*) > 0 AND COUNT(l.campus_id) = COUNT(*)
                                        AND COUNT(DISTINCT l.campus_id) = 1 THEN MIN(l.campus_id) END
                         FROM delivery_assignments a JOIN orders o ON o.id = a.order_id
                         LEFT JOIN delivery_locations l ON l.id = o.location_id
                        WHERE a.batch_id = b.id))
              FROM delivery_batches b
            UNION ALL
            SELECT COALESCE(b.slot_id, b.data->>'slotId', b.data->>'slot_id', b.data->'slotSnapshot'->>'id'), l.campus_id
              FROM delivery_batches b
              JOIN delivery_assignments a ON a.batch_id = b.id
              JOIN orders o ON o.id = a.order_id
              LEFT JOIN delivery_locations l ON l.id = o.location_id
            UNION ALL
            SELECT o.data->'slotProposal'->>'slotId', l.campus_id
              FROM orders o LEFT JOIN delivery_locations l ON l.id = o.location_id
             WHERE o.data->'slotProposal'->>'slotId' IS NOT NULL
            UNION ALL
            SELECT c.slot_id, l.campus_id
              FROM delivery_changes c JOIN orders o ON o.id = c.order_id
              LEFT JOIN delivery_locations l ON l.id = o.location_id
             WHERE c.slot_id IS NOT NULL
            UNION ALL
            SELECT COALESCE(c.original->>'slotId', c.original->>'id'), c.original->>'campusId'
              FROM delivery_changes c
             WHERE COALESCE(c.original->>'slotId', c.original->>'id') IS NOT NULL
            UNION ALL
            SELECT COALESCE(c.proposed->>'slotId', c.proposed->>'id'), c.proposed->>'campusId'
              FROM delivery_changes c
             WHERE COALESCE(c.proposed->>'slotId', c.proposed->>'id') IS NOT NULL
        ), identities AS (
            SELECT refs.slot_id, MIN(refs.campus_id) AS campus_id,
                   COUNT(DISTINCT refs.campus_id) AS campus_count,
                   BOOL_OR(refs.campus_id IS NULL OR campus.id IS NULL) AS unknown
              FROM scope_refs refs LEFT JOIN campuses campus ON campus.id = refs.campus_id
             WHERE refs.slot_id IS NOT NULL GROUP BY refs.slot_id
        )
        UPDATE delivery_slots s
           SET campus_id = CASE WHEN i.campus_count = 1 AND NOT i.unknown THEN i.campus_id ELSE NULL END,
               data = COALESCE(s.data, '{}'::jsonb) || jsonb_build_object(
                   'campusScopeStatus', CASE WHEN i.campus_count = 1 AND NOT i.unknown THEN 'SCOPED'
                                            WHEN i.campus_count > 1 THEN 'SHARED_LEGACY'
                                            ELSE 'UNRESOLVED' END),
               version = s.version + 1
          FROM identities i WHERE i.slot_id = s.id
    """))
    op.execute(sa.text("""
        UPDATE delivery_slots SET data = COALESCE(data, '{}'::jsonb) ||
               jsonb_build_object('campusScopeStatus', 'UNRESOLVED'), version = version + 1
         WHERE campus_id IS NULL AND NOT (COALESCE(data, '{}'::jsonb) ? 'campusScopeStatus')
    """))
    # Reviews made against a global slot must never commit after scope changed.
    op.execute(sa.text("""
        UPDATE batch_plans SET expires_at = LEAST(expires_at, NOW())
         WHERE committed_at IS NULL
    """))


def downgrade() -> None:
    op.drop_index('ix_delivery_slots_campus_id', table_name='delivery_slots')
    op.drop_constraint('fk_delivery_slots_campus_id_campuses', 'delivery_slots', type_='foreignkey')
    op.drop_column('delivery_slots', 'campus_id')
