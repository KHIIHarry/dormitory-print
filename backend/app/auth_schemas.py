"""Strict DTOs for the identity, account, staff and order-access module."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .core import DTO

STAFF_ROLES = ('ADMIN', 'COURIER')
STAFF_STATUSES = ('PENDING_SETUP', 'ACTIVE', 'DISABLED')
LINK_SCOPES = (
    'ORDER_VIEW',
    'FINAL_PDF_PREVIEW',
    'DELIVERY_PROOF_VIEW',
    'ORDER_REVISE_PENDING',
    'ORDER_CANCEL_PENDING',
    'ORDER_CHANGE_LOCATION_PRE_DELIVERY',
    'ORDER_IMPORT',
)
READ_ONLY_SCOPES = ('ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW')


def normalize_email(value: str) -> str:
    """Trim and normalize the domain only; never rewrite a local part (dot/tag)."""
    if not isinstance(value, str):
        raise ValueError('请输入有效的邮箱地址')
    email = value.strip()
    if len(email) > 254 or email.count('@') != 1:
        raise ValueError('请输入有效的邮箱地址')
    local, domain = email.split('@')
    if not local or len(local) > 64 or not domain or '.' not in domain:
        raise ValueError('请输入有效的邮箱地址')
    if any(char.isspace() for char in email) or local.startswith('.') or local.endswith('.'):
        raise ValueError('请输入有效的邮箱地址')
    if any(char in local for char in '(),:;<>[]\\"'):
        raise ValueError('请输入有效的邮箱地址')
    return local + '@' + domain.lower()


def mask_email(value: str | None) -> str | None:
    if not value:
        return None
    local, _, domain = value.partition('@')
    if not domain:
        return value[:1] + '***'
    return (local[:1] or '*') + '***@' + domain


def _validate_password(value: str) -> str:
    if len(value) < 8 or len(value) > 128 or not value.strip():
        raise ValueError('密码长度需为 8-128 位')
    if value != value.strip():
        raise ValueError('密码首尾不能包含空格')
    return value


def _email_field(value: str) -> str:
    return normalize_email(value)


class RegisterInput(DTO):
    email: str
    password: str
    invite_code: str | None = Field(default=None, max_length=64)
    accepted_terms: bool | None = None

    _email = field_validator('email')(_email_field)
    _password = field_validator('password')(_validate_password)

    @field_validator('accepted_terms')
    @classmethod
    def terms_accepted(cls, value):
        if value is False:
            raise ValueError('请先确认账号使用与隐私提示')
        return value


class CustomerLoginInput(DTO):
    email: str
    password: str = Field(max_length=512)
    replace_session_id: str | None = None

    _email = field_validator('email')(_email_field)


class EmailInput(DTO):
    email: str

    _email = field_validator('email')(_email_field)


class TokenInput(DTO):
    token: str = Field(min_length=1, max_length=512)


class ResetPasswordInput(DTO):
    token: str = Field(min_length=1, max_length=512)
    new_password: str

    _password = field_validator('new_password')(_validate_password)


class ChangePasswordInput(DTO):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str

    _password = field_validator('new_password')(_validate_password)


class ResendVerificationInput(DTO):
    email: str | None = None
    registration_challenge_id: str | None = Field(default=None, max_length=64)

    _email = field_validator('email')(lambda value: normalize_email(value) if value else value)

    @model_validator(mode='after')
    def one_identifier(self):
        if not self.email and not self.registration_challenge_id:
            raise ValueError('请提供待验证邮箱或注册挑战')
        return self


class ChangePendingEmailInput(DTO):
    registration_challenge_id: str = Field(min_length=1, max_length=64)
    new_email: str

    _email = field_validator('new_email')(_email_field)


class ChangeEmailInput(DTO):
    new_email: str
    current_password: str = Field(min_length=1, max_length=512)
    expected_version: int = Field(ge=0)

    _email = field_validator('new_email')(_email_field)


class ConfirmInput(DTO):
    confirm: Literal[True]


class ImportOrderInput(DTO):
    order_id: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]


class SessionRevokeInput(DTO):
    session_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default='', max_length=500)


class ProfileInput(DTO):
    display_name: str = Field(min_length=1, max_length=40)
    expected_version: int = Field(ge=0)

    @field_validator('display_name')
    @classmethod
    def trimmed(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('昵称不能为空')
        return value


class NotificationInput(DTO):
    order_created: bool
    order_status_updated: bool
    referral_reward: bool
    expected_version: int = Field(ge=0)


class NotificationPreferences(DTO):
    order_created: bool
    order_status_updated: bool
    referral_reward: bool


class CustomerAccountView(DTO):
    id: str
    email_masked: str
    display_name: str
    verified: bool
    status: Literal['PENDING_VERIFICATION', 'ACTIVE', 'SUSPENDED']
    version: int
    notification_preferences: NotificationPreferences


class CustomerSessionView(DTO):
    role: Literal['CUSTOMER'] = 'CUSTOMER'
    auth_kind: Literal['ANONYMOUS', 'REGISTERED']
    account: CustomerAccountView | None = None
    capabilities: list[str] = Field(default_factory=list)
    recovered: bool = False
    recovery_until: datetime | None = None
    message: str | None = None


class RegistrationResult(DTO):
    registration_challenge_id: str | None = None
    email_masked: str
    id: str | None = None
    status: str = 'PENDING_VERIFICATION'
    mail_status: Literal['QUEUED', 'UNAVAILABLE'] = 'QUEUED'
    retry_after_seconds: int = 60
    message: str = ''


class MailResult(DTO):
    status: Literal['QUEUED', 'ACCEPTED', 'UNAVAILABLE'] = 'ACCEPTED'
    mail_status: Literal['QUEUED', 'UNAVAILABLE'] = 'QUEUED'
    retry_after_seconds: int = 60
    message: str = ''


class PasswordChangedResult(DTO):
    password_changed: bool = True
    sessions_revoked: int = 0
    must_change_password: bool = False


class SessionDeviceView(DTO):
    id: str
    device: str
    last_seen_at: datetime
    current: bool
    created_at: datetime | None = None
    expires_at: datetime | None = None


class SessionList(DTO):
    items: list[SessionDeviceView]


class RevokeResult(DTO):
    revoked: bool
    revoked_count: int = 0
    current_revoked: bool = False


class LinkGuestResult(DTO):
    rotation_completed: bool
    customer_principal_id: str
    guest_principal_id: str
    visible_scope: Literal['LINKED_ORDERS'] = 'LINKED_ORDERS'
    id: str | None = None
    status: str = 'LINKED'


class ImportOrderResult(DTO):
    account_order_grant_id: str
    source_grant_id: str
    scopes: list[str]
    expires_at: datetime
    owner_preserved: bool = True
    id: str | None = None
    order_id: str | None = None
    status: str = 'IMPORTED'


class StaffSessionView(DTO):
    account_id: str
    is_super_admin: bool = False
    discount_level: int | None = Field(default=None, ge=1, le=6)
    id: str
    role: Literal['ADMIN', 'COURIER']
    name: str
    username: str
    email_masked: str | None = None
    email_verified: bool = False
    must_change_password: bool = False
    capabilities: list[str] = Field(default_factory=list)


class StaffView(DTO):
    id: str
    account_id: str
    is_super_admin: bool = False
    discount_level: int | None = Field(default=None, ge=1, le=6)
    version: int
    name: str
    username: str
    phone: str
    email_masked: str | None = None
    email_verified: bool = False
    role: Literal['ADMIN', 'COURIER']
    enabled: bool
    status: Literal['PENDING_SETUP', 'ACTIVE', 'DISABLED']
    must_change_password: bool
    capabilities: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)


class StaffList(DTO):
    items: list[StaffView]
    staff: list[StaffView]


class GrantableAccountView(DTO):
    """The minimum account identity needed to choose a grant target.

    Deliberately excludes the full email, phone, principal, orders and every
    profile field; ``staff.manage`` is not a general customer-search permission.
    """

    id: str
    display_name: str
    email_masked: str
    verified: Literal[True] = True


class GrantableAccountList(DTO):
    items: list[GrantableAccountView]


class StaffCreateInput(DTO):
    """Grant an existing verified customer account employee rights.

    ``name``/``username``/``phone`` are optional internal metadata only.  The
    public workspace display name always comes from the linked account nickname;
    ``name`` is the employee/HR name used by staff administration and payroll.
    None of these fields is ever used to find or bind an account; that is always
    ``accountId``.
    """

    account_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=120)
    phone: str = Field(default='', max_length=32)
    email: str | None = None
    role: Literal['ADMIN', 'COURIER']
    enabled: bool = True
    status: Literal['PENDING_SETUP', 'ACTIVE', 'DISABLED'] = 'ACTIVE'
    must_change_password: bool = False
    reason: str = Field(min_length=1, max_length=500)
    password: str | None = None
    capabilities: list[str] | None = None
    is_super_admin: bool = False
    discount_level: int | None = Field(default=None, ge=1, le=6)

    _email = field_validator('email')(lambda value: normalize_email(value) if value else value)
    _password = field_validator('password')(
        lambda value: _validate_password(value) if value is not None else value)

    @field_validator('account_id')
    @classmethod
    def account_id_trimmed(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请选择客户账号')
        return value

    @field_validator('name', 'username')
    @classmethod
    def trimmed(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError('不能为空')
        return value

    @model_validator(mode='after')
    def super_admin_is_admin(self):
        if self.is_super_admin and self.role != 'ADMIN':
            raise ValueError('只有管理员可以成为超级管理员')
        return self


class StaffUpdateInput(DTO):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    account_id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = None
    role: Literal['ADMIN', 'COURIER'] | None = None
    enabled: bool | None = None
    status: Literal['PENDING_SETUP', 'ACTIVE', 'DISABLED'] | None = None
    must_change_password: bool | None = None
    password: str | None = None
    capabilities: list[str] | None = None
    is_super_admin: bool | None = None
    discount_level: int | None = Field(default=None, ge=1, le=6)

    _email = field_validator('email')(lambda value: normalize_email(value) if value else value)
    _password = field_validator('password')(
        lambda value: _validate_password(value) if value is not None else value)

    @field_validator('name', 'username')
    @classmethod
    def trimmed(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError('不能为空')
        return value


class StaffActionResult(DTO):
    temporary_password: str | None = None
    expires_at: datetime | None = None
    status: str
    message: str = ''
    mail_status: Literal['QUEUED', 'UNAVAILABLE'] | None = None
    retry_after_seconds: int | None = None
    must_change_password: bool | None = None
    revoked: bool | None = None


class AccessLinkInput(DTO):
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    scopes: list[str] | None = None
    # The delivery docs also write the list as `scope`; accept both spellings.
    scope: list[str] | None = None
    reissue: bool = False
    revoke_previous: bool = False


class AccessLinkView(DTO):
    id: str
    order_id: str
    version: int
    scopes: list[str]
    created_at: datetime
    created_by: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_access_at: datetime | None = None


class AccessLinkList(DTO):
    items: list[AccessLinkView]


class AccessLinkIssued(DTO):
    grant: AccessLinkView
    access_token: str
    revoked_grant_ids: list[str] = Field(default_factory=list)
    id: str | None = None
    order_id: str | None = None
    status: str = 'ISSUED'


class OrderAccessView(DTO):
    order_id: str
    source_grant_id: str
    scopes: list[str]
    allowed_actions: list[str]
    expires_at: datetime | None = None


class OrderAccessExchange(DTO):
    order: dict[str, Any]
    access: OrderAccessView
    id: str | None = None
    order_id: str | None = None
    status: str = 'EXCHANGED'


class ExportRequestInput(DTO):
    reason: str = Field(default='个人资料导出', max_length=500)
    fields: list[str] | None = None


class ExportRequestResult(DTO):
    export_job_id: str
    status: str
    expires_at: datetime
    message: str = '导出申请已受理，文件生成后可受权下载，排队不代表已完成'


class DeletionRequestInput(DTO):
    reason: str = Field(default='用户自助申请注销', max_length=500)


class DeletionRequestResult(DTO):
    deletion_request_id: str
    status: str
    requested_at: datetime
    blockers: list[str] = Field(default_factory=list)
    message: str = '注销申请已提交'
    id: str | None = None
    recovery_until: datetime | None = None
    can_recover: bool = False
    recovered: bool = False


class GrantRevokeResult(DTO):
    revoked: bool
    grant_id: str
    affected_grant_ids: list[str]
    owner_preserved: bool = True
    id: str | None = None
    order_id: str | None = None
    status: str = 'REVOKED'


class LoginInput(DTO):
    username: str = Field(max_length=120)
    password: str = Field(max_length=512)

    @field_validator('username')
    @classmethod
    def trimmed(cls, value):
        return value.strip()


StaffLoginInput = LoginInput


class ExchangeInput(DTO):
    order_id: str = Field(min_length=1, max_length=64)
    access_token: str = Field(min_length=1, max_length=512)


class ReasonInput(DTO):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
