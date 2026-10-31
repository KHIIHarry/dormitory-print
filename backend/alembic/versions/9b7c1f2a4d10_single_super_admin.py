"""enforce a single superadmin identity

Revision ID: 9b7c1f2a4d10
Revises: 1e66446aad7c
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = '9b7c1f2a4d10'
down_revision = '1e66446aad7c'
branch_labels = None
depends_on = None


def upgrade():
    duplicate_count = op.get_bind().execute(sa.text(
        'SELECT count(*) FROM staff WHERE is_super_admin = true')).scalar_one()
    if duplicate_count > 1:
        raise RuntimeError(
            'Cannot enforce a single superadmin: multiple staff rows have '
            'is_super_admin = true. Explicitly demote the unintended rows '
            'before running this migration; no automatic promotion or '
            'demotion is performed.')
    op.create_index(
        'uq_staff_single_super_admin', 'staff', ['is_super_admin'], unique=True,
        postgresql_where=sa.text('is_super_admin = true'))


def downgrade():
    op.drop_index('uq_staff_single_super_admin', table_name='staff')
