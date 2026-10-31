"""Mail module tests with a real local SMTP protocol fixture.

The fixture speaks SMTP over TCP and verifies TLS/AUTH classification paths.
It is deliberately not a claim that a production provider delivered mail.
"""
from datetime import timedelta
import socketserver
import threading

import pytest
from sqlalchemy import select

from app.models import ConfigSection, EmailJob, utcnow

APP_MODULES = ['growth', 'mail', 'auth']


class SMTPState:
    def __init__(self):
        self.messages = []
        self.auth_fail = False
        self.auth_plain = False
        self.greeted = threading.Event()

    def reset(self):
        self.messages.clear()
        self.auth_fail = False
        self.auth_plain = False


def make_handler(state):
    class Handler(socketserver.StreamRequestHandler):
        def send(self, text):
            self.wfile.write((text + '\r\n').encode())
            self.wfile.flush()

        def handle(self):
            self.send('220 dormprint-test ESMTP')
            data_mode = False
            buffer = []
            envelope = {'from': '', 'to': [], 'data': ''}
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                text = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if data_mode:
                    if text == '.':
                        data_mode = False
                        envelope['data'] = '\n'.join(buffer)
                        state.messages.append(dict(envelope))
                        buffer = []
                        envelope = {'from': '', 'to': [], 'data': ''}
                        self.send('250 2.0.0 queued')
                    else:
                        buffer.append(text[1:] if text.startswith('..') else text)
                    continue
                upper = text.upper()
                if upper.startswith('EHLO') or upper.startswith('HELO'):
                    state.greeted.set()
                    self.send('250-dormprint-test')
                    self.send('250-AUTH PLAIN LOGIN')
                    self.send('250 8BITMIME')
                elif upper.startswith('AUTH PLAIN'):
                    if state.auth_fail:
                        self.send('535 5.7.8 authentication failed')
                    else:
                        state.auth_plain = True
                        self.send('235 2.7.0 accepted')
                elif upper.startswith('AUTH LOGIN'):
                    self.send('334 VXNlcm5hbWU6')
                    self.rfile.readline()
                    self.send('334 UGFzc3dvcmQ6')
                    self.rfile.readline()
                    if state.auth_fail:
                        self.send('535 5.7.8 authentication failed')
                    else:
                        self.send('235 2.7.0 accepted')
                elif upper.startswith('MAIL FROM'):
                    envelope['from'] = text.split(':', 1)[1].strip(' <>')
                    self.send('250 2.1.0 ok')
                elif upper.startswith('RCPT TO'):
                    envelope['to'].append(text.split(':', 1)[1].strip(' <>'))
                    self.send('250 2.1.5 ok')
                elif upper == 'DATA':
                    data_mode = True
                    self.send('354 end with .')
                elif upper == 'RSET':
                    envelope = {'from': '', 'to': [], 'data': ''}
                    self.send('250 ok')
                elif upper == 'QUIT':
                    self.send('221 bye')
                    break
                else:
                    self.send('250 ok')

    return Handler


@pytest.fixture
def smtp_server():
    state = SMTPState()
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), make_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def settings_allow_local(monkeypatch):
    from app.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, 'allow_insecure_smtp', True, raising=False)
    monkeypatch.setattr(settings, 'mail_allowed_hosts', '127.0.0.1', raising=False)
    monkeypatch.setattr(settings, 'mail_timeout_seconds', 5, raising=False)
    return settings


def seed_mail_config(db, port, auth=True, secret='top-secret-token', enabled=True):
    from app.core import seal

    data = {
        'version': 1,
        'enabled': enabled,
        'host': '127.0.0.1',
        'port': port,
        'security': 'NONE',
        'authEnabled': auth,
        'username': 'mailer@example.invalid',
        'fromName': 'DormPrint 测试',
        'fromAddress': 'mailer@example.invalid',
        'replyTo': '',
        'timeoutSeconds': 5,
        'allowInsecure': True,
        'secretCiphertext': seal({'secret': secret}) if secret else None,
        'lastTest': {'status': 'PASS', 'time': utcnow().isoformat(), 'message': ''},
    }
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    if row is None:
        row = ConfigSection(section='mail', data=data, version=1)
        db.add(row)
    else:
        row.data = data
        row.version = 1
    db.flush()
    return row


