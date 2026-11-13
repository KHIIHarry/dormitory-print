"""Allow a separate internal print task for each order."""

from alembic import op
import sqlalchemy as sa


revision = 'b8c10d2e4f16'
down_revision = 'a6c8e0f2b014'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('print_tasks', 'file_id', existing_type=sa.String(64), nullable=True)
    op.create_index('uq_print_task_identification_order', 'print_tasks', ['order_id'], unique=True,
                    postgresql_where=sa.text('file_id IS NULL'))


def downgrade():
    op.execute('DELETE FROM print_attempts WHERE task_id IN (SELECT id FROM print_tasks WHERE file_id IS NULL)')
    op.execute('DELETE FROM print_tasks WHERE file_id IS NULL')
    # Detach private outputs before old versions read the preparation.
    # Keep expired asset rows so the existing retention worker can remove the blobs.
    op.execute("DELETE FROM artifacts USING assets WHERE artifacts.asset_id = assets.id "
               "AND assets.purpose = 'PRINT_IDENTIFICATION'")
    op.execute("UPDATE assets SET expires_at = CURRENT_TIMESTAMP WHERE purpose = 'PRINT_IDENTIFICATION'")
    op.execute("DELETE FROM jobs WHERE kind = 'ORDER_IDENTIFICATION'")
    op.drop_index('uq_print_task_identification_order', table_name='print_tasks')
    op.alter_column('print_tasks', 'file_id', existing_type=sa.String(64), nullable=False)
