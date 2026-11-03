"""Recoverable after-sale deletion while retaining independent refunds.

Revision ID: d2e4f6a8b010
Revises: b1f2c3d4e5a6
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2e4f6a8b010'
down_revision = 'b1f2c3d4e5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('after_sales', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('refund_records', 'after_sale_id', existing_type=sa.String(64), nullable=True)
    # Honor recoverable entries created before the UI/visibility chain existed.
    op.execute(sa.text("""
        UPDATE after_sales AS sale
           SET deleted_at = entry.created_at, version = sale.version + 1
          FROM trash_entries AS entry
         WHERE entry.target_type = 'AFTER_SALE'
           AND entry.target_id = sale.id
           AND entry.status IN ('DELETED', 'PURGE_PENDING')
           AND sale.deleted_at IS NULL
    """))


def downgrade() -> None:
    # PostgreSQL refuses a downgrade if purged cases left detached refunds.
    # Preserve those real financial records rather than deleting them to fit.
    op.alter_column('refund_records', 'after_sale_id', existing_type=sa.String(64), nullable=False)
    op.drop_column('after_sales', 'deleted_at')
