"""Actual procurement and other cash expenses, independently retained.

Revision ID: f4a6b8c0d012
Revises: e3f5a7b9c011
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4a6b8c0d012'
down_revision = 'e3f5a7b9c011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('operating_expenses',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('category', sa.String(16), nullable=False),
        sa.Column('spent_on', sa.Date(), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('paper_sheets', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('recorded_by', sa.String(64), nullable=False),
        sa.Column('void_reason', sa.Text(), nullable=False),
        sa.Column('voided_by', sa.String(64), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("category IN ('PAPER','INK','OTHER')", name='ck_expense_category'),
        sa.CheckConstraint('amount_cents > 0', name='ck_expense_amount'),
        sa.CheckConstraint("(category = 'PAPER' AND paper_sheets IS NOT NULL AND paper_sheets > 0) OR (category != 'PAPER' AND paper_sheets IS NULL)", name='ck_expense_sheets'),
        sa.CheckConstraint("status IN ('ACTIVE','VOIDED')", name='ck_expense_status'),
        sa.CheckConstraint("(status = 'ACTIVE' AND voided_at IS NULL AND voided_by IS NULL) OR (status = 'VOIDED' AND voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(trim(void_reason)) > 0)", name='ck_expense_void'))
    op.create_index('ix_operating_expenses_spent_on', 'operating_expenses', ['spent_on'])


def downgrade():
    op.drop_index('ix_operating_expenses_spent_on', table_name='operating_expenses')
    op.drop_table('operating_expenses')