def test_chinese_defaults_cover_transactional_and_staff_mail():
    from app.mail import DEFAULT_TEMPLATES

    assert list(DEFAULT_TEMPLATES) == [
        'CUSTOMER_VERIFY_EMAIL', 'CUSTOMER_RESET_PASSWORD',
        'ADMIN_ORDER_CREATED', 'COURIER_DISPATCH', 'DELIVERY_UPDATED',
        'PAYMENT_SUCCESS', 'ORDER_DELIVERED', 'ACCOUNT_DELETED',
        'ORDER_CREATED', 'ORDER_STATUS_UPDATED', 'REFERRAL_REWARD_GRANTED',
    ]
    for key, template in DEFAULT_TEMPLATES.items():
        assert template['subject'], key
        assert template['textBody'], key
        assert '{{' in template['textBody']
        assert template['variables'], key
    assert '{{actionUrl}}' in DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['textBody']
    assert '{{customerPhone}}' in DEFAULT_TEMPLATES['ADMIN_ORDER_CREATED']['textBody']
    assert '{{orderCount}}' in DEFAULT_TEMPLATES['COURIER_DISPATCH']['textBody']
    assert '{{paidAmount}}' in DEFAULT_TEMPLATES['PAYMENT_SUCCESS']['textBody']
    assert DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['critical'] is True
    assert DEFAULT_TEMPLATES['PAYMENT_SUCCESS']['critical'] is False


def test_render_escapes_html_and_rejects_missing_or_unknown_variables():
    from app.mail import render_template
    from app.core import DomainError

    template = {
        'subject': '{{brandName}}｜订单 {{orderCode}}',
        'textBody': '你好 {{brandName}}，查看 {{orderLink}}',
        'htmlBody': '<p>{{brandName}} {{orderLink}}</p>',
        'variables': ['brandName', 'orderCode', 'orderLink'],
        'critical': False,
    }
    rendered = render_template(template, {
        'brandName': 'DormPrint <script>alert(1)</script>',
        'orderCode': 'ABC123',
        'orderLink': 'https://example.invalid/o/1?x=<b>',
    })
    assert '<script>' not in rendered['htmlBody']
    assert '&lt;script&gt;' in rendered['htmlBody']
    assert 'https://example.invalid/o/1?x=' in rendered['htmlBody']
    assert 'DormPrint' in rendered['subject']
    with pytest.raises(DomainError) as missing:
        render_template(template, {'brandName': 'x'})
    assert missing.value.code == 'MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING'
    with pytest.raises(DomainError):
        render_template(template, {'brandName': 'x', 'orderCode': '1',
                                   'orderLink': 'x', 'evil': 'y'})


def test_html_validation_rejects_scripts_handlers_and_remote_images():
    from app.mail import validate_template_content
    from app.core import DomainError

    template = {
        'subject': '测试', 'textBody': '正文 {{brandName}}',
        'htmlBody': '<p>{{brandName}}</p>',
        'variables': ['brandName'], 'critical': False,
    }
    for bad in [
        '<script>alert(1)</script>',
        '<p onclick="x()">x</p>',
        '<a href="javascript:alert(1)">x</a>',
        '<img src="https://tracker.invalid/pixel">',
        '<iframe src="https://evil.invalid"></iframe>',
    ]:
        with pytest.raises(DomainError) as error:
            validate_template_content({**template, 'htmlBody': bad})
        assert error.value.code == 'MAIL_TEMPLATE_INVALID'
    with pytest.raises(DomainError):
        validate_template_content({**template, 'textBody': '缺少必要变量'})


def test_html_validation_accepts_only_the_fixed_brand_logo_image():
    from app.mail import validate_template_content
    from app.core import DomainError

    template = {
        'subject': '测试', 'textBody': '正文 {{brandName}}',
        'htmlBody': '<p>{{brandName}}</p>',
        'variables': ['brandName', 'logoUrl'], 'critical': False,
    }
    good = ('<p><img src="{{logoUrl}}" width="30" height="30" alt="" '
            'style="border:0;border-radius:8px"></p>')
    validate_template_content({**template, 'htmlBody': good})
    for bad in [
        '<img src="https://tracker.invalid/pixel">',
        '<img src="//evil.invalid/logo.png">',
        '<img src="{{brandName}}">',
        '<img src="data:image/png;base64,AAAA">',
    ]:
        with pytest.raises(DomainError) as error:
            validate_template_content({**template, 'htmlBody': bad})
        assert error.value.code == 'MAIL_TEMPLATE_INVALID'


