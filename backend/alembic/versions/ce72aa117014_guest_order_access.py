"""One guest link per order and explicit permanent account ownership."""
from alembic import op
import sqlalchemy as sa

revision = 'ce72aa117014'
down_revision = 'b8c10d2e4f16'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    conflicting = connection.execute(sa.text('''
        SELECT o.id FROM orders o JOIN principals p ON p.id = o.principal_id
        WHERE p.account_id IS NOT NULL AND o.checkout_account_id IS NOT NULL
          AND p.account_id <> o.checkout_account_id LIMIT 1
    ''')).scalar()
    if conflicting:
        raise RuntimeError('订单归属与结算账号存在冲突，请核对订单 ' + conflicting)
    op.add_column('orders', sa.Column('owner_account_id', sa.String(64), nullable=True))
    op.create_foreign_key('fk_order_owner_account', 'orders', 'customer_accounts', ['owner_account_id'], ['id'])
    op.create_index('ix_orders_owner_account_id', 'orders', ['owner_account_id'])
    op.add_column('orders', sa.Column('guest_access_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('order_access_grants', sa.Column('encrypted_token', sa.Text(), nullable=True))
    connection.execute(sa.text('''
        UPDATE orders o SET owner_account_id = COALESCE(p.account_id, o.checkout_account_id)
        FROM principals p WHERE p.id = o.principal_id
    '''))
    # Keep one unrevoked root, preferring a root still valid after the shorter TTL.
    # Retain an expired unrevoked root when the latest state is expiry.
    connection.execute(sa.text('''
        WITH ranked AS (
            SELECT g.id, g.order_id,
                   ROW_NUMBER() OVER (PARTITION BY g.order_id ORDER BY
                       (g.revoked_at IS NULL AND LEAST(g.expires_at, g.created_at + INTERVAL '14 days') > CURRENT_TIMESTAMP) DESC,
                       g.created_at DESC, g.id DESC) AS rank
            FROM order_access_grants g
            JOIN orders o ON o.id = g.order_id
            WHERE o.owner_account_id IS NULL AND g.token_hash IS NOT NULL
              AND g.source_grant_id IS NULL AND g.account_id IS NULL
        )
        UPDATE order_access_grants g SET revoked_at = COALESCE(g.revoked_at, CURRENT_TIMESTAMP),
               encrypted_token = NULL, version = g.version + 1
        WHERE NOT EXISTS (SELECT 1 FROM ranked r WHERE r.id = g.id AND r.rank = 1)
    '''))
    connection.execute(sa.text('''
        UPDATE order_access_grants SET expires_at = LEAST(expires_at, created_at + INTERVAL '14 days'),
               scopes = '["ORDER_VIEW","FINAL_PDF_PREVIEW","DELIVERY_PROOF_VIEW"]'::jsonb
        WHERE revoked_at IS NULL
    '''))
    connection.execute(sa.text('''
        UPDATE auth_sessions s SET revoked_at = COALESCE(s.revoked_at, CURRENT_TIMESTAMP)
        FROM order_access_grants g WHERE s.grant_id = g.id
          AND (g.revoked_at IS NOT NULL OR g.expires_at <= CURRENT_TIMESTAMP)
    '''))
    connection.execute(sa.text('''
        UPDATE auth_sessions s SET expires_at = LEAST(s.expires_at, g.expires_at)
        FROM order_access_grants g WHERE s.grant_id = g.id
    '''))
    connection.execute(sa.text('''
        DELETE FROM idempotency_records
        WHERE route LIKE '%/access-links%' OR route LIKE '%/order-access/%'
           OR route LIKE '%/customer/account/link-guest%' OR route LIKE '%/customer/account/import-order%'
           OR route LIKE '%/orders%' OR route LIKE '%/after-sales%'
    '''))
    op.create_index('uq_order_guest_access_root', 'order_access_grants', ['order_id'], unique=True,
                    postgresql_where=sa.text('token_hash IS NOT NULL AND source_grant_id IS NULL AND revoked_at IS NULL'))


def downgrade():
    claimed = op.get_bind().execute(sa.text('''
        SELECT o.id FROM orders o JOIN principals p ON p.id = o.principal_id
        WHERE o.owner_account_id IS NOT NULL
          AND o.owner_account_id IS DISTINCT FROM p.account_id LIMIT 1
    ''')).scalar()
    if claimed:
        raise RuntimeError('订单已有独立账号归属，旧版本无法保留，请使用部署前备份恢复：' + claimed)
    # Credentials invalidated by the upgrade stay invalid during a downgrade.
    op.execute('UPDATE order_access_grants SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP), encrypted_token = NULL')
    op.execute("UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP) WHERE kind = 'ORDER_ACCESS'")
    op.drop_index('uq_order_guest_access_root', table_name='order_access_grants')
    op.drop_column('order_access_grants', 'encrypted_token')
    op.drop_column('orders', 'guest_access_version')
    op.drop_index('ix_orders_owner_account_id', table_name='orders')
    op.drop_constraint('fk_order_owner_account', 'orders', type_='foreignkey')
    op.drop_column('orders', 'owner_account_id')
