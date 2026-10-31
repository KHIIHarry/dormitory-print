"""Branded HTML bodies for the seven built-in transactional templates.

The markup deliberately stays inside the bounded rich-text subset accepted by
``app.mail.validate_template_content``: layout tables, inline styles, plain text
and links. There are no images, remote fonts, scripts or tracking pixels, so the
mail survives Gmail/QQ/163 sanitising and the admin preview CSP
(``default-src 'none'; style-src 'unsafe-inline'``).

Keep this module dependency-free: it is imported while ``app.mail`` builds its
default templates, and the frontend demo mirrors the same markup.
"""
from __future__ import annotations

BRAND = '{{brandName}}'
ACTION = '{{actionUrl}}'
EXPIRES = '{{expiresMinutes}}'
ORDER_CODE = '{{orderCode}}'
ORDER_STATUS = '{{orderStatus}}'
ORDER_LINK = '{{orderLink}}'
ORDER_AMOUNT = '{{orderAmount}}'
PAID_AMOUNT = '{{paidAmount}}'
CUSTOMER_NICKNAME = '{{customerNickname}}'
CUSTOMER_PHONE = '{{customerPhone}}'
COURIER_NAME = '{{courierName}}'
COURIER_LINK = '{{courierLink}}'
BATCH_NAME = '{{batchName}}'
DELIVERY_DATE = '{{deliveryDate}}'
DELIVERY_SLOT = '{{deliverySlot}}'
DELIVERY_LOCATION = '{{deliveryLocation}}'
DELIVERED_AT = '{{deliveredAt}}'
ORDER_COUNT = '{{orderCount}}'
DELETION_DEADLINE = '{{deletionDeadline}}'
REWARD = '{{rewardText}}'

INK = '#2f2b26'
BODY = '#57514a'
MUTED = '#8d8478'
FAINT = '#a99f92'
CLAY = '#c67c4e'
CLAY_DARK = '#a65c31'
CREAM = '#faf3ec'
PAPER = '#fffdfa'
CANVAS = '#f4eee6'
HEADER = '#fbf6f0'
LINE = '#efe5d8'
BORDER = '#eadfd2'

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
        "Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif")


def _font(size: int, color: str = BODY, weight: int = 400,
          line: float = 1.7) -> str:
    return (f'font-family:{FONT};font-size:{size}px;line-height:{line};'
            f'font-weight:{weight};color:{color}')


def _paragraph(text: str, size: int = 15, color: str = BODY,
               line: float = 1.9, margin: str = '16px 0 0') -> str:
    return f'<p style="margin:{margin};{_font(size, color, 400, line)}">{text}</p>'


def _eyebrow(text: str) -> str:
    return (
        f'<p style="margin:0;{_font(11, CLAY_DARK, 600, 1.5)};letter-spacing:.16em">'
        f'<span style="display:inline-block;width:18px;height:1px;'
        f'background:#d9b899;vertical-align:middle;margin-right:8px"></span>{text}</p>'
    )


def _button(label: str, href: str = ACTION) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:26px 0 0">\n'
        '  <tr>\n'
        f'    <td align="center" bgcolor="{CLAY}" style="border-radius:12px">\n'
        f'      <a href="{href}" style="display:inline-block;padding:13px 30px;'
        f'{_font(15, "#ffffff", 600, 1.4)};text-decoration:none;border-radius:12px">'
        f'{label}</a>\n'
        '    </td>\n'
        '  </tr>\n'
        '</table>'
    )


def _fallback(href: str = ACTION) -> str:
    return (
        f'<p style="margin:14px 0 0;{_font(12, MUTED, 400, 1.8)}">'
        '按钮无法打开时，请复制以下链接到浏览器访问：<br>'
        f'<span style="color:{CLAY_DARK};word-break:break-all">{href}</span></p>'
    )


def _note(title: str, body: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:24px 0 0;background:{CREAM};border-radius:14px">\n'
        '  <tr>\n'
        '    <td style="padding:16px 18px;border-left:3px solid #dcae83;'
        'border-radius:14px">\n'
        f'      <p style="margin:0;{_font(13, "#7a4c2c", 600, 1.6)}">{title}</p>\n'
        f'      <p style="margin:4px 0 0;{_font(12, MUTED, 400, 1.8)}">{body}</p>\n'
        '    </td>\n'
        '  </tr>\n'
        '</table>'
    )


def _safety(text: str) -> str:
    return (
        f'<p style="margin:20px 0 0;{_font(13, "#6f675d", 400, 1.9)}">'
        f'<strong style="color:#4c453d">安全提示：</strong>{text}</p>'
    )