def test_default_html_templates_are_styled_and_render_cleanly():
    from app.mail import (DEFAULT_TEMPLATES, SAMPLE_VARIABLES, render_template,
                          validate_template_content)

    for key, template in DEFAULT_TEMPLATES.items():
        html = template['htmlBody']
        assert html.startswith('<table'), key
        assert '{{brandName}}' in html
        validate_template_content(template)
        variables = {item: SAMPLE_VARIABLES[item] for item in template['variables']}
        rendered = render_template(template, variables)
        assert '{{' not in rendered['htmlBody'], key
        assert rendered['htmlBody'].count('<table') >= 2, key
        assert 'DormPrint 示例' in rendered['htmlBody'], key
    assert '{{actionUrl}}' in DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['htmlBody']
    assert '{{orderLink}}' in DEFAULT_TEMPLATES['ORDER_CREATED']['htmlBody']


def test_html_validation_accepts_layout_tables_and_blocks_active_markup():
    from app.mail import validate_template_content
    from app.core import DomainError

    base = {
        'subject': '测试', 'textBody': '正文 {{brandName}} {{actionUrl}}',
        'htmlBody': '', 'variables': ['brandName', 'actionUrl'],
        'critical': True,
    }
    good = (
        '<table role="presentation" width="600" cellpadding="0" '
        'cellspacing="0" border="0" style="background:#fff">'
        '<tr><td align="center">'
        '<a href="{{actionUrl}}" style="color:#c67c4e">继续</a>'
        '</td></tr></table>'
    )
    validate_template_content({**base, 'htmlBody': good})
    for bad in [
        '<a href="//evil.invalid/x">x</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>',
        '<div style="background-image:url(https://evil.invalid/p)">x</div>',
        '<div style="position: fixed;top:0">x</div>',
        '<p class="hero">x</p>',
        '<a href="https://example.invalid" target="_blank">x</a>',
        '<svg><rect></rect></svg>',
    ]:
        with pytest.raises(DomainError) as error:
            validate_template_content({**base, 'htmlBody': bad})
        assert error.value.code == 'MAIL_TEMPLATE_INVALID'


def test_seed_backfills_html_only_for_untouched_defaults(db):
    from app.mail import DEFAULT_TEMPLATES, seed_templates
    from app.models import MailTemplate

    legacy = MailTemplate(
        key='ORDER_CREATED', subject='旧主题 {{brandName}}',
        text_body='旧正文 {{brandName}}', html_body='<p>旧默认 HTML</p>', enabled=True,
        data={'name': '下单回执', 'variables': [{'key': 'brandName'}],
              'isDefault': True, 'critical': False},
        version=3,
    )
    edited = MailTemplate(
        key='CUSTOMER_VERIFY_EMAIL', subject='自定义',
        text_body='自定义 {{brandName}}', html_body='', enabled=True,
        data={'name': '验证邮箱', 'variables': [{'key': 'brandName'}],
              'isDefault': False, 'critical': True},
        version=2,
    )
    db.add_all([legacy, edited])
    db.flush()
    seed_templates(db)
    db.refresh(legacy)
    db.refresh(edited)
    assert legacy.html_body == DEFAULT_TEMPLATES['ORDER_CREATED']['htmlBody']
    assert '{{logoUrl}}' in legacy.html_body
    assert legacy.subject == '旧主题 {{brandName}}'
    assert legacy.version == 3
    assert edited.html_body == ''
    assert edited.version == 2


def test_mail_available_requires_valid_enabled_config(db, smtp_server, settings_allow_local):
    from app.mail import mail_available

    state, port = smtp_server
    assert mail_available(db) is False
    seed_mail_config(db, port)
    assert mail_available(db) is True
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    row.data = {**row.data, 'enabled': False}
    db.flush()
    assert mail_available(db) is False


def test_enqueue_is_idempotent_encrypts_payload_and_hides_contact(db, smtp_server,
                                                                  settings_allow_local):
    from app.mail import enqueue_email, seed_templates

    state, port = smtp_server
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-test-1')
    again = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-test-1')
    assert job.id == again.id
    assert job.protected_payload
    assert 'student@example.invalid' not in job.protected_payload
    assert 'example.invalid/orders/1' not in job.protected_payload
    assert 'student' not in str(job.data)
    assert job.status == 'QUEUED'


def test_enqueue_injects_absolute_brand_logo_url(db, smtp_server,
                                                 settings_allow_local):
    from app.core import unseal
    from app.mail import enqueue_email, seed_templates
    from app.settings import get_settings

    state, port = smtp_server
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'CUSTOMER_VERIFY_EMAIL', 'student@example.invalid', {
        'brandName': 'DormPrint',
        'actionUrl': 'https://example.invalid/verify#token=secret-token-value',
        'expiresMinutes': '30',
    }, business_key='mail-logo-1')
    payload = unseal(job.protected_payload)
    origin = get_settings().public_origin.rstrip('/')
    assert f'{origin}/assets/icon-192.png' in payload['htmlBody']


