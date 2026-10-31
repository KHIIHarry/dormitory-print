"""staff grade and superadmin constraints

Revision ID: 1e66446aad7c
Revises: c779008eb12e
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '1e66446aad7c'
down_revision: Union[str, Sequence[str], None] = 'c779008eb12e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint('ck_staff_discount_level', 'staff', 'discount_level IS NULL OR (discount_level >= 1 AND discount_level <= 6)')
    op.create_check_constraint('ck_staff_superadmin_role', 'staff', "is_super_admin = false OR role = 'ADMIN'")


def downgrade() -> None:
    op.drop_constraint('ck_staff_superadmin_role', 'staff', type_='check')
    op.drop_constraint('ck_staff_discount_level', 'staff', type_='check')