def _steps(items: list[str]) -> str:
    rows = []
    for index, text in enumerate(items, start=1):
        rows.append(
            '  <tr>\n'
            '    <td width="26" valign="top" style="padding:0 0 12px">\n'
            f'      <span style="display:inline-block;width:20px;height:20px;'
            f'border-radius:50%;background:#f3e2d3;font-family:{FONT};'
            f'font-size:11px;line-height:20px;font-weight:600;color:{CLAY_DARK};'
            f'text-align:center">{index}</span>\n'
            '    </td>\n'
            '    <td valign="top" style="padding:1px 0 12px;'
            f'{_font(14, BODY, 400, 1.7)}">{text}</td>\n'
            '  </tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:20px 0 0">\n' + '\n'.join(rows) + '\n</table>'
    )


def _facts(order_code_value: str = ORDER_CODE,
           status_label: str = '当前状态',
           status_value: str = ORDER_STATUS) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:24px 0 0;border:1px solid #efe4d6;'
        'border-radius:14px;background:#fdfaf6">\n'
        '  <tr>\n'
        f'    <td style="padding:14px 18px;border-bottom:1px solid #f2e9de;'
        f'{_font(13, MUTED, 400, 1.6)}">订单号</td>\n'
        f'    <td align="right" style="padding:14px 18px;'
        f'border-bottom:1px solid #f2e9de;'
        f'{_font(14, INK, 600, 1.6)};letter-spacing:.04em">{order_code_value}</td>\n'
        '  </tr>\n'
        '  <tr>\n'
        f'    <td style="padding:14px 18px;{_font(13, MUTED, 400, 1.6)}">'
        f'{status_label}</td>\n'
        f'    <td align="right" style="padding:14px 18px;'
        f'{_font(14, CLAY_DARK, 600, 1.6)}">{status_value}</td>\n'
        '  </tr>\n'
        '</table>'
    )


def _multi_facts(rows: list[tuple[str, str]]) -> str:
    body = []
    for index, (label, value) in enumerate(rows):
        border = 'border-bottom:1px solid #f2e9de;' if index < len(rows) - 1 else ''
        body.append(
            '  <tr>\n'
            f'    <td style="padding:13px 18px;{border}{_font(13, MUTED, 400, 1.6)}">'
            f'{label}</td>\n'
            f'    <td align="right" style="padding:13px 18px;{border}'
            f'{_font(14, INK, 600, 1.6)}">{value}</td>\n'
            '  </tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:24px 0 0;border:1px solid #efe4d6;'
        'border-radius:14px;background:#fdfaf6">\n'
        + '\n'.join(body)
        + '\n</table>'
    )


def _reward_card() -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:24px 0 0;background:{CREAM};border-radius:14px">\n'
        '  <tr>\n'
        '    <td style="padding:20px 22px">\n'
        f'      <p style="margin:0;{_font(10, CLAY_DARK, 600, 1.5)};'
        f'letter-spacing:.18em">奖励内容</p>\n'
        f'      <p style="margin:6px 0 0;{_font(17, INK, 600, 1.7)}">{REWARD}</p>\n'
        '    </td>\n'
        '  </tr>\n'
        '</table>'
    )


def _shell(tag: str, eyebrow: str, title: str, content: str, footer: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="width:100%;margin:0;padding:0;background:'
        f'{CANVAS}">\n'
        '  <tr>\n'
        '    <td align="center" style="padding:32px 12px">\n'
        '      <table role="presentation" width="600" cellpadding="0" '
        'cellspacing="0" border="0" style="width:100%;max-width:600px;'
        f'background:{PAPER};border:1px solid {BORDER};border-radius:20px;'
        'overflow:hidden">\n'
        '        <tr>\n'
        f'          <td style="height:4px;background:{CLAY};'
        'border-radius:20px 20px 0 0;font-size:0;line-height:0">&nbsp;</td>\n'
        '        </tr>\n'
        '        <tr>\n'
        f'          <td style="padding:20px 32px;background:{HEADER};'
        f'border-bottom:1px solid {LINE}">\n'
        '            <table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" border="0">\n'
        '              <tr>\n'
        f'                <td style="{_font(15, INK, 600, 1.5)}">\n'
        '                  <img src="{{logoUrl}}" width="30" height="30" '
        'alt="" style="display:inline-block;width:30px;height:30px;'
        'margin-right:10px;border:0;border-radius:8px;'
        f'background:{CLAY};vertical-align:middle">\n'
        f'                  <span style="vertical-align:middle">{BRAND}</span>\n'
        '                </td>\n'
        '                <td align="right" style="white-space:nowrap">\n'
        '                  <span style="display:inline-block;padding:5px 11px;'
        'border:1px solid #ead7c4;border-radius:999px;background:#fff9f2;'
        f'{_font(11, CLAY_DARK, 600, 1.5)};letter-spacing:.1em">{tag}</span>\n'
        '                </td>\n'
        '              </tr>\n'
        '            </table>\n'
        '          </td>\n'
        '        </tr>\n'
        '        <tr>\n'
        '          <td style="padding:30px 32px 28px">\n'
        f'            {eyebrow}\n'
        f'            <h1 style="margin:11px 0 0;{_font(23, INK, 600, 1.45)};'
        f'letter-spacing:-.01em">{title}</h1>\n'
        f'            {content}\n'
        '          </td>\n'
        '        </tr>\n'
        '        <tr>\n'
        f'          <td style="padding:18px 32px 22px;background:{HEADER};'
        f'border-top:1px solid {LINE}">\n'
        f'            <p style="margin:0;{_font(12, MUTED, 400, 1.8)}">'
        f'{footer}</p>\n'
        '          </td>\n'
        '        </tr>\n'
        '      </table>\n'
        f'      <p style="margin:14px 0 0;{_font(11, FAINT, 400, 1.6)}">'
        '系统自动发送 · 请勿直接回复</p>\n'
        '    </td>\n'
        '  </tr>\n'
        '</table>'
    )