def test_process_email_job_really_sends_and_destroys_secret_payload(db, smtp_server,
                                                                   settings_allow_local):
    from app.mail import enqueue_email, process_email_job, seed_templates

    state, port = smtp_server
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'CUSTOMER_VERIFY_EMAIL', 'student@example.invalid', {
        'brandName': 'DormPrint',
        'actionUrl': 'https://example.invalid/verify#token=secret-token-value',
        'expiresMinutes': '30',
    }, business_key='mail-send-1')
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status == 'SMTP_ACCEPTED'
    assert job.protected_payload is None
    assert len(state.messages) == 1
    message = state.messages[0]
    assert message['to'] == ['student@example.invalid']
    assert 'secret-token-value' in message['data']
    assert 'Subject:' in message['data'] or 'subject:' in message['data']
    assert state.auth_plain is True
    assert 'secret-token-value' not in str(job.data)
    assert 'student@example.invalid' not in str(job.data)


def test_auth_failure_is_classified_without_creating_secret_leak(db, smtp_server,
                                                                 settings_allow_local):
    from app.mail import enqueue_email, process_email_job, seed_templates

    state, port = smtp_server
    state.auth_fail = True
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-auth-fail-1')
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status in ('RETRY', 'FAILED')
    assert job.data['lastErrorClass'] == 'MAIL_AUTH_FAILED'
    assert 'top-secret-token' not in str(job.data)
    assert job.protected_payload is not None


def test_expired_payload_is_destroyed_without_sending(db, smtp_server, settings_allow_local):
    from app.mail import enqueue_email, process_email_job, seed_templates

    state, port = smtp_server
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-expired-1')
    job.expires_at = utcnow() - timedelta(minutes=1)
    db.flush()
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.protected_payload is None
    assert job.status in ('EXPIRED', 'FAILED')
    assert state.messages == []


def test_ssrf_guard_blocks_loopback_by_default():
    from app.mail import is_safe_smtp_target
    from app.settings import get_settings

    settings = get_settings()
    assert is_safe_smtp_target('127.0.0.1', settings) is False
    assert is_safe_smtp_target('localhost', settings) is False
    assert is_safe_smtp_target('169.254.169.254', settings) is False


def test_default_template_restore_and_edit_validation(db):
    from app.mail import (DEFAULT_TEMPLATES, ensure_template, restore_template,
                          update_template)
    from app.core import DomainError

    seed = ensure_template(db, 'CUSTOMER_VERIFY_EMAIL')
    assert seed.subject == DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['subject']
    with pytest.raises(DomainError) as error:
        update_template(db, 'CUSTOMER_VERIFY_EMAIL', {
            'subject': 'x', 'textBody': '没有链接', 'htmlBody': '',
            'enabled': True,
        }, expected_version=seed.version)
    assert error.value.code == 'MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING'
    with pytest.raises(DomainError):
        update_template(db, 'CUSTOMER_VERIFY_EMAIL', {
            'subject': 'x', 'textBody': '{{brandName}} {{actionUrl}} {{expiresMinutes}}',
            'htmlBody': '<script>bad</script>', 'enabled': True,
        }, expected_version=seed.version)
    updated = update_template(db, 'CUSTOMER_VERIFY_EMAIL', {
        'subject': '新主题 {{brandName}}',
        'textBody': DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['textBody'],
        'htmlBody': '<p>安全 {{brandName}}</p>', 'enabled': True,
    }, expected_version=seed.version)
    version_after_update = updated.version
    restored = restore_template(db, 'CUSTOMER_VERIFY_EMAIL',
                                expected_version=version_after_update)
    assert restored.subject == DEFAULT_TEMPLATES['CUSTOMER_VERIFY_EMAIL']['subject']
    assert restored.version == version_after_update + 1


# ---------------------------------------------------------------------------
# HTTP routes: permissions, secret non-disclosure, template safety
# ---------------------------------------------------------------------------


def mail_patch_payload(version, **overrides):
    payload = {
        'expectedVersion': version, 'enabled': True,
        'host': 'smtp.example.invalid', 'port': 587, 'security': 'STARTTLS',
        'authEnabled': True, 'username': 'mailer@example.invalid',
        'secret': 'smtp-secret-value', 'fromName': 'DormPrint',
        'fromAddress': 'mailer@example.invalid', 'replyTo': '',
        'timeoutSeconds': 10, 'reason': '配置测试',
    }
    payload.update(overrides)
    return payload


