"""DTOs for SMTP configuration, templates and safe preview/test sends."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .core import DTO

SecurityMode = Literal['STARTTLS', 'SSL_TLS', 'NONE']


class MailTestResult(DTO):
    status: str
    time: datetime
    message: str


class MailConfigDTO(DTO):
    version: int = Field(ge=0)
    enabled: bool
    host: str
    port: int = Field(ge=0)
    security: SecurityMode
    auth_enabled: bool
    username: str
    secret_configured: bool
    from_name: str
    from_address: str
    reply_to: str
    timeout_seconds: int = Field(ge=0)
    allow_insecure: bool = False
    last_test: MailTestResult | None = None


class MailConfigUpdate(DTO):
    expected_version: int = Field(ge=0)
    enabled: bool
    host: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    security: SecurityMode
    auth_enabled: bool
    username: str = Field(default='', max_length=254)
    secret: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    from_name: str = Field(default='', max_length=120)
    from_address: str = Field(default='', max_length=254)
    reply_to: str = Field(default='', max_length=254)
    timeout_seconds: int = Field(ge=1, le=120)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def secret_choice(self):
        if self.clear_secret and self.secret:
            raise ValueError('不能同时替换与清除密钥')
        return self


class MailConfigClear(DTO):
    expected_version: int = Field(ge=0)
    clear_secret: Literal[True]
    reason: str = Field(min_length=1, max_length=500)


class MailConfigInput(DTO):
    expected_version: int = Field(ge=0)
    enabled: bool | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=0, le=65535)
    security: SecurityMode | None = None
    auth_enabled: bool | None = None
    username: str | None = Field(default=None, max_length=254)
    secret: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    from_name: str | None = Field(default=None, max_length=120)
    from_address: str | None = Field(default=None, max_length=254)
    reply_to: str | None = Field(default=None, max_length=254)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def require_complete_update(self):
        if self.clear_secret and self.secret:
            raise ValueError('不能同时替换与清除密钥')
        if self.clear_secret:
            return self
        if None in (self.enabled, self.host, self.port, self.security,
                    self.auth_enabled, self.username, self.from_name,
                    self.from_address, self.reply_to, self.timeout_seconds):
            raise ValueError('邮件配置更新必须提供完整的安全字段')
        if self.secret and self.clear_secret:
            raise ValueError('不能同时替换与清除密钥')
        return self


class MailTemplateVariable(DTO):
    key: str
    description: str


class MailTemplateDTO(DTO):
    key: str
    name: str
    version: int = Field(ge=0)
    subject: str
    text_body: str
    html_body: str
    enabled: bool
    critical: bool
    variables: list[MailTemplateVariable]
    updated_by: str
    updated_at: datetime


class TemplateList(DTO):
    items: list[MailTemplateDTO]


class TemplateInput(DTO):
    expected_version: int = Field(ge=0)
    subject: str = Field(min_length=1, max_length=300)
    text_body: str = Field(min_length=1, max_length=20000)
    html_body: str
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class TemplatePreviewInput(DTO):
    subject: str | None = None
    text_body: str | None = None
    html_body: str | None = None


class MailReasonInput(DTO):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class TemplatePreview(DTO):
    subject: str
    text_body: str
    html_body: str


class TestMailInput(DTO):
    to: str = Field(min_length=3, max_length=254)

    @field_validator('to')
    @classmethod
    def valid_recipient(cls, value):
        import re
        pattern = r'^[^\s@<>,;"]{1,64}@[^\s@<>,;"]{1,189}\.[A-Za-z]{2,}$'
        if '\r' in value or '\n' in value or not re.match(pattern, value):
            raise ValueError('收件邮箱格式无效')
        return value


class MailQueuedResult(DTO):
    email_job_id: str
    status: str
    delivery_status: str = 'UNKNOWN'
    message: str = '已入队；SMTP 服务器接受不代表已送达收件箱'


class MailConnectionResult(DTO):
    status: str
    tested_at: datetime
    message: str
    error_class: str | None = None