_SECURITY_FOOTER = (
    '为保障账号安全，我们不会通过邮件索要密码、验证码或支付信息。'
)
_ORDER_FOOTER = '此邮件为订单进度通知，仅发送给已开启通知的账号。'


def _verify_email() -> str:
    content = (
        _paragraph(f'你好，感谢注册 {BRAND}。完成邮箱验证后，即可正常登录、'
                   '查看订单与接收重要通知。')
        + _button('验证邮箱')
        + _fallback()
        + _note(f'链接 {EXPIRES} 分钟内有效',
                '该链接仅可使用一次，超时后请返回登录页重新发起验证。')
        + _safety('如果这不是您本人的操作，请忽略本邮件。邮箱不会被验证，'
                  '账号也不会发生任何变化。')
    )
    return _shell('安全验证', _eyebrow('账号安全'), '请验证您的邮箱', content,
                  _SECURITY_FOOTER)


def _reset_password() -> str:
    content = (
        _paragraph(f'我们收到了 {BRAND} 账号的密码重置请求。如果这是您本人发起，'
                   '请点击下方按钮设置新密码。')
        + _button('设置新密码')
        + _fallback()
        + _note(f'链接 {EXPIRES} 分钟内有效',
                '该链接仅可使用一次；如已超时，请重新发起密码重置。')
        + _safety('任何工作人员都不会向您索要密码或验证码。若您没有发起此次请求，'
                  '请忽略本邮件，并考虑尽快修改密码。')
    )
    return _shell('密码重置', _eyebrow('账号安全'), '重置账号密码', content,
                  _SECURITY_FOOTER)


def _order_created() -> str:
    content = (
        _paragraph(f'您在 {BRAND} 的订单已提交成功。请按订单页面指引联系打印员'
                   '并完成线下付款；订单不会自动确认为已付款。')
        + _facts()
        + _button('查看订单', ORDER_LINK)
        + _fallback(ORDER_LINK)
        + _note('订单说明',
                '如需修改或取消，请在订单页面按最新规则操作；排队或制作中的订单'
                '可能无法直接修改。')
    )
    return _shell('订单回执', _eyebrow('订单动态'), '订单已创建', content, _ORDER_FOOTER)


def _order_status() -> str:
    content = (
        _paragraph('您的订单状态有了新的进展，详情如下。实际配送时间请以最新订单'
                   '页面为准。')
        + _facts(status_label='最新状态')
        + _button('查看订单详情', ORDER_LINK)
        + _fallback(ORDER_LINK)
        + _note('状态可能短暂滞后',
                '打印与配送进度偶有延迟，请以订单页面实时显示为准。')
    )
    return _shell('订单进度', _eyebrow('订单动态'), '订单进度已更新', content, _ORDER_FOOTER)


def _admin_order_created() -> str:
    content = (
        _paragraph(f'订单 {ORDER_CODE} 已提交，请及时核对文件并安排打印。')
        + _multi_facts([
            ('订单号', ORDER_CODE),
            ('订单金额', ORDER_AMOUNT),
            ('客户昵称', CUSTOMER_NICKNAME),
            ('联系电话', CUSTOMER_PHONE),
        ])
        + _note('联系客户',
                '请留意添加客户微信，确认文件细节与配送安排；电话仅用于必要联系。')
        + _button('查看订单', ORDER_LINK)
        + _fallback(ORDER_LINK)
    )
    return _shell('新订单', _eyebrow('待处理订单'), '有新的订单待处理', content,
                  '此邮件发送给具备接单/打印权限的管理员。')