def test_mail_config_domain_update_never_returns_secret(db):
    from app.mail import mail_config_view, update_mail_config
    from app.mail_schemas import MailConfigInput
    from app.core import DomainError

    current = mail_config_view(db)
    updated = update_mail_config(db, MailConfigInput.model_validate(
        mail_patch_payload(current.version)))
    assert updated.version == current.version + 1
    assert updated.secret_configured is True
    assert 'smtp-secret-value' not in updated.model_dump_json()
    with pytest.raises(DomainError) as conflict:
        update_mail_config(db, MailConfigInput.model_validate(
            mail_patch_payload(current.version)))
    assert conflict.value.code == 'VERSION_CONFLICT'
    cleared = update_mail_config(db, MailConfigInput.model_validate({
        'expectedVersion': updated.version, 'clearSecret': True,
        'reason': '清除测试密钥',
    }))
    assert cleared.secret_configured is False


def test_mail_config_route_is_admin_only(admin_client, client, api):
    response = api(admin_client, '/admin/config/mail', method='GET')
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['secretConfigured'] is False
    denied = api(client, '/admin/config/mail', method='GET')
    assert denied.status_code in (401, 403)


def test_template_routes_list_all_and_preview_uses_safe_sample(admin_client, api):
    listing = api(admin_client, '/admin/mail/templates', method='GET')
    assert listing.status_code == 200, listing.text
    items = listing.json()['data']['items']
    assert len(items) == 11
    verify = next(item for item in items if item['key'] == 'CUSTOMER_VERIFY_EMAIL')
    assert verify['critical'] is True
    preview = api(admin_client,
                  '/admin/mail/templates/CUSTOMER_VERIFY_EMAIL/preview',
                  {'subject': None, 'textBody': None, 'htmlBody': None})
    assert preview.status_code == 200, preview.text
    rendered = preview.json()['data']
    assert 'example.invalid' in rendered['textBody']
    assert '#token=preview-not-usable' in rendered['textBody']
    assert 'DormPrint' in rendered['subject']


def test_template_patch_rejects_missing_security_placeholder(admin_client, api):
    listing = api(admin_client, '/admin/mail/templates', method='GET').json()['data']['items']
    verify = next(item for item in listing if item['key'] == 'CUSTOMER_VERIFY_EMAIL')
    response = api(admin_client,
                   '/admin/mail/templates/CUSTOMER_VERIFY_EMAIL',
                   {'expectedVersion': verify['version'], 'subject': '坏模板',
                    'textBody': '没有一次性链接', 'htmlBody': '', 'enabled': True,
                    'reason': '测试校验'}, method='PATCH')
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING'


def test_test_send_without_config_is_unavailable_not_fake_success(admin_client, api):
    response = api(admin_client, '/admin/mail/test-send',
                   {'to': 'admin@example.invalid'})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'EMAIL_SERVICE_UNAVAILABLE'


def test_connection_route_blocks_ssrf_target_and_does_not_leak_secret(
        admin_client, db, api):
    from app.mail import update_mail_config
    from app.mail_schemas import MailConfigInput

    current = update_mail_config(db, MailConfigInput.model_validate(
        mail_patch_payload(1, host='127.0.0.1', port=25, security='STARTTLS',
                           authEnabled=False, secret=None)))
    assert current.enabled is True
    db.commit()
    response = api(admin_client, '/admin/mail/test-connection', {})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'FAIL'
    assert data['errorClass'] == 'MAIL_SSRF_BLOCKED'
    assert 'smtp-secret-value' not in response.text


def test_template_restore_route_uses_shared_reason_input(admin_client, api):
    listing = api(admin_client, '/admin/mail/templates', method='GET').json()['data']['items']
    verify = next(item for item in listing if item['key'] == 'CUSTOMER_RESET_PASSWORD')
    missing = api(admin_client,
                  '/admin/mail/templates/CUSTOMER_RESET_PASSWORD/restore-default',
                  {'reason': '缺少版本'})
    assert missing.status_code == 422
    restored = api(admin_client,
                   '/admin/mail/templates/CUSTOMER_RESET_PASSWORD/restore-default',
                   {'expectedVersion': verify['version'], 'reason': '恢复默认'})
    assert restored.status_code == 200, restored.text
    assert restored.json()['data']['version'] == verify['version'] + 1


