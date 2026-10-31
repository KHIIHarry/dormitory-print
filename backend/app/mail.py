"""Reliable transactional email outbox, SMTP transport and editable templates.

Design boundaries:
* Payloads that permit sending (recipient, rendered action URL) live only in an
  encrypted short-lived column and are destroyed after acceptance or expiry.
* Status/log metadata is redacted; the SMTP secret is sealed separately and
  never returned by any route.
* SMTP connection targets are resolved and checked against SSRF policy unless
  the deployment explicitly allowlists the host.
* An SMTP acceptance is not a delivery claim.
"""
from __future__ import annotations

from datetime import timedelta
import ipaddress
import os
import hashlib
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html.parser import HTMLParser
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import flag_modified

from .config import get_config
from .core import (
    Actor, DomainError, Envelope, actor_dep, advisory_lock, audit, enqueue_job,
    lock, ok, require_admin, seal, touch, unseal,
)
from .db import get_db
from .mail_html import template_html
from .mail_schemas import (
    MailConfigDTO, MailConfigInput, MailConnectionResult, MailQueuedResult,
    MailConfigClear, MailConfigUpdate, MailReasonInput, MailTemplateDTO,
    MailTestResult, TemplateInput, TemplateList, TemplatePreview,
    TemplatePreviewInput, TestMailInput,
)
from .models import AuditLog, ConfigSection, EmailJob, MailTemplate, utcnow
from .settings import get_settings

router = APIRouter()

TEMPLATE_KEYS = (
    'CUSTOMER_VERIFY_EMAIL',
    'CUSTOMER_RESET_PASSWORD',
    'ADMIN_ORDER_CREATED',
    'COURIER_DISPATCH',
    'DELIVERY_UPDATED',
    'PAYMENT_SUCCESS',
    'ORDER_DELIVERED',
    'REFERRAL_REWARD_GRANTED',
    'ACCOUNT_DELETED',
    'ORDER_CREATED',
    'ORDER_STATUS_UPDATED',
)

VARIABLE_DESCRIPTIONS = {
    'brandName': '品牌名称',
    'logoUrl': '系统品牌 Logo（固定资源，不可自定义）',
    'actionUrl': '一次性安全操作链接（测试仅使用不可操作样例）',
    'expiresMinutes': '安全链接有效分钟数',
    'orderCode': '订单短提取码',
    'orderStatus': '订单状态中文名称',
    'orderLink': '受权订单查询入口（不含管理链接）',
    'orderAmount': '订单金额（含币种）',
    'paidAmount': '实付金额（含币种）',
    'customerNickname': '客户昵称',
    'customerPhone': '客户联系电话',
    'courierName': '配送员称呼',
    'courierLink': '配送员任务入口',
    'batchName': '配送批次名称',
    'deliveryDate': '配送日期',
    'deliverySlot': '配送时段',
    'deliveryLocation': '配送楼栋或地点',
    'deliveredAt': '签收时间',
    'orderCount': '本批订单数量',
    'deletionDeadline': '注销完成期限',
    'rewardText': '奖励说明（不包含其他客户信息）',
}

DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    'CUSTOMER_VERIFY_EMAIL': {
        'name': '验证邮箱',
        'subject': '{{brandName}}｜请验证您的邮箱',
        'textBody': (
            '您好！感谢使用 {{brandName}}。\n'
            '请通过以下链接验证您的邮箱：\n{{actionUrl}}\n'
            '此链接将在 {{expiresMinutes}} 分钟后失效，且只能使用一次。\n'
            '如果这不是您的操作，请忽略本邮件。'
        ),
        'htmlBody': template_html('CUSTOMER_VERIFY_EMAIL'),
        'variables': ['brandName', 'actionUrl', 'expiresMinutes'],
        'critical': True,
        'enabled': True,
    },
    'CUSTOMER_RESET_PASSWORD': {
        'name': '重置客户密码',
        'subject': '{{brandName}}｜重置账号密码',
        'textBody': (
            '您好！我们收到了您的 {{brandName}} 账号密码重置请求。\n'
            '请通过以下链接设置新密码：\n{{actionUrl}}\n'
            '链接将在 {{expiresMinutes}} 分钟后失效，且只能使用一次。\n'
            '如果不是您本人申请，请忽略本邮件。任何工作人员都不会索要您的密码。'
        ),
        'htmlBody': template_html('CUSTOMER_RESET_PASSWORD'),
        'variables': ['brandName', 'actionUrl', 'expiresMinutes'],
        'critical': True,
        'enabled': True,
    },
    'ADMIN_ORDER_CREATED': {
        'name': '新订单通知（管理员）',
        'subject': '{{brandName}}｜新订单 {{orderCode}} 待处理',
        'textBody': (
            '{{brandName}} 收到一笔新订单 {{orderCode}}，金额 {{orderAmount}}。\n'
            '客户昵称：{{customerNickname}}\n联系电话：{{customerPhone}}\n'
            '请及时核对文件并安排打印，留意添加客户微信以便沟通。\n'
            '查看订单：{{orderLink}}'
        ),
        'htmlBody': template_html('ADMIN_ORDER_CREATED'),
        'variables': ['brandName', 'orderCode', 'orderAmount', 'customerNickname',
                      'customerPhone', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'COURIER_DISPATCH': {
        'name': '配送派单通知（配送员）',
        'subject': '{{brandName}}｜配送任务 {{batchName}}（{{deliveryDate}} {{deliverySlot}}）',
        'textBody': (
            '{{courierName}}，你好！{{brandName}} 的配送任务已发布。\n'
            '批次「{{batchName}}」：{{deliveryDate}} {{deliverySlot}}，'
            '共 {{orderCount}} 单。\n'
            '请按页面指引领取并配送：{{courierLink}}\n'
            '出发前请核对订单数量与包装。'
        ),
        'htmlBody': template_html('COURIER_DISPATCH'),
        'variables': ['brandName', 'courierName', 'batchName', 'deliveryDate',
                      'deliverySlot', 'orderCount', 'courierLink'],
        'critical': False,
        'enabled': True,
    },
    'DELIVERY_UPDATED': {
        'name': '配送信息变更',
        'subject': '{{brandName}}｜订单 {{orderCode}} 配送信息已更新',
        'textBody': (
            '您好！{{brandName}} 订单 {{orderCode}} 的配送信息已更新。\n'
            '配送楼栋：{{deliveryLocation}}\n配送时段：{{deliverySlot}}\n'
            '查看订单：{{orderLink}}'
        ),
        'htmlBody': template_html('DELIVERY_UPDATED'),
        'variables': ['brandName', 'orderCode', 'deliveryLocation',
                      'deliverySlot', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'PAYMENT_SUCCESS': {
        'name': '付款成功',
        'subject': '{{brandName}}｜订单 {{orderCode}} 付款成功',
        'textBody': (
            '您好！{{brandName}} 已确认收到订单 {{orderCode}} 的付款 {{paidAmount}}。\n'
            '我们正在安排打印与配送，签收后会再发送确认邮件。\n'
            '查看订单：{{orderLink}}'
        ),
        'htmlBody': template_html('PAYMENT_SUCCESS'),
        'variables': ['brandName', 'orderCode', 'paidAmount', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'ORDER_DELIVERED': {
        'name': '签收成功',
        'subject': '{{brandName}}｜订单 {{orderCode}} 已签收',
        'textBody': (
            '您好！{{brandName}} 订单 {{orderCode}} 已于 {{deliveredAt}} 签收。\n'
            '配送楼栋：{{deliveryLocation}}\n查看订单：{{orderLink}}\n'
            '请及时取回资料并妥善保管。'
        ),
        'htmlBody': template_html('ORDER_DELIVERED'),
        'variables': ['brandName', 'orderCode', 'deliveredAt',
                      'deliveryLocation', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'ACCOUNT_DELETED': {
        'name': '账号注销受理',
        'subject': '{{brandName}}｜账号注销申请已受理',
        'textBody': (
            '你的 {{brandName}} 账号注销申请已受理。\n'
            '我们将在 {{deletionDeadline}} 前完成注销，并停止发送后续通知。\n'
            '在此之前登录账号可撤销申请。注销完成后订单、优惠券与历史记录将无法恢复。'
        ),
        'htmlBody': template_html('ACCOUNT_DELETED'),
        'variables': ['brandName', 'deletionDeadline'],
        'critical': False,
        'enabled': True,
    },
    'ORDER_CREATED': {
        'name': '下单回执',
        'subject': '{{brandName}}｜订单 {{orderCode}} 已创建',
        'textBody': (
            '您好！您在 {{brandName}} 的订单 {{orderCode}} 已提交。\n'
            '当前状态：{{orderStatus}}\n'
            '您可通过以下入口查看订单：{{orderLink}}\n'
            '请按页面指引联系打印员并完成线下付款，订单不会自动确认为已付款。'
        ),
        'htmlBody': template_html('ORDER_CREATED'),
        'variables': ['brandName', 'orderCode', 'orderStatus', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'ORDER_STATUS_UPDATED': {
        'name': '订单进度',
        'subject': '{{brandName}}｜订单 {{orderCode}} 进度更新',
        'textBody': (
            '您好！您的 {{brandName}} 订单 {{orderCode}} 状态已更新：{{orderStatus}}。\n'
            '查看订单：{{orderLink}}\n实际配送时间请以最新订单页面为准。'
        ),
        'htmlBody': template_html('ORDER_STATUS_UPDATED'),
        'variables': ['brandName', 'orderCode', 'orderStatus', 'orderLink'],
        'critical': False,
        'enabled': True,
    },
    'REFERRAL_REWARD_GRANTED': {
        'name': '邀请奖励到账',
        'subject': '{{brandName}}｜邀请奖励已发放',
        'textBody': (
            '您好！感谢您邀请朋友使用 {{brandName}}。\n'
            '您获得的奖励为：{{rewardText}}\n'
            '请登录「我的优惠券」查看使用条件、有效期与活动说明。'
        ),
        'htmlBody': template_html('REFERRAL_REWARD_GRANTED'),
        'variables': ['brandName', 'rewardText'],
        'critical': False,
        'enabled': True,
    },
}

SAMPLE_VARIABLES = {
    'brandName': 'DormPrint 示例',
    'logoUrl': '/assets/icon-192.png',
    'actionUrl': 'https://example.invalid/safe-preview#token=preview-not-usable',
    'expiresMinutes': '30',
    'orderCode': 'DP20261030',
    'orderStatus': '待确认',
    'orderLink': 'https://example.invalid/orders/sample',
    'orderAmount': '¥36.00',
    'paidAmount': '¥36.00',
    'customerNickname': '示例同学',
    'customerPhone': '138****0000',
    'courierName': '示例配送员',
    'courierLink': 'https://example.invalid/courier',
    'batchName': '10月31日 晚班',
    'deliveryDate': '2026-10-31',
    'deliverySlot': '19:00–20:00',
    'deliveryLocation': '示例公寓 3 栋',
    'deliveredAt': '2026-10-31 20:12',
    'orderCount': '6',
    'deletionDeadline': '2026-11-14',
    'rewardText': '示例奖励：一张优惠券（以真实活动规则为准）',
}

_ALLOWED_TAGS = {
    'p', 'br', 'strong', 'b', 'em', 'i', 'h1', 'h2', 'h3', 'h4',
    'ul', 'ol', 'li', 'blockquote', 'pre', 'span', 'div', 'hr', 'a',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'img',
}
_ALLOWED_ATTRIBUTES = {
    'style', 'href', 'role', 'width', 'height', 'align', 'valign', 'bgcolor',
    'cellpadding', 'cellspacing', 'border', 'colspan', 'rowspan',
    'src', 'alt',
}
_SAFE_HREF_PREFIXES = ('http://', 'https://', 'mailto:')
_BLOCKED_HTML_TOKENS = (
    '<script', '<iframe', '<object', '<embed', '<link', '<meta',
    'javascript:', 'vbscript:', 'data:text/html', 'expression(', 'url(',
    '@import', '-moz-binding',
)
_BLOCKED_STYLES = (
    'url(', 'expression(', '@import', 'javascript:', 'vbscript:',
    'behavior:', '-moz-binding', 'position:fixed', 'position:absolute',
)
_VARIABLE_RE = re.compile(r'\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}')
_EMAIL_RE = re.compile(r'^[^\s@<>,;"]{1,64}@[^\s@<>,;"]{1,189}\.[A-Za-z]{2,}$')
_MAX_PAYLOAD_VARIABLES = 20
_MAX_VARIABLE_LENGTH = 4000


def _ttl_hours() -> int:
    raw = os.environ.get('DORMPRINT_MAIL_PAYLOAD_TTL_HOURS', '72')
    try:
        return max(1, min(int(raw), 24 * 30))
    except ValueError:
        return 72


def _worker_business_key(email_key: str, suffix: str = '') -> str:
    value = 'email:' + email_key + suffix
    if len(value) <= 200:
        return value
    return 'email:sha256:' + hashlib.sha256(value.encode()).hexdigest()


def _retry_max_attempts() -> int:
    raw = os.environ.get('DORMPRINT_MAIL_RETRY_MAX_ATTEMPTS', '5')
    try:
        return max(1, min(int(raw), 20))
    except ValueError:
        return 5


def _valid_email(value: str) -> bool:
    return bool(value and len(value) <= 254 and _EMAIL_RE.match(value)
                and '\r' not in value and '\n' not in value)


def _raw_data(section: ConfigSection) -> dict:
    return dict(section.data or {})


def _security(data: dict) -> str:
    value = data.get('security') or data.get('securityMode') or 'STARTTLS'
    return value if value in ('STARTTLS', 'SSL_TLS', 'NONE') else 'STARTTLS'


def _auth_enabled(data: dict) -> bool:
    if 'authEnabled' in data:
        return bool(data['authEnabled'])
    return bool(data.get('smtpAuthEnabled', True))


def _timeout(data: dict) -> int:
    try:
        value = int(data.get('timeoutSeconds') or data.get('connectionTimeoutSeconds') or 10)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, 120))


def _config_values(db) -> tuple[ConfigSection, dict]:
    section = get_config(db, 'mail')
    data = _raw_data(section)
    return section, {
        'enabled': bool(data.get('enabled')),
        'host': str(data.get('host') or '').strip(),
        'port': int(data.get('port') or 0),
        'security': _security(data),
        'authEnabled': _auth_enabled(data),
        'username': str(data.get('username') or ''),
        'fromName': str(data.get('fromName') or ''),
        'fromAddress': str(data.get('fromAddress') or ''),
        'replyTo': str(data.get('replyTo') or ''),
        'timeoutSeconds': _timeout(data),
        'secretCiphertext': data.get('secretCiphertext'),
        'allowInsecure': bool(get_settings().allow_insecure_smtp),
        'lastTest': data.get('lastTest'),
    }


def _secret(values: dict) -> str:
    ciphertext = values.get('secretCiphertext')
    if not ciphertext:
        return ''
    try:
        payload = unseal(ciphertext)
    except Exception:
        return ''
    return str(payload.get('secret') or '') if isinstance(payload, dict) else ''


def mail_available(db) -> bool:
    """True only when a complete, usable SMTP configuration exists.

    This performs validation only; it never opens a network connection.
    """
    try:
        _, values = _config_values(db)
    except DomainError:
        return False
    if not values['enabled'] or not values['host'] or not (1 <= values['port'] <= 65535):
        return False
    if any(char.isspace() for char in values['host']) or '/' in values['host'] or '://' in values['host']:
        return False
    if not _valid_email(values['fromAddress']):
        return False
    if values['security'] == 'NONE' and not values['allowInsecure']:
        return False
    if values['authEnabled'] and (not values['username'] or not _secret(values)):
        return False
    last = values.get('lastTest') or {}
    if isinstance(last, dict) and str(last.get('status') or '').upper() == 'FAIL':
        # An explicit failed connection test makes the service known-unavailable;
        # registration must not create a pending account until it is retested.
        return False
    return True


def mail_config_view(db) -> MailConfigDTO:
    section, values = _config_values(db)
    last = values.get('lastTest')
    return MailConfigDTO(
        version=section.version,
        enabled=values['enabled'],
        host=values['host'],
        port=values['port'],
        security=values['security'],
        authEnabled=values['authEnabled'],
        username=values['username'],
        secretConfigured=bool(values['secretCiphertext']),
        fromName=values['fromName'],
        fromAddress=values['fromAddress'],
        replyTo=values['replyTo'],
        timeoutSeconds=values['timeoutSeconds'],
        allowInsecure=values['allowInsecure'],
        lastTest=MailTestResult.model_validate(last) if last else None,
    )


def smtp_target_error(host: str, settings=None) -> str | None:
    """Return a redacted error class for a disallowed/unresolvable SMTP host."""
    settings = settings or get_settings()
    host = (host or '').strip().strip('[]').lower()
    if not host or len(host) > 255:
        return 'MAIL_SSRF_BLOCKED'
    allowed = {item.strip().lower() for item in (settings.mail_allowed_hosts or '').split(',') if item.strip()}
    if host in allowed:
        return None
    if getattr(settings, 'allow_internal_smtp', False):
        return None
    if host in ('localhost', 'localhost.localdomain') or host.endswith('.localhost'):
        return 'MAIL_SSRF_BLOCKED'
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return 'MAIL_DNS_FAILED'
    if not infos:
        return 'MAIL_DNS_FAILED'
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split('%')[0])
        except ValueError:
            return 'MAIL_SSRF_BLOCKED'
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast
                or address.is_unspecified):
            return 'MAIL_SSRF_BLOCKED'
    return None


def is_safe_smtp_target(host: str, settings=None) -> bool:
    """SSRF guard: reject loopback/private/link-local/metadata targets."""
    return smtp_target_error(host, settings) is None


class MailTransportError(Exception):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def _smtp_session(values: dict):
    host, port = values['host'], values['port']
    settings = get_settings()
    target_error = smtp_target_error(host, settings)
    if target_error:
        message = ('SMTP 主机名无法解析' if target_error == 'MAIL_DNS_FAILED'
                   else 'SMTP 目标地址被安全策略拒绝')
        raise MailTransportError(target_error, message)
    timeout = values['timeoutSeconds']
    context = ssl.create_default_context()
    try:
        if values['security'] == 'SSL_TLS':
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
        client.ehlo()
        if values['security'] == 'STARTTLS':
            if not client.has_extn('starttls'):
                client.close()
                raise MailTransportError('MAIL_TLS_FAILED', 'SMTP 服务器未提供 STARTTLS')
            client.starttls(context=context)
            client.ehlo()
        if values['security'] == 'NONE' and not values['allowInsecure']:
            client.close()
            raise MailTransportError('MAIL_CONFIG_INVALID', '未批准无加密 SMTP 连接')
        if values['authEnabled']:
            password = _secret(values)
            if not password:
                client.close()
                raise MailTransportError('MAIL_AUTH_FAILED', 'SMTP 密钥缺失')
            client.login(values['username'], password)
        return client
    except MailTransportError:
        raise
    except smtplib.SMTPAuthenticationError as error:
        raise MailTransportError('MAIL_AUTH_FAILED', 'SMTP 身份认证失败') from error
    except smtplib.SMTPNotSupportedError as error:
        raise MailTransportError('MAIL_TLS_FAILED', 'SMTP 服务器不支持所需安全能力') from error
    except ssl.SSLError as error:
        raise MailTransportError('MAIL_TLS_FAILED', 'SMTP TLS 证书或握手校验失败') from error
    except socket.gaierror as error:
        raise MailTransportError('MAIL_DNS_FAILED', 'SMTP 主机名无法解析') from error
    except (socket.timeout, TimeoutError) as error:
        raise MailTransportError('MAIL_CONNECTION_FAILED', 'SMTP 连接超时') from error
    except (ConnectionError, OSError) as error:
        raise MailTransportError('MAIL_CONNECTION_FAILED', 'SMTP 连接失败') from error
    except smtplib.SMTPException as error:
        raise MailTransportError('MAIL_SEND_FAILED', 'SMTP 会话失败') from error


def _connect_check(values: dict) -> None:
    client = _smtp_session(values)
    try:
        client.noop()
    except smtplib.SMTPException as error:
        raise MailTransportError('MAIL_SEND_FAILED', 'SMTP 会话检查失败') from error
    finally:
        try:
            client.quit()
        except Exception:
            pass


def _send(values: dict, payload: dict) -> None:
    client = _smtp_session(values)
    message = EmailMessage()
    message['Subject'] = payload['subject']
    message['From'] = formataddr((values['fromName'] or 'DormPrint', values['fromAddress']))
    message['To'] = payload['to']
    if values['replyTo']:
        message['Reply-To'] = values['replyTo']
    message.set_content(payload['textBody'])
    if payload.get('htmlBody'):
        message.add_alternative(payload['htmlBody'], subtype='html')
    try:
        client.send_message(message)
    except smtplib.SMTPException as error:
        raise MailTransportError('MAIL_SEND_FAILED', 'SMTP 服务器拒绝接收邮件') from error
    finally:
        try:
            client.quit()
        except Exception:
            pass


def _variables_for(template: dict) -> list[str]:
    variables = template.get('variables') or []
    keys = [item['key'] if isinstance(item, dict) else str(item) for item in variables]
    if 'logoUrl' not in keys:
        keys.append('logoUrl')
    return keys


# The brand logo is a fixed, self-hosted asset. Every editable template may
# reference it, but it is never part of the required-variable contract.
for _default_template in DEFAULT_TEMPLATES.values():
    if 'logoUrl' not in _default_template['variables']:
        _default_template['variables'].append('logoUrl')


def _template_default(key: str) -> dict:
    if key not in DEFAULT_TEMPLATES:
        raise DomainError('MAIL_TEMPLATE_INVALID', '未知邮件模板', 404)
    return DEFAULT_TEMPLATES[key]


def _template_data(row: MailTemplate) -> dict:
    return dict(row.data or {})


def _template_payload(row: MailTemplate) -> dict:
    data = _template_data(row)
    variables = list(data.get('variables') or [])
    return {
        'subject': row.subject,
        'textBody': row.text_body,
        'htmlBody': row.html_body or '',
        'variables': variables,
        'required': data.get('required'),
        'critical': bool(data.get('critical')),
        'enabled': bool(row.enabled),
    }


def _template_view(row: MailTemplate) -> MailTemplateDTO:
    data = _template_data(row)
    variables = data.get('variables') or []
    return MailTemplateDTO(
        key=row.key,
        name=data.get('name') or row.key,
        version=row.version,
        subject=row.subject,
        textBody=row.text_body,
        htmlBody=row.html_body or '',
        enabled=bool(row.enabled),
        critical=bool(data.get('critical')),
        variables=[
            {'key': item['key'] if isinstance(item, dict) else item,
             'description': VARIABLE_DESCRIPTIONS.get(
                 item['key'] if isinstance(item, dict) else item, '允许模板变量')}
            for item in variables
        ],
        updatedBy=data.get('updatedBy') or '系统默认',
        updatedAt=row.updated_at,
    )


def ensure_template(db, key: str) -> MailTemplate:
    key = key.strip().upper()
    default = _template_default(key)
    row = db.scalar(select(MailTemplate).where(MailTemplate.key == key))
    if row:
        return row
    row = MailTemplate(
        key=key,
        subject=default['subject'],
        text_body=default['textBody'],
        html_body=default.get('htmlBody') or '',
        enabled=bool(default.get('enabled', True)),
        data={
            'name': default['name'],
            'variables': [{'key': item, 'description': VARIABLE_DESCRIPTIONS.get(item, '')}
                          for item in default['variables']],
            'required': [item for item in (default.get('required') or default['variables'])
                         if item != 'logoUrl'],
            'critical': bool(default['critical']),
            'isDefault': True,
            'updatedBy': '系统默认',
        },
    )
    db.add(row)
    db.flush()
    return row


def seed_templates(db) -> list[MailTemplate]:
    """Create missing templates and backfill HTML for untouched defaults.

    Older installations stored system defaults with an empty ``html_body``.
    Only rows that are still marked as defaults are upgraded, so an edited
    template is never overwritten and its version is not bumped.
    """
    rows = [ensure_template(db, key) for key in TEMPLATE_KEYS]
    for row in rows:
        default = DEFAULT_TEMPLATES.get(row.key)
        if not default or not default.get('htmlBody'):
            continue
        if (_template_data(row).get('isDefault')
                and (row.html_body or '') != default['htmlBody']):
            row.html_body = default['htmlBody']
    db.flush()
    return rows


def _safe_href(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if _VARIABLE_RE.fullmatch(raw):
        return True
    if not raw.lower().startswith(_SAFE_HREF_PREFIXES):
        return False
    return not any(char in raw for char in '<>"') and not any(
        char.isspace() for char in raw)


def _unsafe_style(value: str) -> bool:
    compact = re.sub(r'\s+', '', value).lower()
    return any(token in compact for token in _BLOCKED_STYLES)


class _TemplateHTMLValidator(HTMLParser):
    """Accept only the bounded rich-text subset the editor advertises."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.error: str | None = None

    def handle_starttag(self, tag, attrs):
        self._inspect(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._inspect(tag, attrs)

    def _inspect(self, tag, attrs) -> None:
        if self.error:
            return
        if tag not in _ALLOWED_TAGS:
            self.error = f'HTML 模板包含不允许的标签 {tag}'
            return
        for name, value in attrs:
            if name not in _ALLOWED_ATTRIBUTES:
                self.error = ('HTML 模板不能包含事件处理属性'
                              if name.startswith('on')
                              else f'HTML 模板包含不允许的属性 {name}')
                return
            if name == 'src' and tag != 'img':
                self.error = 'HTML 模板只允许品牌 Logo 使用图片地址'
                return
            if name == 'href' and not _safe_href(value or ''):
                self.error = 'HTML 模板的链接只允许 http(s)、mailto 或模板变量'
                return
            if name == 'style' and _unsafe_style(value or ''):
                self.error = 'HTML 模板包含不允许的样式'
                return
        if tag == 'img':
            source = (dict(attrs).get('src') or '').strip()
            match = _VARIABLE_RE.fullmatch(source)
            if not match or match.group(1) != 'logoUrl':
                self.error = 'HTML 模板的图片只允许使用系统品牌 Logo'


def validate_template_content(template: dict, required: list[str] | None = None,
                              critical: bool | None = None) -> None:
    subject = str(template.get('subject') or '')
    text = str(template.get('textBody') or '')
    html = str(template.get('htmlBody') or '')
    if not subject.strip() or not text.strip():
        raise DomainError('MAIL_TEMPLATE_INVALID', '邮件主题和纯文本正文不能为空')
    if '\r' in subject or '\n' in subject:
        raise DomainError('MAIL_TEMPLATE_INVALID', '邮件主题不能包含换行')
    allowed = _variables_for(template)
    found = set(_VARIABLE_RE.findall(subject)) | set(_VARIABLE_RE.findall(text)) | \
        set(_VARIABLE_RE.findall(html))
    unknown = found - set(allowed)
    if unknown:
        raise DomainError(
            'MAIL_TEMPLATE_INVALID', '模板使用了未授权的变量',
            field_errors={'variables': [f'不允许使用 {sorted(unknown)}']},
        )
    explicit_required = template.get('required')
    required = required if required is not None else explicit_required or allowed
    required = [item for item in required if item != 'logoUrl']
    text_found = set(_VARIABLE_RE.findall(text))
    missing = [item for item in required if item not in text_found]
    if missing:
        raise DomainError(
            'MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING', '模板缺少必要变量，无法安全发送',
            field_errors={'variables': [f'缺少 {missing}']},
        )
    if not html.strip():
        return
    if len(html) > 60000:
        raise DomainError('MAIL_TEMPLATE_INVALID', 'HTML 模板内容过长')
    lowered = html.lower()
    if any(token in lowered for token in _BLOCKED_HTML_TOKENS):
        raise DomainError('MAIL_TEMPLATE_INVALID', 'HTML 模板包含不允许的脚本或远程资源')
    checker = _TemplateHTMLValidator()
    try:
        checker.feed(html)
        checker.close()
    except Exception as error:  # malformed markup must not crash the request
        raise DomainError('MAIL_TEMPLATE_INVALID', 'HTML 模板无法解析') from error
    if checker.error:
        raise DomainError('MAIL_TEMPLATE_INVALID', checker.error)
    if critical and ('actionUrl' not in allowed or 'actionUrl' not in found):
        raise DomainError('MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING',
                          '安全邮件必须保留一次性操作链接')


def render_template(template: dict, variables: dict[str, Any]) -> dict[str, str]:
    allowed = _variables_for(template)
    required = [item for item in (template.get('required') or allowed)
                if item != 'logoUrl']
    supplied = {str(key): '' if value is None else str(value) for key, value in variables.items()}
    unknown = set(supplied) - set(allowed)
    if unknown:
        raise DomainError('MAIL_TEMPLATE_INVALID', '模板变量不在白名单内',
                          field_errors={'variables': [f'不允许 {sorted(unknown)}']})
    missing = [item for item in required if item not in supplied]
    if missing:
        raise DomainError('MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING', '模板变量缺失',
                          field_errors={'variables': [f'缺少 {missing}']})
    for key, value in supplied.items():
        if len(value) > _MAX_VARIABLE_LENGTH or '\r' in value and key == 'subject':
            raise DomainError('MAIL_TEMPLATE_INVALID', '模板变量内容过长或包含非法字符')

    def replace(value: str, html_escape: bool) -> str:
        def one(match):
            key = match.group(1)
            raw = supplied.get(key, '')
            if key in ('actionUrl', 'orderLink'):
                raw = raw.replace('\r', '').replace('\n', '')
            if html_escape:
                return _escape_html(raw)
            return raw
        return _VARIABLE_RE.sub(one, value)

    subject = replace(str(template.get('subject') or ''), False).replace('\r', '').replace('\n', '')
    text_body = replace(str(template.get('textBody') or ''), False)
    html_body = replace(str(template.get('htmlBody') or ''), True)
    if not html_body.strip():
        html_body = '<pre>' + _escape_html(text_body) + '</pre>'
    return {'subject': subject, 'textBody': text_body, 'htmlBody': html_body}


def _escape_html(value: str) -> str:
    return (value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&#x27;'))


def update_template(db, key: str, payload: dict, expected_version: int,
                    actor: Actor | None = None) -> MailTemplate:
    row = ensure_template(db, key)
    lock(db, MailTemplate, row.id, expected_version)
    data = _template_data(row)
    critical = bool(data.get('critical'))
    if critical and not payload.get('enabled', True):
        raise DomainError('MAIL_TEMPLATE_INVALID', '验证与找回等安全邮件不能停用')
    content = {
        'subject': payload.get('subject', row.subject),
        'textBody': payload.get('textBody', row.text_body),
        'htmlBody': payload.get('htmlBody', row.html_body or ''),
        'variables': data.get('variables') or [],
        'critical': critical,
    }
    validate_template_content(content, _variables_for(content), critical)
    row.subject = content['subject']
    row.text_body = content['textBody']
    row.html_body = content['htmlBody']
    row.enabled = bool(payload.get('enabled', row.enabled))
    data['updatedBy'] = (actor.id if actor else '管理员')
    data['isDefault'] = False
    row.data = data
    touch(row)
    db.flush()
    return row


def restore_template(db, key: str, expected_version: int,
                     actor: Actor | None = None) -> MailTemplate:
    row = ensure_template(db, key)
    lock(db, MailTemplate, row.id, expected_version)
    default = _template_default(row.key)
    row.subject = default['subject']
    row.text_body = default['textBody']
    row.html_body = default.get('htmlBody') or ''
    row.enabled = bool(default.get('enabled', True))
    data = _template_data(row)
    data.update({
        'name': default['name'],
        'variables': [{'key': item, 'description': VARIABLE_DESCRIPTIONS.get(item, '')}
                      for item in default['variables']],
        'required': [item for item in (default.get('required') or default['variables'])
                     if item != 'logoUrl'],
        'critical': bool(default['critical']),
        'isDefault': True,
        'updatedBy': actor.id if actor else '系统默认',
    })
    row.data = data
    touch(row)
    db.flush()
    return row


def enqueue_email(db, key: str, to: str, variables: dict, business_key: str) -> EmailJob:
    """Persist one idempotent outbox row with a short-lived encrypted payload."""
    key = str(key or '').strip().upper()
    if not business_key or len(business_key) > 200:
        raise DomainError('MAIL_TEMPLATE_INVALID', '邮件业务唯一键无效')
    to = str(to or '').strip()
    if not _valid_email(to):
        raise DomainError('MAIL_INVALID_RECIPIENT', '收件邮箱格式无效', 422,
                          field_errors={'to': ['收件邮箱格式无效']})
    if not isinstance(variables, dict) or len(variables) > _MAX_PAYLOAD_VARIABLES:
        raise DomainError('MAIL_TEMPLATE_INVALID', '邮件变量无效')
    advisory_lock(db, 'email-outbox:' + business_key)
    existing = db.scalar(select(EmailJob).where(EmailJob.business_key == business_key))
    if existing:
        enqueue_job(db, 'EMAIL', existing.id, _worker_business_key(existing.business_key))
        return existing
    if not mail_available(db):
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮件服务暂不可用，请稍后重试', 503)
    row = ensure_template(db, key)
    if not row.enabled:
        raise DomainError('MAIL_TEMPLATE_INVALID', '该通知模板已停用', 422)
    template = _template_payload(row)
    if 'logoUrl' in _variables_for(template):
        variables = {**variables,
                     'logoUrl': f"{get_settings().public_origin.rstrip('/')}"
                                '/assets/icon-192.png'}
    rendered = render_template(template, variables)
    payload = {
        'to': to,
        'subject': rendered['subject'],
        'textBody': rendered['textBody'],
        'htmlBody': rendered['htmlBody'],
        'fromName': None,
    }
    job = EmailJob(
        business_key=business_key,
        template_key=row.key,
        status='QUEUED',
        protected_payload=seal(payload),
        attempts=0,
        available_at=utcnow(),
        expires_at=utcnow() + timedelta(hours=_ttl_hours()),
        data={
            'templateVersion': row.version,
            'recipientMasked': _mask_email(to),
            'variableKeys': sorted(variables),
            'lastErrorClass': None,
            'history': [{'status': 'QUEUED', 'at': utcnow().isoformat()}],
        },
    )
    db.add(job)
    db.flush()
    enqueue_job(db, 'EMAIL', job.id, _worker_business_key(job.business_key))
    return job


def _mask_email(value: str) -> str:
    local, _, domain = value.partition('@')
    return (local[:1] + '***@' + domain) if local else '***'


def _purge_payload(job: EmailJob, status: str, error: str | None = None) -> None:
    job.protected_payload = None
    data = dict(job.data or {})
    data['lastErrorClass'] = error
    data['purgedAt'] = utcnow().isoformat()
    history = list(data.get('history') or [])
    history.append({'status': status, 'at': utcnow().isoformat()})
    data['history'] = history[-20:]
    job.data = data
    job.status = status
    touch(job)
    session = object_session(job)
    if session is not None:
        session.flush()


def purge_expired_payloads(db, limit: int = 200) -> int:
    """Destroy protected send payloads whose retention window elapsed."""
    rows = db.scalars(select(EmailJob).where(
        EmailJob.expires_at <= utcnow(),
        EmailJob.protected_payload.is_not(None),
    ).order_by(EmailJob.expires_at).limit(limit).with_for_update(skip_locked=True)).all()
    for job in rows:
        _purge_payload(job, 'EXPIRED', 'MAIL_PAYLOAD_EXPIRED')
    return len(rows)


def process_email_job(db, job_id: str) -> None:
    """Send one queued job. Transaction ownership stays with the caller."""
    purge_expired_payloads(db, limit=50)
    job = lock(db, EmailJob, job_id)
    if job.status in ('SMTP_ACCEPTED', 'FAILED', 'EXPIRED', 'CANCELLED'):
        return
    now = utcnow()
    if job.status in ('RETRY', 'RETRY_PENDING') and job.available_at and job.available_at > now:
        # Root jobs requeue the same durable Job from EmailJob.available_at;
        # an early duplicate claim must not bypass the backoff or resend.
        return
    if job.expires_at and job.expires_at <= now:
        _purge_payload(job, 'EXPIRED', 'MAIL_PAYLOAD_EXPIRED')
        return
    if not job.protected_payload:
        _purge_payload(job, 'FAILED', 'MAIL_PAYLOAD_MISSING')
        return
    if not mail_available(db):
        _fail_or_retry(job, now, 'MAIL_CONFIG_INVALID')
        return
    try:
        payload = unseal(job.protected_payload)
        if not isinstance(payload, dict) or not payload.get('to'):
            raise DomainError('MAIL_TEMPLATE_INVALID', '邮件载荷无效')
        _, values = _config_values(db)
        _send(values, payload)
    except MailTransportError as error:
        _fail_or_retry(job, now, error.error_class)
        return
    except (ValueError, TypeError):
        _purge_payload(job, 'FAILED', 'MAIL_PAYLOAD_INVALID')
        return
    except DomainError:
        _purge_payload(job, 'FAILED', 'MAIL_PAYLOAD_INVALID')
        return
    job.attempts += 1
    job.status = 'SMTP_ACCEPTED'
    job.protected_payload = None
    data = dict(job.data or {})
    data['lastErrorClass'] = None
    data['smtpAcceptedAt'] = now.isoformat()
    data['deliveryStatus'] = 'UNKNOWN'
    history = list(data.get('history') or [])
    history.append({'status': 'SMTP_ACCEPTED', 'at': now.isoformat()})
    data['history'] = history[-20:]
    job.data = data
    touch(job)
    session = object_session(job)
    if session is not None:
        session.flush()


def _fail_or_retry(job: EmailJob, now, error_class: str) -> None:
    job.attempts += 1
    data = dict(job.data or {})
    data['lastErrorClass'] = error_class
    history = list(data.get('history') or [])
    if job.attempts >= _retry_max_attempts():
        job.status = 'FAILED'
        data['failedAt'] = now.isoformat()
        history.append({'status': 'FAILED', 'at': now.isoformat(), 'errorClass': error_class})
    else:
        job.status = 'RETRY'
        backoff = min(3600, 30 * (2 ** max(0, job.attempts - 1)))
        job.available_at = now + timedelta(seconds=backoff)
        history.append({'status': 'RETRY', 'at': now.isoformat(), 'errorClass': error_class})
    data['history'] = history[-20:]
    job.data = data
    touch(job)
    session = object_session(job)
    if session is not None:
        session.flush()


def test_connection(db) -> dict:
    _, values = _config_values(db)
    if not values['host'] or not (1 <= values['port'] <= 65535):
        raise DomainError('MAIL_CONFIG_INVALID', '请先填写完整的 SMTP 主机和端口')
    try:
        _connect_check(values)
    except MailTransportError as error:
        return {'status': 'FAIL', 'errorClass': error.error_class, 'message': str(error)}
    return {'status': 'PASS', 'errorClass': None,
            'message': 'DNS、TCP/TLS、EHLO 与认证检查通过；不代表邮件已送达'}


def _rate_limit(db, actor: Actor, action: str, limit: int = 5) -> None:
    since = utcnow() - timedelta(minutes=1)
    count = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.actor_id == actor.id,
        AuditLog.action == action,
        AuditLog.created_at >= since,
    ))
    if count and count >= limit:
        raise DomainError('MAIL_RATE_LIMITED', '测试发送过于频繁，请稍后再试', 429)


def _store_last_test(db, result: dict) -> dict:
    section = get_config(db, 'mail')
    data = _raw_data(section)
    data['lastTest'] = {
        'status': result['status'],
        'time': utcnow().isoformat(),
        'message': result.get('message', ''),
    }
    section.data = data
    flag_modified(section, 'data')
    db.flush()
    return {**result, 'testedAt': utcnow()}


def _client_ip(request) -> str:
    return request.client.host if request and request.client else ''


@router.get('/admin/config/mail', response_model=Envelope[MailConfigDTO])
def get_mail_config(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    return ok(mail_config_view(db))


def update_mail_config(db, payload: MailConfigInput,
                       actor: Actor | None = None) -> MailConfigDTO:
    section = get_config(db, 'mail')
    lock(db, ConfigSection, section.id, payload.expected_version)
    data = _raw_data(section)
    current_security = _security(data)
    if payload.clear_secret:
        data['secretCiphertext'] = None
    elif payload.secret:
        data['secretCiphertext'] = seal({'secret': payload.secret})
    if payload.security is not None and payload.security == 'NONE':
        if not get_settings().allow_insecure_smtp:
            raise DomainError('MAIL_CONFIG_INVALID', '当前部署未批准无加密 SMTP 连接')
    fields = {
        'enabled': payload.enabled,
        'host': (payload.host or '').strip() if payload.host is not None else None,
        'port': payload.port,
        'security': payload.security,
        'authEnabled': payload.auth_enabled,
        'username': payload.username,
        'fromName': payload.from_name,
        'fromAddress': payload.from_address,
        'replyTo': payload.reply_to,
        'timeoutSeconds': payload.timeout_seconds,
    }
    for name, value in fields.items():
        if value is not None:
            data[name] = value
    security = _security(data)
    data['security'] = security
    data['securityMode'] = security
    host_value = str(data.get('host') or '')
    if any(char.isspace() for char in host_value) or '/' in host_value or '://' in host_value:
        raise DomainError('MAIL_CONFIG_INVALID', 'SMTP 主机名格式无效', 422,
                          field_errors={'host': ['只填写主机名或 IP，不含协议或路径']})
    for header_field in ('fromName', 'username'):
        value = str(data.get(header_field) or '')
        if '\r' in value or '\n' in value:
            raise DomainError('MAIL_CONFIG_INVALID', '邮件头字段包含非法换行', 422,
                              field_errors={header_field: ['不能包含换行']})
    if data.get('fromAddress') and not _valid_email(str(data['fromAddress'])):
        raise DomainError('MAIL_CONFIG_INVALID', '发件邮箱格式无效', 422,
                          field_errors={'fromAddress': ['发件邮箱格式无效']})
    if data.get('replyTo') and not _valid_email(str(data['replyTo'])):
        raise DomainError('MAIL_CONFIG_INVALID', 'Reply-To 邮箱格式无效', 422,
                          field_errors={'replyTo': ['回复邮箱格式无效']})
    if (not payload.clear_secret and _auth_enabled(data)
            and not (data.get('username') and data.get('secretCiphertext'))):
        raise DomainError('MAIL_CONFIG_INVALID', '启用 SMTP 认证时必须配置账号与密钥')
    if (not payload.clear_secret and data.get('enabled')
            and (not data.get('host') or not data.get('port')
                 or not _valid_email(str(data.get('fromAddress') or '')))):
        raise DomainError('MAIL_CONFIG_INVALID', '启用邮件发送前必须填写完整主机、端口和发件邮箱')
    data['lastTest'] = None
    section.data = data
    touch(section)
    if actor is not None:
        audit(db, actor, 'MAIL_CONFIG_UPDATED', 'CONFIG', 'mail', reason=payload.reason,
              data={'version': section.version, 'secretChanged': bool(payload.secret),
                    'secretCleared': bool(payload.clear_secret), 'security': security,
                    'currentSecurity': current_security})
    db.flush()
    return mail_config_view(db)


@router.patch('/admin/config/mail', response_model=Envelope[MailConfigDTO])
def patch_mail_config(payload: MailConfigUpdate | MailConfigClear,
                      db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    normalized = MailConfigInput.model_validate(payload.model_dump(by_alias=True))
    return ok(update_mail_config(db, normalized, actor))


@router.post('/admin/mail/test-connection', response_model=Envelope[MailConnectionResult])
def post_test_connection(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    _rate_limit(db, actor, 'MAIL_TEST_CONNECTION')
    result = test_connection(db)
    result = _store_last_test(db, result)
    audit(db, actor, 'MAIL_TEST_CONNECTION', 'MAIL', 'config',
          data={'status': result['status'], 'errorClass': result.get('errorClass')})
    return ok(result)


@router.post('/admin/mail/test-send', response_model=Envelope[MailQueuedResult])
def post_test_send(payload: TestMailInput, db=Depends(get_db),
                   actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    _rate_limit(db, actor, 'MAIL_TEST_SEND')
    if not mail_available(db):
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮件服务未就绪，无法测试发送', 503)
    rows = seed_templates(db)
    template = next((item for item in rows if item.key == 'ORDER_CREATED'), rows[0])
    variables = {key: SAMPLE_VARIABLES[key] for key in
                 _variables_for(_template_data(template)) if key in SAMPLE_VARIABLES}
    job = enqueue_email(db, template.key, payload.to, variables,
                        business_key='mail-test-send:' + str(uuid4()))
    audit(db, actor, 'MAIL_TEST_SEND', 'EMAIL_JOB', job.id,
          data={'status': job.status, 'recipientMasked': _mask_email(payload.to)})
    return ok({
        'emailJobId': job.id, 'status': 'QUEUED', 'deliveryStatus': 'UNKNOWN',
        'message': '测试邮件已入队；SMTP 服务器接受不代表已送达收件箱',
    })


@router.get('/admin/mail/templates', response_model=Envelope[TemplateList])
def get_templates(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    rows = seed_templates(db)
    return ok({'items': [_template_view(row) for row in rows]})


@router.get('/admin/mail/templates/{key}', response_model=Envelope[MailTemplateDTO])
def get_template(key: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    return ok(_template_view(ensure_template(db, key)))


@router.patch('/admin/mail/templates/{key}', response_model=Envelope[MailTemplateDTO])
def patch_template(key: str, payload: TemplateInput, db=Depends(get_db),
                   actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    row = update_template(db, key, payload.model_dump(by_alias=True), payload.expected_version, actor)
    audit(db, actor, 'MAIL_TEMPLATE_UPDATED', 'MAIL_TEMPLATE', row.key, reason=payload.reason,
          data={'version': row.version, 'fields': ['subject', 'textBody', 'htmlBody', 'enabled']})
    return ok(_template_view(row))


@router.post('/admin/mail/templates/{key}/preview', response_model=Envelope[TemplatePreview])
def preview_template(key: str, payload: TemplatePreviewInput, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    row = ensure_template(db, key)
    data = _template_data(row)
    content = {
        'subject': payload.subject if payload.subject is not None else row.subject,
        'textBody': payload.text_body if payload.text_body is not None else row.text_body,
        'htmlBody': payload.html_body if payload.html_body is not None else (row.html_body or ''),
        'variables': data.get('variables') or [],
        'required': data.get('required'),
        'critical': bool(data.get('critical')),
    }
    validate_template_content(content, data.get('required') or _variables_for(content),
                              bool(data.get('critical')))
    variables = {item: SAMPLE_VARIABLES.get(item, '') for item in _variables_for(content)}
    rendered = render_template(content, variables)
    return ok(rendered)


@router.post('/admin/mail/templates/{key}/test-send', response_model=Envelope[MailQueuedResult])
def test_send_template(key: str, payload: TestMailInput, db=Depends(get_db),
                       actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    _rate_limit(db, actor, 'MAIL_TEST_SEND')
    if not mail_available(db):
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮件服务未就绪，无法测试发送', 503)
    row = ensure_template(db, key)
    variables = {item: SAMPLE_VARIABLES.get(item, '') for item in _template_data(row).get('variables', [])
                 if (item['key'] if isinstance(item, dict) else item) in SAMPLE_VARIABLES}
    job = enqueue_email(db, row.key, payload.to, variables,
                        business_key='mail-template-test:' + row.key + ':' + str(uuid4()))
    audit(db, actor, 'MAIL_TEMPLATE_TEST_SEND', 'MAIL_TEMPLATE', row.key,
          data={'emailJobId': job.id})
    return ok({'emailJobId': job.id, 'status': 'QUEUED', 'deliveryStatus': 'UNKNOWN',
               'message': '测试邮件已入队；SMTP 服务器接受不代表已送达收件箱'})


@router.post('/admin/mail/templates/{key}/restore-default',
             response_model=Envelope[MailTemplateDTO])
def restore_default_template(key: str, payload: MailReasonInput, db=Depends(get_db),
                             actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'mail.manage')
    row = restore_template(db, key, payload.expected_version, actor)
    audit(db, actor, 'MAIL_TEMPLATE_RESTORED', 'MAIL_TEMPLATE', row.key,
          reason=payload.reason, data={'version': row.version})
    return ok(_template_view(row))