def _payment_success() -> str:
    content = (
        _paragraph(f'{BRAND} 已确认收到订单 {ORDER_CODE} 的付款，正在安排打印。')
        + _multi_facts([('付款金额', PAID_AMOUNT), ('订单号', ORDER_CODE)])
        + _button('查看订单', ORDER_LINK)
        + _fallback(ORDER_LINK)
        + _note('接下来', '打印与配送完成后，我们会再发送一封签收确认邮件。')
    )
    return _shell('付款成功', _eyebrow('订单动态'), '付款已确认', content,
                  _ORDER_FOOTER)


def _order_delivered() -> str:
    content = (
        _paragraph(f'订单 {ORDER_CODE} 已由配送员完成签收，感谢使用 {BRAND}。')
        + _multi_facts([
            ('订单号', ORDER_CODE),
            ('签收时间', DELIVERED_AT),
            ('配送楼栋', DELIVERY_LOCATION),
        ])
        + _button('查看订单', ORDER_LINK)
        + _fallback(ORDER_LINK)
        + _note('资料保管', '请及时取回打印资料并妥善保管；如签收信息有误，请联系我们。')
    )
    return _shell('签收成功', _eyebrow('订单动态'), '订单已签收', content,
                  _ORDER_FOOTER)


def _delivery_updated() -> str:
    content = (
        _paragraph(f'订单 {ORDER_CODE} 的配送信息已更新，请以最新安排为准。')
        + _multi_facts([('配送楼栋', DELIVERY_LOCATION), ('配送时段', DELIVERY_SLOT)])
        + _button('查看订单', ORDER_LINK)
        + _fallback(ORDER_LINK)
        + _note('尚未付款？', '请先完成付款，配送安排才会最终生效。')
    )
    return _shell('配送变更', _eyebrow('配送信息'), '配送信息已更新', content,
                  _ORDER_FOOTER)


def _courier_dispatch() -> str:
    content = (
        _paragraph(f'{COURIER_NAME}，你好。批次「{BATCH_NAME}」已发布，'
                   '请按以下安排取件配送。')
        + _multi_facts([
            ('配送日期', DELIVERY_DATE),
            ('配送时段', DELIVERY_SLOT),
            ('订单数量', ORDER_COUNT),
        ])
        + _button('查看配送任务', COURIER_LINK)
        + _fallback(COURIER_LINK)
        + _note('出发前', '请核对订单数量与包装，并提前联系客户确认送达时间。')
    )
    return _shell('配送派单', _eyebrow('配送任务'), '新的配送批次', content,
                  '此邮件仅发送给本批次配送员。')


def _account_deleted() -> str:
    content = (
        _paragraph(f'你的 {BRAND} 账号注销申请已受理。')
        + _paragraph(f'我们将在 {DELETION_DEADLINE} 前完成注销处理，并停止发送后续通知。')
        + _note('反悔期', '在此之前登录账号即可撤销注销申请。')
        + _safety('注销完成后，订单、优惠券与历史记录将无法恢复。')
    )
    return _shell('账号注销', _eyebrow('账号安全'), '账号注销申请已受理', content,
                  _SECURITY_FOOTER)


def _referral_reward() -> str:
    content = (
        _paragraph(f'感谢您邀请朋友使用 {BRAND}。以下奖励已发放到您的账号：')
        + _reward_card()
        + _steps(['登录账号', '打开「我的优惠券」',
                  '查看使用条件与有效期'])
        + _note('使用说明',
                '奖励的使用门槛、有效期与活动说明，均以「我的优惠券」页面为准。')
    )
    return _shell('奖励到账', _eyebrow('邀请奖励'), '邀请奖励已到账', content,
                  '如不希望继续接收此类通知，可在「账号设置 · 通知偏好」中关闭。')


_TEMPLATE_BUILDERS = {
    'CUSTOMER_VERIFY_EMAIL': _verify_email,
    'CUSTOMER_RESET_PASSWORD': _reset_password,
    'ADMIN_ORDER_CREATED': _admin_order_created,
    'COURIER_DISPATCH': _courier_dispatch,
    'DELIVERY_UPDATED': _delivery_updated,
    'PAYMENT_SUCCESS': _payment_success,
    'ORDER_DELIVERED': _order_delivered,
    'ACCOUNT_DELETED': _account_deleted,
    'ORDER_CREATED': _order_created,
    'ORDER_STATUS_UPDATED': _order_status,
    'REFERRAL_REWARD_GRANTED': _referral_reward,
}


def template_html(key: str) -> str:
    """Return the full HTML body for one built-in template key."""
    try:
        return _TEMPLATE_BUILDERS[key]()
    except KeyError as error:  # pragma: no cover - guarded by TEMPLATE_KEYS
        raise ValueError(f'unknown mail template {key!r}') from error