def test_starttls_required_but_missing_is_classified(db, smtp_server, settings_allow_local):
    from app.mail import enqueue_email, process_email_job, seed_templates

    state, port = smtp_server
    seed_mail_config(db, port)
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    row.data = {**row.data, 'security': 'STARTTLS', 'authEnabled': False,
                'secretCiphertext': None}
    db.flush()
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-tls-missing-1')
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status in ('RETRY', 'FAILED')
    assert job.data['lastErrorClass'] == 'MAIL_TLS_FAILED'
    assert state.messages == []


def test_dns_failure_is_classified_and_retried(db, settings_allow_local):
    from app.mail import enqueue_email, process_email_job, seed_templates

    seed_mail_config(db, 25, auth=False, secret=None)
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    row.data = {**row.data, 'host': 'a' * 64 + '.invalid', 'security': 'STARTTLS'}
    db.flush()
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-dns-failure-1')
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status == 'RETRY'
    assert job.data['lastErrorClass'] == 'MAIL_DNS_FAILED'
    assert job.protected_payload is not None


def test_enqueue_creates_worker_job_and_root_jobs_reuses_it_for_retry(
        db, smtp_server, settings_allow_local):
    from sqlalchemy import func as sql_func, select as sql_select
    from app.mail import enqueue_email, process_email_job, seed_templates
    from app.models import Job

    state, port = smtp_server
    state.auth_fail = True
    seed_mail_config(db, port)
    seed_templates(db)
    job = enqueue_email(db, 'ORDER_CREATED', 'student@example.invalid', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/orders/1',
    }, business_key='mail-worker-1')
    worker = db.scalar(sql_select(Job).where(Job.business_key == 'email:mail-worker-1'))
    assert worker is not None and worker.kind == 'EMAIL' and worker.target_id == job.id
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status == 'RETRY'
    assert job.available_at > utcnow()
    assert job.attempts == 1
    assert state.messages == []
    # Root jobs requeues this same Job using EmailJob.available_at; no second
    # durable retry Job and an early duplicate claim cannot bypass backoff.
    assert db.scalar(sql_select(sql_func.count()).select_from(Job).where(
        Job.kind == 'EMAIL')) == 1
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.attempts == 1 and state.messages == []
    state.auth_fail = False
    job.available_at = utcnow() - timedelta(seconds=1)
    db.flush()
    process_email_job(db, job.id)
    db.refresh(job)
    assert job.status == 'SMTP_ACCEPTED' and job.attempts == 2
    assert len(state.messages) == 1
    process_email_job(db, job.id)
    assert len(state.messages) == 1
    assert db.scalar(sql_select(sql_func.count()).select_from(Job).where(
        Job.kind == 'EMAIL')) == 1


def test_admin_can_resend_pending_verification_without_fake_success(
        admin_client, account_factory, db, api, smtp_server, settings_allow_local):
    from sqlalchemy import select as sql_select
    from app.models import EmailJob, OneTimeToken

    state, port = smtp_server
    seed_mail_config(db, port)
    account = account_factory(email='pending-admin@example.invalid', verified=False,
                              invite_code='PENDINGINV')
    db.commit()
    response = api(admin_client,
                   f'/admin/customers/{account.id}/resend-verification',
                   {'expectedVersion': account.version, 'reason': '支持客户验证'})
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['status'] == 'QUEUED'
    job = db.get(EmailJob, body['emailJobId'])
    assert job is not None and job.status == 'QUEUED'
    assert job.template_key == 'CUSTOMER_VERIFY_EMAIL'
    token = db.scalar(sql_select(OneTimeToken).where(
        OneTimeToken.kind == 'REGISTRATION', OneTimeToken.account_id == account.id))
    assert token is not None and token.consumed_at is None
    assert 'PENDINGINV' not in str(job.data)


