"""Store independent read receipts for admin notifications."""
from alembic import op
import sqlalchemy as sa

revision = 'ae84f1d9b203'
down_revision = 'ce72aa117014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_audit_logs_notification_lookup', 'audit_logs',
        ['target_type', 'action', 'target_id', 'created_at', 'id'],
    )
    op.create_table(
        'admin_notification_reads',
        sa.Column('staff_id', sa.String(64), sa.ForeignKey('staff.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('notification_id', sa.String(160), primary_key=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('admin_notification_reads')
    op.drop_index('ix_audit_logs_notification_lookup', table_name='audit_logs')
