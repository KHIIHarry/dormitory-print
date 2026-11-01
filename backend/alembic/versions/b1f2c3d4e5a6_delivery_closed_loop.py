"""delivery closed loop: acceptance windows, collection groups, arrangement events

Revision ID: b1f2c3d4e5a6
Revises: 7d3c9a1e5b20
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b1f2c3d4e5a6'
down_revision: Union[str, Sequence[str], None] = '7d3c9a1e5b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    # Slot acceptance windows and the orthogonal early-seal facts.  Nullable on
    # purpose: legacy rows remain readable and are flagged "待补接单规则" by the
    # API instead of being silently rewritten during the migration.
    op.add_column('delivery_slots', sa.Column('open_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('delivery_slots', sa.Column('cutoff_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('delivery_slots', sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('delivery_slots', sa.Column('sealed_by', sa.String(64), nullable=True))
    op.add_column('delivery_slots', sa.Column('seal_reason', sa.String(300), nullable=True))
    op.create_check_constraint(
        'ck_delivery_slots_open_before_cutoff', 'delivery_slots',
        'open_at IS NULL OR cutoff_at IS NULL OR open_at < cutoff_at')

    op.add_column('orders', sa.Column('arrangement_version', sa.Integer(),
                                      nullable=False, server_default=sa.text('0')))
    op.add_column('orders', sa.Column('arrangement_updated_at',
                                      sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'delivery_groups',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('slot_id', sa.String(64), nullable=False),
        sa.Column('campus_id', sa.String(64), nullable=False),
        sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sealed_by', sa.String(64), nullable=True),
        sa.Column('seal_reason', sa.String(300), nullable=True),
        sa.Column('data', JSON, nullable=False),
        sa.ForeignKeyConstraint(['slot_id'], ['delivery_slots.id'],
                                name='fk_delivery_groups_slot_id_delivery_slots'),
        sa.ForeignKeyConstraint(['campus_id'], ['campuses.id'],
                                name='fk_delivery_groups_campus_id_campuses'),
        sa.UniqueConstraint('slot_id', 'campus_id', name='uq_delivery_group_slot_campus'),
    )
    op.create_index('ix_delivery_groups_slot_id', 'delivery_groups', ['slot_id'])
    op.create_index('ix_delivery_groups_campus_id', 'delivery_groups', ['campus_id'])

    op.add_column('delivery_batches', sa.Column('slot_id', sa.String(64), nullable=True))
    op.add_column('delivery_batches', sa.Column('campus_id', sa.String(64), nullable=True))
    op.add_column('delivery_batches', sa.Column('group_id', sa.String(64), nullable=True))
    op.add_column('delivery_batches', sa.Column('source', sa.String(20), nullable=False,
                                                 server_default=sa.text("'MANUAL'")))
    op.add_column('delivery_batches', sa.Column('is_regular', sa.Boolean(), nullable=False,
                                                 server_default=sa.text('false')))
    op.add_column('delivery_batches', sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('delivery_batches', sa.Column('sealed_by', sa.String(64), nullable=True))
    op.add_column('delivery_batches', sa.Column('seal_reason', sa.String(300), nullable=True))
    op.create_foreign_key('fk_delivery_batches_slot_id_delivery_slots',
                          'delivery_batches', 'delivery_slots', ['slot_id'], ['id'])
    op.create_foreign_key('fk_delivery_batches_campus_id_campuses',
                          'delivery_batches', 'campuses', ['campus_id'], ['id'])
    op.create_foreign_key('fk_delivery_batches_group_id_delivery_groups',
                          'delivery_batches', 'delivery_groups', ['group_id'], ['id'])
    op.create_index('ix_delivery_batches_slot_id', 'delivery_batches', ['slot_id'])
    op.create_index('ix_delivery_batches_campus_id', 'delivery_batches', ['campus_id'])
    op.create_index('ix_delivery_batches_group_id', 'delivery_batches', ['group_id'])
    op.create_index('uq_regular_batch_per_group', 'delivery_batches', ['group_id'],
                    unique=True, postgresql_where=sa.text(
                        'is_regular = true AND deleted_at IS NULL'))

    op.create_table(
        'delivery_changes',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('order_id', sa.String(64), nullable=False),
        sa.Column('batch_id', sa.String(64), nullable=True),
        sa.Column('slot_id', sa.String(64), nullable=True),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default=sa.text("'APPLIED'")),
        sa.Column('confirmation_required', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('original', JSON, nullable=False),
        sa.Column('proposed', JSON, nullable=False),
        sa.Column('reason', sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column('internal_note', sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column('actor_id', sa.String(64), nullable=True),
        sa.Column('plan_id', sa.String(64), nullable=True),
        sa.Column('data', JSON, nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'],
                                name='fk_delivery_changes_order_id_orders'),
        sa.ForeignKeyConstraint(['batch_id'], ['delivery_batches.id'],
                                name='fk_delivery_changes_batch_id_delivery_batches'),
        sa.ForeignKeyConstraint(['slot_id'], ['delivery_slots.id'],
                                name='fk_delivery_changes_slot_id_delivery_slots'),
    )
    op.create_index('ix_delivery_changes_order_id', 'delivery_changes', ['order_id'])
    op.create_index('ix_delivery_changes_batch_id', 'delivery_changes', ['batch_id'])
    op.create_index('ix_delivery_changes_slot_id', 'delivery_changes', ['slot_id'])
    op.create_index('ix_delivery_changes_plan_id', 'delivery_changes', ['plan_id'])
    op.create_index('ix_delivery_changes_order_created', 'delivery_changes',
                    ['order_id', 'created_at'])

    op.create_table(
        'delivery_notices',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('order_id', sa.String(64), nullable=False),
        sa.Column('change_id', sa.String(64), nullable=True),
        sa.Column('kind', sa.String(40), nullable=False,
                  server_default=sa.text("'DELIVERY_UPDATED'")),
        sa.Column('recipient', sa.String(320), nullable=True),
        sa.Column('status', sa.String(24), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column('dedupe_key', sa.String(200), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_error', sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column('email_job_id', sa.String(64), nullable=True),
        sa.Column('data', JSON, nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'],
                                name='fk_delivery_notices_order_id_orders'),
        sa.ForeignKeyConstraint(['change_id'], ['delivery_changes.id'],
                                name='fk_delivery_notices_change_id_delivery_changes'),
        sa.UniqueConstraint('dedupe_key', name='uq_delivery_notices_dedupe_key'),
    )
    op.create_index('ix_delivery_notices_order_id', 'delivery_notices', ['order_id'])
    op.create_index('ix_delivery_notices_change_id', 'delivery_notices', ['change_id'])

    # Traceability only: legacy batches that already carried a slot in JSON keep
    # that link in an explicit column.  No groups, reassignment, merge or mail
    # are created here; the repair preview handles those explicitly.
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text("""
            UPDATE delivery_batches b
               SET slot_id = b.data->>'slotId'
             WHERE b.slot_id IS NULL
               AND b.data ? 'slotId'
               AND EXISTS (SELECT 1 FROM delivery_slots s
                            WHERE s.id = b.data->>'slotId')
        """))


def downgrade() -> None:
    op.drop_index('ix_delivery_notices_change_id', table_name='delivery_notices')
    op.drop_index('ix_delivery_notices_order_id', table_name='delivery_notices')
    op.drop_table('delivery_notices')
    op.drop_index('ix_delivery_changes_order_created', table_name='delivery_changes')
    op.drop_index('ix_delivery_changes_plan_id', table_name='delivery_changes')
    op.drop_index('ix_delivery_changes_slot_id', table_name='delivery_changes')
    op.drop_index('ix_delivery_changes_batch_id', table_name='delivery_changes')
    op.drop_index('ix_delivery_changes_order_id', table_name='delivery_changes')
    op.drop_table('delivery_changes')
    op.drop_index('uq_regular_batch_per_group', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_group_id', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_campus_id', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_slot_id', table_name='delivery_batches')
    op.drop_constraint('fk_delivery_batches_group_id_delivery_groups',
                       'delivery_batches', type_='foreignkey')
    op.drop_constraint('fk_delivery_batches_campus_id_campuses',
                       'delivery_batches', type_='foreignkey')
    op.drop_constraint('fk_delivery_batches_slot_id_delivery_slots',
                       'delivery_batches', type_='foreignkey')
    for column in ('seal_reason', 'sealed_by', 'sealed_at', 'is_regular',
                   'source', 'group_id', 'campus_id', 'slot_id'):
        op.drop_column('delivery_batches', column)
    op.drop_index('ix_delivery_groups_campus_id', table_name='delivery_groups')
    op.drop_index('ix_delivery_groups_slot_id', table_name='delivery_groups')
    op.drop_table('delivery_groups')
    op.drop_column('orders', 'arrangement_updated_at')
    op.drop_column('orders', 'arrangement_version')
    op.drop_constraint('ck_delivery_slots_open_before_cutoff',
                       'delivery_slots', type_='check')
    for column in ('seal_reason', 'sealed_by', 'sealed_at', 'cutoff_at', 'open_at'):
        op.drop_column('delivery_slots', column)