def test_admin_recovery_requires_verified_account(admin_client, account_factory,
                                                  db, api):
    account = account_factory(email='unverified-recovery@example.invalid',
                              verified=False, invite_code='RECOVERYINV')
    db.commit()
    response = api(admin_client, f'/admin/customers/{account.id}/send-recovery',
                   {'expectedVersion': account.version, 'reason': '客户申请'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'EMAIL_NOT_VERIFIED'


def test_smtp_secret_is_sealed_at_rest_not_plaintext(db):
    from app.mail import update_mail_config
    from app.mail_schemas import MailConfigInput
    from app.core import unseal

    current = update_mail_config(db, MailConfigInput.model_validate(
        mail_patch_payload(1)))
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    assert 'smtp-secret-value' not in str(row.data)
    sealed = row.data['secretCiphertext']
    assert sealed and sealed != 'smtp-secret-value'
    assert unseal(sealed)['secret'] == 'smtp-secret-value'
    assert current.secret_configured is True


def test_mail_config_rejects_plaintext_insecure_mode_without_deployment_approval(db):
    from app.mail import update_mail_config
    from app.mail_schemas import MailConfigInput
    from app.core import DomainError

    with pytest.raises(DomainError) as error:
        update_mail_config(db, MailConfigInput.model_validate(
            mail_patch_payload(1, security='NONE')))
    assert error.value.code == 'MAIL_CONFIG_INVALID'


def test_mail_config_patch_route_seals_secret_and_is_versioned(admin_client, api):
    current = api(admin_client, '/admin/config/mail', method='GET').json()['data']
    payload = mail_patch_payload(current['version'])
    updated = api(admin_client, '/admin/config/mail', payload, method='PATCH')
    assert updated.status_code == 200, updated.text
    body = updated.json()['data']
    assert body['version'] == current['version'] + 1
    assert body['secretConfigured'] is True
    assert 'smtp-secret-value' not in updated.text
    conflict = api(admin_client, '/admin/config/mail',
                   mail_patch_payload(current['version']), method='PATCH')
    assert conflict.status_code == 409
    cleared = api(admin_client, '/admin/config/mail', {
        'expectedVersion': body['version'], 'clearSecret': True,
        'reason': '清除接口密钥'}, method='PATCH')
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['data']['secretConfigured'] is False


def test_connection_test_uses_real_smtp_ehlo_and_auth(db, smtp_server,
                                                      settings_allow_local):
    from app.mail import test_connection

    state, port = smtp_server
    seed_mail_config(db, port)
    result = test_connection(db)
    assert result['status'] == 'PASS', result
    assert state.greeted.is_set()
    assert state.auth_plain is True
    assert result['errorClass'] is None


def test_optional_notification_requires_subscription_and_uses_safe_payload(
        db, smtp_server, settings_allow_local, account_factory):
    from app.growth import enqueue_notification_email

    state, port = smtp_server
    seed_mail_config(db, port)
    account = account_factory(email='subscribed@example.invalid',
                              invite_code='SUBSCRIBE1',
                              preferences={'orderCreated': True,
                                           'orderStatusUpdated': False,
                                           'referralReward': False})
    job = enqueue_notification_email(db, account, 'ORDER_CREATED', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/u/orders/1',
    }, 'notification-subscribed-1')
    assert job is not None and job.template_key == 'ORDER_CREATED'
    assert job.status == 'QUEUED'
    other = enqueue_notification_email(db, account, 'ORDER_STATUS_UPDATED', {
        'brandName': 'DormPrint', 'orderCode': 'ABC123',
        'orderStatus': '待确认', 'orderLink': 'https://example.invalid/u/orders/1',
    }, 'notification-unsubscribed-2')
    assert other is None


def test_courier_cannot_read_or_write_mail_admin(client_factory, staff_factory, db, api):
    from datetime import timedelta
    from app.core import token_hash
    from app.models import Account, AuthSession

    staff = staff_factory(role='COURIER', capabilities=[])
    assert staff.account_id, 'staff_factory must link a verified customer account'
    account = db.get(Account, staff.account_id)
    assert account is not None and account.verified_at is not None and account.status == 'ACTIVE'
    token = 'courier-mail-token'
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account
    denied = api(client, '/admin/config/mail', method='GET')
    assert denied.status_code == 403
    denied_write = api(client, '/admin/mail/templates', method='GET')
    assert denied_write.status_code == 403


def test_failed_connection_test_disables_registration_availability(db, smtp_server,
                                                                   settings_allow_local):
    from app.mail import mail_available, test_connection

    state, port = smtp_server
    seed_mail_config(db, port)
    state.auth_fail = True
    result = test_connection(db)
    assert result['status'] == 'FAIL'
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    row.data = {**row.data, 'lastTest': {
        'status': 'FAIL', 'time': utcnow().isoformat(), 'message': result['message']}}
    db.flush()
    assert mail_available(db) is False


def _order_stub(account, status='PENDING_PRINT', version=3):
    from types import SimpleNamespace
    return SimpleNamespace(
        checkout_account_id=account.id, id='order-notify-1',
        pickup_code='ABC123', status=status, version=version,
        total_cents=3600, cash_received_cents=3600,
        nickname='测试同学', phone='13800000000', location_id=None,
        data={'deliveredAt': utcnow().isoformat()},
    )


def test_new_order_notifies_permitted_staff_not_customer(
        db, smtp_server, settings_allow_local, account_factory, staff_factory):
    from sqlalchemy import func as sql_func, select as sql_select
    from app.growth import notify_order_event
    from app.models import EmailJob, Job

    state, port = smtp_server
    seed_mail_config(db, port)
    customer = account_factory(
        email='notify-customer@example.invalid', invite_code='NOTIFYCUST1',
        preferences={'orderCreated': True, 'orderStatusUpdated': True,
                     'referralReward': False})
    order = _order_stub(customer)
    orders_staff = staff_factory(
        username='orders_mail', email='orders-mail@example.invalid',
        email_verified=True, capabilities=['orders.manage'])
    production = staff_factory(
        username='prod_mail', email='prod-mail@example.invalid',
        email_verified=True, capabilities=['production.manage'])
    staff_factory(role='COURIER', username='courier_mail',
                  email='courier-mail@example.invalid', email_verified=True,
                  capabilities=['delivery.manage'])
    jobs = notify_order_event(db, order, 'CREATED')
    assert isinstance(jobs, list) and len(jobs) == 2
    assert {job.template_key for job in jobs} == {'ADMIN_ORDER_CREATED'}
    assert {job.business_key for job in jobs} == {
        f'admin-order-email:{order.id}:{orders_staff.id}',
        f'admin-order-email:{order.id}:{production.id}',
    }
    assert db.scalar(sql_select(sql_func.count()).select_from(EmailJob).where(
        EmailJob.business_key.like('order-email:%'))) == 0
    again = notify_order_event(db, order, 'CREATED')
    assert {job.id for job in again} == {job.id for job in jobs}
    assert db.scalar(sql_select(sql_func.count()).select_from(Job).where(
        Job.kind == 'EMAIL')) == 2
    assert notify_order_event(db, order, 'STATUS_UPDATED') is None


def test_notify_order_mail_failure_does_not_rollback_order_work(
        db, account_factory, staff_factory):
    from sqlalchemy import func as sql_func, select as sql_select
    from app.growth import notify_order_event
    from app.models import EmailJob

    customer = account_factory(
        email='notify-fail@example.invalid', invite_code='NOTIFYFAIL1',
        preferences={'orderCreated': True})
    staff_factory(username='fail_mail', email='fail-mail@example.invalid',
                  email_verified=True, capabilities=['orders.manage'])
    customer.display_name = '事务内修改'
    db.flush()
    before = db.scalar(sql_select(sql_func.count()).select_from(EmailJob))
    assert notify_order_event(db, _order_stub(customer), 'CREATED') == []
    assert customer.display_name == '事务内修改'
    assert db.scalar(sql_select(sql_func.count()).select_from(EmailJob)) == before


def test_payment_delivery_and_account_mail_are_transactional(
        db, smtp_server, settings_allow_local, account_factory):
    from app.growth import (
        notify_account_deleted, notify_order_delivered, notify_payment_success,
    )

    state, port = smtp_server
    seed_mail_config(db, port)
    account = account_factory(email='txn-mail@example.invalid',
                              invite_code='TXNMAIL1', preferences={})
    order = _order_stub(account, version=7)
    payment = notify_payment_success(db, order)
    assert payment is not None and payment.template_key == 'PAYMENT_SUCCESS'
    assert f'PAYMENT_SUCCESS:{order.id}:7' in payment.business_key
    assert notify_payment_success(db, order).id == payment.id
    delivered = notify_order_delivered(db, order, delivered_at=utcnow())
    assert delivered is not None and delivered.template_key == 'ORDER_DELIVERED'
    assert 'ORDER_DELIVERED:order-notify-1:7' in delivered.business_key
    deleted = notify_account_deleted(db, account, utcnow() + timedelta(days=14))
    assert deleted is not None and deleted.template_key == 'ACCOUNT_DELETED'


def test_courier_dispatch_goes_only_to_the_assigned_courier(
        db, smtp_server, settings_allow_local, staff_factory):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.growth import notify_courier_dispatch

    state, port = smtp_server
    seed_mail_config(db, port)
    courier = staff_factory(
        role='COURIER', username='dispatch_courier',
        email='dispatch@example.invalid', email_verified=True)
    staff_factory(role='COURIER', username='other_courier',
                  email='other@example.invalid', email_verified=True)
    batch_id = str(uuid4())
    batch = SimpleNamespace(
        id=batch_id, name='晚班', starts_at=utcnow(),
        ends_at=utcnow() + timedelta(hours=2), courier_id=courier.id,
        version=2)
    job = notify_courier_dispatch(db, batch)
    assert job is not None and job.template_key == 'COURIER_DISPATCH'
    assert job.business_key == f'courier-dispatch:{batch_id}:2'
    assert notify_courier_dispatch(db, batch).id == job.id
