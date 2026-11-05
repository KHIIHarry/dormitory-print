"""Keep delivery fees in the immutable quote total.

Revision ID: a6c8e0f2b014
Revises: f4a6b8c0d012
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6c8e0f2b014'
down_revision = 'f4a6b8c0d012'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('quotes', sa.Column('delivery_fee_cents', sa.Integer(), nullable=False, server_default='0'))
    op.drop_constraint('quotes_check', 'quotes', type_='check')
    op.create_check_constraint('ck_quotes_totals', 'quotes',
        'subtotal_cents >= 0 AND discount_cents >= 0 AND delivery_fee_cents >= 0 AND '
        'total_cents = subtotal_cents - discount_cents + delivery_fee_cents AND total_cents >= 0')


def downgrade():
    # Charged delivery quotes must not be rewritten to fit the old formula.
    bind = op.get_bind()
    if bind.scalar(sa.text('SELECT count(*) FROM quotes WHERE delivery_fee_cents <> 0')):
        raise RuntimeError('Cannot downgrade while quotes contain paid delivery fees')
    op.drop_constraint('ck_quotes_totals', 'quotes', type_='check')
    op.create_check_constraint('quotes_check', 'quotes',
        'subtotal_cents >= 0 AND discount_cents >= 0 AND '
        'total_cents = subtotal_cents - discount_cents AND total_cents >= 0')
    op.drop_column('quotes', 'delivery_fee_cents')
