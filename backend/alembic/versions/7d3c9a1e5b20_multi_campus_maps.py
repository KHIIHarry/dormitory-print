"""multi-campus maps and per-campus point selection

Revision ID: 7d3c9a1e5b20
Revises: 9b7c1f2a4d10
Create Date: 2026-11-01
"""
from datetime import datetime
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision: str = '7d3c9a1e5b20'
down_revision: Union[str, Sequence[str], None] = '9b7c1f2a4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _published_at(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


def upgrade() -> None:
    op.create_table(
        'campuses',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('theme_color', sa.String(7), nullable=False),
        sa.Column('selection_enabled', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('map_asset_id', sa.String(64), nullable=True),
        sa.Column('map_url', sa.String(500), nullable=False),
        sa.Column('map_version', sa.Integer(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['map_asset_id'], ['assets.id'],
                                name='fk_campuses_map_asset_id_assets'),
        sa.UniqueConstraint('name', name='uq_campuses_name'),
        sa.CheckConstraint("theme_color ~ '^#[0-9A-Fa-f]{6}$'",
                           name='ck_campuses_theme_color'),
    )
    op.add_column('delivery_locations',
                  sa.Column('campus_id', sa.String(64), nullable=True))
    op.create_foreign_key('fk_delivery_locations_campus_id_campuses',
                          'delivery_locations', 'campuses', ['campus_id'], ['id'])
    op.create_index('ix_delivery_locations_campus_id', 'delivery_locations',
                    ['campus_id'])

    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT data FROM config_sections WHERE section = 'map'")).scalar_one_or_none()
    locations = bind.execute(sa.text(
        'SELECT count(*) FROM delivery_locations')).scalar_one()
    if row is None and locations == 0:
        # Fresh install: the service lazily creates the first campus on the
        # first public/admin map request, so there is nothing to backfill.
        return

    data = row or {}
    campus_id = uuid4().hex
    asset_id = data.get('assetId')
    if asset_id and not bind.execute(sa.text(
            'SELECT 1 FROM assets WHERE id = :id'), {'id': asset_id}).scalar():
        asset_id = None
    bind.execute(sa.text(
        """
        INSERT INTO campuses
            (id, version, created_at, updated_at, name, theme_color,
             selection_enabled, sort_order, map_asset_id, map_url,
             map_version, published_at)
        VALUES
            (:id, 1, now(), now(), '默认校区', '#C67C4E',
             true, 0, :asset_id, :map_url, :map_version, :published_at)
        """
    ), {
        'id': campus_id,
        'asset_id': asset_id,
        'map_url': data.get('mapUrl') or '',
        'map_version': int(data.get('mapVersion') or 1),
        'published_at': _published_at(data.get('publishedAt')),
    })
    bind.execute(sa.text(
        'UPDATE delivery_locations SET campus_id = :id WHERE campus_id IS NULL'),
        {'id': campus_id})


def downgrade() -> None:
    op.drop_index('ix_delivery_locations_campus_id', table_name='delivery_locations')
    op.drop_constraint('fk_delivery_locations_campus_id_campuses',
                       'delivery_locations', type_='foreignkey')
    op.drop_column('delivery_locations', 'campus_id')
    op.drop_table('campuses')
