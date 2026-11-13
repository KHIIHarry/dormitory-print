"""Render and publish a private, non-billable order identification sheet."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unicodedata

import pymupdf
import qrcode
from PIL import Image, ImageOps
from sqlalchemy import select

from . import pdf_engine
from .config import get_config, identification_default_website_url, section_data
from .core import DomainError, enqueue_job
from .media import IDENTIFICATION_PURPOSE, IDENTIFICATION_QR_PURPOSE, _artifact_key, _expiry_for, _root, _storage_path
from .models import Artifact, Asset, Job, Order, OrderFile, PrintTask


JOB_KIND = 'ORDER_IDENTIFICATION'
FILE_NAME = '订单识别页.pdf'
HEADER = Path(__file__).with_name('resources') / 'identification-header.pdf'
SCALE = pdf_engine.A4_WIDTH_PT / 794
INK = (0.09, 0.09, 0.09)
MUTED = (0.29, 0.29, 0.29)
ROW_LINE = (0.76, 0.76, 0.76)
FIRST_TABLE_TOP = 386
CONTINUATION_TABLE_TOP = 233
FIRST_TABLE_BOTTOM = 925
CONTINUATION_TABLE_BOTTOM = 1052
FONT_SIZE = 16.5
LINE_HEIGHT = 23
ROW_HEIGHT = 46
QR_X, QR_Y, QR_SIZE = 629, 964, 108


@dataclass(frozen=True)
class IdentificationFile:
    name: str
    copies: tuple[str, ...]
    binding: str


@dataclass(frozen=True)
class FileRow:
    lines: tuple[str, ...]
    copies: str
    binding: str

    @property
    def height(self):
        return ROW_HEIGHT + LINE_HEIGHT * (len(self.lines) - 1)


def _wrap(text, font, font_size, width):
    # Filenames can contain continuous CJK, long words, or explicit newlines.
    lines = []
    line = ''
    for character in text:
        if character == '\n' or (line and font.text_length(line + character, font_size) > width):
            lines.append(line)
            line = ''
        if character != '\n':
            line += character
    lines.append(line)
    return tuple(lines)


def _file_rows(files, font):
    max_lines = 1 + (CONTINUATION_TABLE_BOTTOM - CONTINUATION_TABLE_TOP - ROW_HEIGHT) // LINE_HEIGHT
    for file in files:
        name = file.name or '文件名未记录'
        # PDF CJK text uses BMP code points. Keep uncommon characters
        # distinguishable even when the PDF font has no matching glyph.
        printable = ''.join(
            character if ord(character) <= 0xffff and unicodedata.category(character) not in ('Cc', 'Cf')
            and font.has_glyph(ord(character)) else f'U+{ord(character):04X}'
            for character in name
        )
        lines = _wrap(printable, font, FONT_SIZE, 440)
        copies = file.copies[0]
        if len(file.copies) > 1:
            lines += _wrap('；'.join(file.copies), font, FONT_SIZE, 440)
            copies = '分段'
        for offset in range(0, len(lines), max_lines):
            yield FileRow(lines[offset:offset + max_lines], copies, file.binding)


def _paginate(rows):
    pages = [[]]
    y, bottom = FIRST_TABLE_TOP, FIRST_TABLE_BOTTOM
    for row in rows:
        if y + row.height > bottom:
            pages.append([])
            y, bottom = CONTINUATION_TABLE_TOP, CONTINUATION_TABLE_BOTTOM
        pages[-1].append(row)
        y += row.height
    return pages


def _rect(x, y, width, height):
    return pymupdf.Rect(x * SCALE, y * SCALE, (x + width) * SCALE, (y + height) * SCALE)


def _rule(page, x, y, end_x, end_y, color=INK, width=1):
    page.draw_line((x * SCALE, y * SCALE), (end_x * SCALE, end_y * SCALE),
                   color=color, width=width * SCALE)


def _binding(page, x, y, binding, scale=1):
    page.draw_rect(_rect(x, y, 20 * scale, 25 * scale), color=MUTED, width=scale * SCALE)
    positions = {'CORNER': ((5, 5),), 'SIDE': ((5, 7.5), (5, 18.5))}
    if binding in positions:
        for dx, dy in positions[binding]:
            page.draw_circle(((x + dx * scale) * SCALE, (y + dy * scale) * SCALE),
                             2.2 * scale * SCALE, color=None, fill=INK)
    else:
        page.draw_circle(((x + 10 * scale) * SCALE, (y + 12.5 * scale) * SCALE),
                         3 * scale * SCALE, color=INK, width=1.35 * scale * SCALE)


def _website_qr(page, url):
    qr = qrcode.QRCode(border=4)
    qr.add_data(url)
    matrix = qr.get_matrix()
    size = QR_SIZE / len(matrix)
    shape = page.new_shape()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                shape.draw_rect(_rect(QR_X + x * size, QR_Y + y * size, size, size))
    shape.finish(color=None, fill=INK)
    shape.commit()
    # The matrix already includes the four-module quiet zone. Leave just
    # enough room for the corner strokes to sit outside it.
    _website_qr_frame(page, QR_X, QR_Y, QR_SIZE, 0.6)


def _uploaded_website_qr(page, path):
    with Image.open(path) as image:
        foreground = ImageOps.exif_transpose(image).convert('RGBA')
        background = Image.new('RGBA', foreground.size, 'white')
        background.alpha_composite(foreground)
        png = BytesIO()
        background.convert('L').save(png, format='PNG')
    page.insert_image(_rect(627, 960, 106, 106), stream=png.getvalue())
    _website_qr_frame(page, 627, 960, 106, 5)


def _website_qr_frame(page, left, top, size, gap):
    for x, dx in ((left - gap, 1), (left + size + gap, -1)):
        for y, dy in ((top - gap, 1), (top + size + gap, -1)):
            shape = page.new_shape()
            shape.draw_polyline([
                ((x + 11 * dx) * SCALE, y * SCALE),
                (x * SCALE, y * SCALE),
                (x * SCALE, (y + 11 * dy) * SCALE),
            ])
            shape.finish(color=MUTED, width=1.05 * SCALE, closePath=False)
            shape.commit()


def render_identification(path, pickup_code, files, a4_sheets, photos, website, *, qr_image=None):
    regular = pymupdf.Font('china-s')
    bold = pymupdf.Font('hebo')
    pages = _paginate(_file_rows(files, regular))
    with pymupdf.open() as pdf, pymupdf.open(HEADER) as header:
        for number, rows in enumerate(pages, start=1):
            page = pdf.new_page(width=pdf_engine.A4_WIDTH_PT, height=pdf_engine.A4_HEIGHT_PT)
            writer = pymupdf.TextWriter(page.rect)

            def put(value, x, y, size, font=regular, align='left'):
                value = str(value)
                width = font.text_length(value, size)
                if align == 'right':
                    x -= width
                elif align == 'center':
                    x -= width / 2
                writer.append((x * SCALE, y * SCALE), value, font=font, fontsize=size * SCALE)

            if number == 1:
                page.show_pdf_page(_rect(0, 0, 794, 151), header, 0)
                put('订单提取码', 56, 190, 15)
                code_size = min(85, 332 / bold.text_length(pickup_code, 1))
                put(pickup_code, 56, 269, code_size, bold)
                _rule(page, 407, 177, 407, 277, ROW_LINE)
                put('订单识别页', 437, 190, 15)
                for x, total, label in ((437, len(files), '原始文件'), (550, a4_sheets, 'A4 纸张'),
                                        (656, photos, '照片张数')):
                    total_size = min(35, 80 / bold.text_length(str(total), 1))
                    put(total, x, 241, total_size, bold)
                    put(label, x, 265, 15)
                _rule(page, 56, 301, 738, 301, width=1.35)
                put('文件清单', 56, 338, 24)
                y = FIRST_TABLE_TOP
                legend_y = 322
                _rule(page, 56, 945, 738, 945, width=1.35)
                put('下次打印，扫码下单', 56, 1012, 25)
                put('在线上传文件，预约宿舍配送。', 56, 1041, 15)
                if qr_image is None:
                    _website_qr(page, website)
                else:
                    _uploaded_website_qr(page, qr_image)
            else:
                put('订单提取码', 56, 72, 15)
                put(pickup_code, 54, 127, 53, bold)
                put('订单识别页', 738, 115, 15, align='right')
                _rule(page, 56, 152, 738, 152, width=1.35)
                put('文件清单（续）', 56, 188, 20)
                y = CONTINUATION_TABLE_TOP
                legend_y = 173

            for x, method, label in ((490, 'CORNER', '左上角钉'), (578, 'SIDE', '左侧装订'),
                                     (666, 'NONE', '无需装订')):
                _binding(page, x, legend_y, method, .67)
                put(label, x + 19, legend_y + 13, 13)
            put('原始文件名', 56, y - 14, 14)
            put('份数', 541, y - 14, 14, align='center')
            put('装订方式', 682, y - 14, 14, align='center')
            _rule(page, 56, y, 738, y, width=1.35)
            for row in rows:
                for offset, line in enumerate(row.lines):
                    put(line, 56, y + 28 + offset * LINE_HEIGHT, FONT_SIZE)
                number_font = bold if row.copies.isdigit() else regular
                put(row.copies, 541, y + 28, 17, number_font, align='center')
                _binding(page, 672, y + 10, row.binding)
                y += row.height
                _rule(page, 56, y, 738, y, ROW_LINE, .85)
            if len(pages) > 1:
                put(f'{number} / {len(pages)}', 738, 1090, 15, align='right')
            writer.write_text(page, color=INK)
        pdf.subset_fonts()
        pdf.save(path, garbage=3, deflate=True, no_new_id=True)
    return len(pages)


def queue_identification(db, order):
    config = section_data('workflow', get_config(db, 'workflow'))
    if config['orderIdentificationEnabled']:
        enqueue_job(db, JOB_KIND, order.id, f'order-identification:{order.id}', {
            'contentRevision': order.content_revision,
            'website': config['orderIdentificationWebsiteUrl'] or identification_default_website_url(),
        })


def _order_files(db, order):
    from .production import _original_file_names

    files = list(db.scalars(select(OrderFile).where(
        OrderFile.order_id == order.id, OrderFile.revision == order.content_revision,
    ).order_by(OrderFile.created_at, OrderFile.id)))
    names = _original_file_names(db, files)
    parts_by_file = {file.id: [] for file in files}
    for file_id, artifact in db.execute(select(PrintTask.file_id, Artifact)
        .join(Artifact, PrintTask.artifact_id == Artifact.id)
        .where(PrintTask.order_id == order.id, PrintTask.file_id.in_(parts_by_file),
               PrintTask.active.is_(True))):
        parts_by_file[file_id].append(artifact)

    result = []
    a4_sheets = photos = 0
    for file in files:
        parts = sorted(parts_by_file[file.id], key=lambda artifact: (
            int((artifact.settings or {}).get('sequence') or 0), artifact.id,
        ))
        if not parts:
            raise RuntimeError('Order file has no active print artifact')
        counts = [str((artifact.settings or {}).get('copies') or 1) for artifact in parts]
        if len(set(counts)) > 1:
            copies = tuple(
                f'{(artifact.settings or {}).get("range") or f"分段 {index}"} × {count}'
                for index, (artifact, count) in enumerate(zip(parts, counts), start=1)
            )
        else:
            copies = (counts[0],)
        result.append(IdentificationFile(names[file.id], copies, file.binding_type))
        sheets = sum(artifact.sheets for artifact in parts)
        if file.kind == 'PHOTO':
            photos += sheets
        else:
            a4_sheets += sheets
    if not result:
        raise RuntimeError('Order has no active files')
    return result, a4_sheets, photos


def process_identification(db, job):
    """Called by the leased worker. The order lock serializes creation and cancellation."""
    lease_token = job.lease_token
    order = db.scalar(select(Order).where(Order.id == job.target_id).with_for_update())
    job = db.scalar(select(Job).where(Job.id == job.id).with_for_update()
                    .execution_options(populate_existing=True))
    if not job or job.lease_token != lease_token or job.status != 'PROCESSING':
        return False
    if (not order or order.deleted_at or not order.settlement_kind
            or order.status not in ('PENDING_PRINT', 'READY_FOR_DELIVERY')
            or order.content_revision != job.data['contentRevision']):
        return False
    if db.scalar(select(PrintTask.id).where(
        PrintTask.order_id == order.id, PrintTask.file_id.is_(None),
    )):
        return True

    # Jobs created with an uploaded QR still carry its original asset.
    qr_image = None
    if qr_asset_id := job.data.get('qrAssetId'):
        qr_asset = db.get(Asset, qr_asset_id)
        if (not qr_asset or qr_asset.purpose != IDENTIFICATION_QR_PURPOSE
                or qr_asset.status != 'READY' or qr_asset.deleted_at or not qr_asset.storage_key):
            raise DomainError('IDENTIFICATION_QR_UNAVAILABLE', '网站二维码不可用', 409)
        qr_image = _storage_path(qr_asset.storage_key)
        if not qr_image.is_file() or pdf_engine.sha256_file(qr_image) != qr_asset.sha256:
            raise DomainError('IDENTIFICATION_QR_UNAVAILABLE', '网站二维码文件不可用', 409)

    files, sheets, photos = _order_files(db, order)
    with TemporaryDirectory(dir=_root() / 'tmp') as tmp:
        path = Path(tmp) / FILE_NAME
        pages = render_identification(path, order.pickup_code, files, sheets, photos,
                                      job.data['website'], qr_image=qr_image)
        sha = pdf_engine.sha256_file(path)
        key = _artifact_key(job.id, sha)
        size = path.stat().st_size
        asset = Asset(
            id=job.id, purpose=IDENTIFICATION_PURPOSE, name=FILE_NAME, mime='application/pdf',
            size=size, sha256=sha, storage_key=key, status='READY', pages=pages,
            expires_at=_expiry_for(IDENTIFICATION_PURPOSE, db),
        )
        db.add(asset)
        db.flush()
        settings = {'contentRevision': order.content_revision, 'nonBillable': True,
                    'copies': 1, 'sides': 'SINGLE', 'flip': 'NONE', 'colorMode': 'BW'}
        artifact = Artifact(
            preparation_id=order.preparation_id, asset_id=asset.id, client_file_id=job.id,
            sha256=sha, pages=pages, sheets=pages, settings=settings, page_map=[],
        )
        db.add(artifact)
        db.flush()
        db.add(PrintTask(order_id=order.id, artifact_id=artifact.id, settings=settings))
        db.flush()
        pdf_engine.publish_file(path, _storage_path(key), sha)
    return True
