"""Relational business facts; JSON columns contain versioned, validated snapshots."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


J = JSON().with_variant(JSONB, 'postgresql')


class Entity:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Principal(Entity, Base):
    __tablename__ = 'principals'
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class Account(Entity, Base):
    __tablename__ = 'customer_accounts'
    principal_id: Mapped[str] = mapped_column(ForeignKey('principals.id'), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='PENDING_EMAIL')
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(120), default='')
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, default=lambda: uuid4().hex)
    preferences: Mapped[dict] = mapped_column(J, default=dict)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Staff(Entity, Base):
    __tablename__ = 'staff'
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'), unique=True, index=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discount_level: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, default='')
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32), default='')
    email: Mapped[str | None] = mapped_column(String(254))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default='PENDING_SETUP')
    capabilities: Mapped[list] = mapped_column(J, default=list)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    temporary_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("role IN ('ADMIN','COURIER')"),
        CheckConstraint('discount_level IS NULL OR (discount_level >= 1 AND discount_level <= 6)'),
        CheckConstraint("is_super_admin = false OR role = 'ADMIN'"),
        Index('uq_staff_single_super_admin', 'is_super_admin', unique=True,
              postgresql_where=text('is_super_admin = true'),
              sqlite_where=text('is_super_admin = 1')))


class AuthSession(Entity, Base):
    __tablename__ = 'auth_sessions'
    kind: Mapped[str] = mapped_column(String(24), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    principal_id: Mapped[str | None] = mapped_column(ForeignKey('principals.id'))
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'), index=True)
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'), index=True)
    grant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default=new_id)
    device_label: Mapped[str] = mapped_column(String(160), default='浏览器')
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OneTimeToken(Entity, Base):
    __tablename__ = 'one_time_tokens'
    kind: Mapped[str] = mapped_column(String(48))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    session_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(J, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessGrant(Entity, Base):
    __tablename__ = 'order_access_grants'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'), index=True)
    source_grant_id: Mapped[str | None] = mapped_column(ForeignKey('order_access_grants.id'))
    scopes: Mapped[list] = mapped_column(J, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64), default='')
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfigSection(Entity, Base):
    __tablename__ = 'config_sections'
    section: Mapped[str] = mapped_column(String(48), unique=True)
    data: Mapped[dict] = mapped_column(J, default=dict)


class Asset(Entity, Base):
    __tablename__ = 'assets'
    principal_id: Mapped[str | None] = mapped_column(ForeignKey('principals.id'), index=True)
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    assignment_id: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    pages: Mapped[int | None] = mapped_column(Integer)
    normalized_asset_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(J, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint('size >= 0'),)


class UploadSession(Entity, Base):
    __tablename__ = 'upload_sessions'
    principal_id: Mapped[str | None] = mapped_column(ForeignKey('principals.id'))
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    purpose: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    total_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='UPLOADING')
    data: Mapped[dict] = mapped_column(J, default=dict)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey('assets.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint('total_bytes > 0'),)


class Preparation(Entity, Base):
    __tablename__ = 'preparations'
    principal_id: Mapped[str] = mapped_column(ForeignKey('principals.id'), index=True)
    checkout_account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    source_grant_id: Mapped[str | None] = mapped_column(ForeignKey('order_access_grants.id', use_alter=True, name='fk_preparation_source_grant'))
    replaces_order_id: Mapped[str | None] = mapped_column(String(64))
    expected_order_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='QUEUED')
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    input: Mapped[dict] = mapped_column(J, default=dict)
    data: Mapped[dict] = mapped_column(J, default=dict)
    quote_id: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class Artifact(Entity, Base):
    __tablename__ = 'artifacts'
    preparation_id: Mapped[str] = mapped_column(ForeignKey('preparations.id'), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), unique=True)
    client_file_id: Mapped[str] = mapped_column(String(64))
    client_segment_id: Mapped[str | None] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(Integer)
    sheets: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(J, default=dict)
    page_map: Mapped[list] = mapped_column(J, default=list)
    __table_args__ = (CheckConstraint('pages > 0 AND sheets > 0'),)


class Quote(Entity, Base):
    __tablename__ = 'quotes'
    preparation_id: Mapped[str] = mapped_column(ForeignKey('preparations.id'), index=True)
    checkout_account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    price_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subtotal_cents: Mapped[int] = mapped_column(Integer)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer)
    selected_coupon_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('subtotal_cents >= 0 AND discount_cents >= 0 AND total_cents = subtotal_cents - discount_cents AND total_cents >= 0'),)


class Location(Entity, Base):
    __tablename__ = 'delivery_locations'
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(32), default='DORMITORY')
    x: Mapped[float] = mapped_column(default=0.5)
    y: Mapped[float] = mapped_column(default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    map_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint('x >= 0 AND x <= 1 AND y >= 0 AND y <= 1'),)


class DeliverySlot(Entity, Base):
    __tablename__ = 'delivery_slots'
    date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer)
    occupied: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('capacity > 0 AND occupied >= 0 AND occupied <= capacity'), CheckConstraint('ends_at > starts_at'),)


class Order(Entity, Base):
    __tablename__ = 'orders'
    principal_id: Mapped[str] = mapped_column(ForeignKey('principals.id'), index=True)
    checkout_account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    pickup_code: Mapped[str] = mapped_column(String(20), unique=True)
    status: Mapped[str] = mapped_column(String(32), default='PENDING_CONFIRMATION', index=True)
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    preparation_id: Mapped[str] = mapped_column(ForeignKey('preparations.id'))
    quote_id: Mapped[str] = mapped_column(ForeignKey('quotes.id'))
    total_cents: Mapped[int] = mapped_column(Integer)
    cash_received_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    settlement_kind: Mapped[str | None] = mapped_column(String(24))
    nickname: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    customer_note: Mapped[str] = mapped_column(String(120), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    location_id: Mapped[str] = mapped_column(ForeignKey('delivery_locations.id'))
    requested_slot_id: Mapped[str] = mapped_column(ForeignKey('delivery_slots.id'))
    confirmed_slot_id: Mapped[str | None] = mapped_column(ForeignKey('delivery_slots.id'))
    actual_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    address_review: Mapped[bool] = mapped_column(Boolean, default=False)
    packing_status: Mapped[str] = mapped_column(String(24), default='BLOCKED')
    close_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('total_cents >= 0 AND cash_received_cents >= 0 AND refunded_cents >= 0 AND refunded_cents <= cash_received_cents'),)


class OrderRevision(Entity, Base):
    __tablename__ = 'order_revisions'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    preparation_id: Mapped[str] = mapped_column(ForeignKey('preparations.id'))
    quote_id: Mapped[str] = mapped_column(ForeignKey('quotes.id'))
    snapshot: Mapped[dict] = mapped_column(J)
    __table_args__ = (UniqueConstraint('order_id', 'number'),)


class OrderFile(Entity, Base):
    __tablename__ = 'order_files'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    client_file_id: Mapped[str] = mapped_column(String(64))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'))
    kind: Mapped[str] = mapped_column(String(16))
    binding_type: Mapped[str] = mapped_column(String(16), default='NONE')
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (UniqueConstraint('order_id','revision','client_file_id'),)


class PrintTask(Entity, Base):
    __tablename__ = 'print_tasks'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey('order_files.id'))
    artifact_id: Mapped[str] = mapped_column(ForeignKey('artifacts.id'))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default='PENDING')
    settings: Mapped[dict] = mapped_column(J, default=dict)


class PrintAttempt(Entity, Base):
    __tablename__ = 'print_attempts'
    task_id: Mapped[str] = mapped_column(ForeignKey('print_tasks.id'), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey('artifacts.id'))
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    outcome: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text, default='')
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BindingTask(Entity, Base):
    __tablename__ = 'binding_tasks'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey('order_files.id'), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='WAITING_PRINT')
    data: Mapped[dict] = mapped_column(J, default=dict)


class PackingTask(Entity, Base):
    __tablename__ = 'packing_tasks'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='BLOCKED')
    manifest_revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(J, default=dict)


class Receipt(Entity, Base):
    __tablename__ = 'cash_receipts'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='UNMATCHED')
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents >= 0'),)


class Payment(Entity, Base):
    __tablename__ = 'payment_records'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), unique=True)
    receipt_id: Mapped[str | None] = mapped_column(ForeignKey('cash_receipts.id'))
    amount_cents: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents >= 0'),)


class ReceiptAdjustment(Entity, Base):
    __tablename__ = 'receipt_adjustments'
    receipt_id: Mapped[str] = mapped_column(ForeignKey('cash_receipts.id'), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    reason: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint('amount_cents > 0'),)


class DeliveryBatch(Entity, Base):
    __tablename__ = 'delivery_batches'
    name: Mapped[str] = mapped_column(String(120))
    courier_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default='PLANNED')
    reason: Mapped[str] = mapped_column(Text, default='')
    data: Mapped[dict] = mapped_column(J, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint('ends_at > starts_at'),)


class DeliveryAssignment(Entity, Base):
    __tablename__ = 'delivery_assignments'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('delivery_batches.id'), index=True)
    courier_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    status: Mapped[str] = mapped_column(String(24), default='PLANNED')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    address_review: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (Index('uq_active_order_assignment','order_id',unique=True,postgresql_where=text('active = true'),sqlite_where=text('active = 1')),)


class DeliveryProof(Entity, Base):
    __tablename__ = 'delivery_proofs'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'))
    assignment_id: Mapped[str] = mapped_column(ForeignKey('delivery_assignments.id'))
    photo_asset_id: Mapped[str | None] = mapped_column(ForeignKey('assets.id'))
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey('delivery_proofs.id'))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (Index('uq_current_delivery_proof','assignment_id',unique=True,postgresql_where=text('active = true'),sqlite_where=text('active = 1')),)


class BusinessException(Entity, Base):
    __tablename__ = 'business_exceptions'
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'), index=True)
    type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='OPEN')
    reason: Mapped[str] = mapped_column(Text)
    blocks_production: Mapped[bool] = mapped_column(Boolean, default=False)
    blocks_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(J, default=dict)


class BatchPlan(Entity, Base):
    __tablename__ = 'batch_plans'
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    data: Mapped[dict] = mapped_column(J)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AfterSale(Entity, Base):
    __tablename__ = 'after_sales'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    source: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(64))
    responsibility: Mapped[str] = mapped_column(String(24))
    solution: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default='REQUESTED')
    reason: Mapped[str] = mapped_column(Text)
    suggested_cents: Mapped[int] = mapped_column(Integer, default=0)
    approved_cents: Mapped[int | None] = mapped_column(Integer)
    adjustment_basis: Mapped[str] = mapped_column(Text, default='')
    data: Mapped[dict] = mapped_column(J, default=dict)


class Refund(Entity, Base):
    __tablename__ = 'refund_records'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    after_sale_id: Mapped[str] = mapped_column(ForeignKey('after_sales.id'), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    reason: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents >= 0'),)


class Campaign(Entity, Base):
    __tablename__ = 'promotion_campaigns'
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    budget_cents: Mapped[int | None] = mapped_column(Integer)
    reserved_liability_cents: Mapped[int] = mapped_column(Integer, default=0)
    realized_discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('reserved_liability_cents >= 0 AND realized_discount_cents >= 0'),CheckConstraint('budget_cents IS NULL OR reserved_liability_cents + realized_discount_cents <= budget_cents'),)


class Coupon(Entity, Base):
    __tablename__ = 'coupon_grants'
    account_id: Mapped[str] = mapped_column(ForeignKey('customer_accounts.id'), index=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey('promotion_campaigns.id'))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='AVAILABLE')
    risk_hold: Mapped[str] = mapped_column(String(24), default='NONE')
    reserved_order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'))
    used_order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'))
    source_key: Mapped[str] = mapped_column(String(160), unique=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents > 0'),)


class PromotionEvent(Entity, Base):
    __tablename__ = 'promotion_events'
    business_key: Mapped[str] = mapped_column(String(200), unique=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey('promotion_campaigns.id'))
    coupon_id: Mapped[str | None] = mapped_column(ForeignKey('coupon_grants.id'))
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'))
    kind: Mapped[str] = mapped_column(String(48))
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(J, default=dict)


class Referral(Entity, Base):
    __tablename__ = 'referrals'
    inviter_id: Mapped[str] = mapped_column(ForeignKey('customer_accounts.id'))
    invitee_id: Mapped[str] = mapped_column(ForeignKey('customer_accounts.id'), unique=True)
    qualified_order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'))
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default='WAITING_FIRST_ORDER')
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('inviter_id <> invitee_id'),)


class MailTemplate(Entity, Base):
    __tablename__ = 'mail_templates'
    key: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(String(300))
    text_body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(J, default=dict)


class EmailJob(Entity, Base):
    __tablename__ = 'email_outbox'
    business_key: Mapped[str] = mapped_column(String(200), unique=True)
    template_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='QUEUED')
    protected_payload: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(J, default=dict)


class ExportJob(Entity, Base):
    __tablename__ = 'export_jobs'
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'))
    account_id: Mapped[str | None] = mapped_column(ForeignKey('customer_accounts.id'))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default='QUEUED')
    storage_key: Mapped[str | None] = mapped_column(String(240))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(J, default=dict)


class TrashEntry(Entity, Base):
    __tablename__ = 'trash_entries'
    target_type: Mapped[str] = mapped_column(String(48))
    target_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='DELETED')
    purge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(J, default=dict)


class AuditLog(Entity, Base):
    __tablename__ = 'audit_logs'
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(48))
    target_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default='')
    request_id: Mapped[str] = mapped_column(String(64), default='')
    data: Mapped[dict] = mapped_column(J, default=dict)


class AnonymousLedger(Entity, Base):
    __tablename__ = 'anonymous_ledger'
    bucket: Mapped[str] = mapped_column(String(64), unique=True)
    totals: Mapped[dict] = mapped_column(J, default=dict)


class DeletionRequest(Entity, Base):
    __tablename__ = 'deletion_requests'
    account_id: Mapped[str] = mapped_column(ForeignKey('customer_accounts.id'))
    status: Mapped[str] = mapped_column(String(24), default='REQUESTED')
    data: Mapped[dict] = mapped_column(J, default=dict)


class IdempotencyRecord(Base):
    __tablename__ = 'idempotency_records'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    actor_key: Mapped[str] = mapped_column(String(200))
    route: Mapped[str] = mapped_column(String(300))
    key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    encrypted_response: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[dict] = mapped_column(J, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint('actor_key','route','key'),)


class Job(Entity, Base):
    __tablename__ = 'jobs'
    kind: Mapped[str] = mapped_column(String(48))
    business_key: Mapped[str] = mapped_column(String(200), unique=True)
    target_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(J, default=dict)


class WagePolicy(Entity, Base):
    __tablename__ = 'wage_policies'
    staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'), unique=True)
    scope_key: Mapped[str] = mapped_column(String(80), unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    fixed_cents: Mapped[int] = mapped_column(Integer, default=0)
    rate_bps: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint("mode IN ('FIXED','PERCENT')"), CheckConstraint('fixed_cents >= 0'), CheckConstraint('rate_bps >= 0 AND rate_bps <= 10000'))


class WageEntry(Entity, Base):
    __tablename__ = 'wage_entries'
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'), index=True)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey('delivery_assignments.id'))
    source_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(7), index=True)
    snapshot: Mapped[dict] = mapped_column(J, default=dict)
    reason: Mapped[str] = mapped_column(Text, default='')
    actor_id: Mapped[str] = mapped_column(String(64), default='')


class WageSettlement(Entity, Base):
    __tablename__ = 'wage_settlements'
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents >= 0'),)


class WageSettlementItem(Entity, Base):
    __tablename__ = 'wage_settlement_items'
    settlement_id: Mapped[str] = mapped_column(ForeignKey('wage_settlements.id'), index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey('wage_entries.id'), unique=True)


class SupplementaryCharge(Entity, Base):
    __tablename__ = 'supplementary_charges'
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    purpose: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    include_in_payroll: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default='ACTIVE')
    cash_received_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents > 0'), CheckConstraint('cash_received_cents >= 0 AND cash_received_cents <= amount_cents'), CheckConstraint('refunded_cents >= 0 AND refunded_cents <= cash_received_cents'), CheckConstraint("status IN ('ACTIVE','CANCELLED')"))


class SupplementaryMovement(Entity, Base):
    __tablename__ = 'supplementary_movements'
    charge_id: Mapped[str] = mapped_column(ForeignKey('supplementary_charges.id'), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (CheckConstraint('amount_cents > 0'), CheckConstraint("kind IN ('RECEIPT','REFUND')"))


class SupplementaryDispute(Entity, Base):
    __tablename__ = 'supplementary_disputes'
    charge_id: Mapped[str] = mapped_column(ForeignKey('supplementary_charges.id'), index=True)
    principal_id: Mapped[str | None] = mapped_column(ForeignKey('principals.id'))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='OPEN')
    resolution: Mapped[str] = mapped_column(Text, default='')
    actor_id: Mapped[str] = mapped_column(String(64), default='')
