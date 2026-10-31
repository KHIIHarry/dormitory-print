"""Real media engine: format inspection, LibreOffice conversion, PDF geometry.

Everything here is deterministic, database-free and side-effect free apart from
files explicitly written to caller-provided temporary paths.  The media module
owns storage/transactions; this module owns actual file bytes.

Design rules enforced here (see backend/CONTRACT.md and docs/02 G1-G4):

* File type comes from magic bytes *and* container contents, never the suffix
  alone. Macros, external OLE links and embedded executables are rejected.
* Office documents are converted by LibreOffice headless with a private profile
  and no shell interpolation. If the binary is missing we say so; we never fake
  a conversion.
* Every page of a non-A4 document is proportionally fitted inside A4 without
  cropping the document.
* Each print task/copy expands independently: single-sided -> one sheet per
  page; duplex -> ceil(n/2) sheets per copy with a real blank back for an odd
  page count (including the last copy).
* Photo geometry is computed in millimetres from the calibrated paper/margins.
* Grayscale prefers a vector-preserving Ghostscript conversion. Rasterising is
  a last resort and always carries an explicit warning so the UI can require a
  new preview confirmation.
* Artifacts are verified by SHA-256 before an atomic publish.
"""
from __future__ import annotations

import functools
import hashlib
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # Pillow-heif is a hard V1 requirement, but report honestly if missing.
    from pillow_heif import register_heif_opener
except ImportError:  # pragma: no cover - dependency is pinned in requirements
    register_heif_opener = None

A4_WIDTH_PT = 595.276
A4_HEIGHT_PT = 841.890
MM_TO_PT = 72.0 / 25.4
A4_TOLERANCE_PT = 0.75
ASPECT_TOLERANCE = 0.02
MAX_EXCEL_AREA_CELLS = 100_000
MAX_CHUNK_BYTES = 32 * 1024 * 1024

if register_heif_opener is not None:  # pragma: no branch
    register_heif_opener()


class EngineError(Exception):
    """A real processing failure with a stable client-facing code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


# ---------------------------------------------------------------------------
# Format inspection
# ---------------------------------------------------------------------------

KIND_BY_EXTENSION = {
    '.pdf': 'PDF',
    '.doc': 'DOC',
    '.docx': 'DOCX',
    '.ppt': 'PPT',
    '.pptx': 'PPTX',
    '.xls': 'XLS',
    '.xlsx': 'XLSX',
    '.jpg': 'JPEG',
    '.jpeg': 'JPEG',
    '.png': 'PNG',
    '.webp': 'WEBP',
    '.heic': 'HEIC',
    '.heif': 'HEIC',
}

KIND_MIME = {
    'PDF': 'application/pdf',
    'DOC': 'application/msword',
    'DOCX': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'PPT': 'application/vnd.ms-powerpoint',
    'PPTX': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'XLS': 'application/vnd.ms-excel',
    'XLSX': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
    'HEIC': 'image/heic',
}

MIME_KINDS = {
    'application/pdf': 'PDF',
    'application/msword': 'DOC',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
    'application/vnd.ms-powerpoint': 'PPT',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
    'application/vnd.ms-excel': 'XLS',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
    'image/jpeg': 'JPEG',
    'image/jpg': 'JPEG',
    'image/png': 'PNG',
    'image/webp': 'WEBP',
    'image/heic': 'HEIC',
    'image/heif': 'HEIC',
}

OFFICE_KINDS = frozenset({'DOC', 'DOCX', 'PPT', 'PPTX', 'XLS', 'XLSX'})
IMAGE_KINDS = frozenset({'JPEG', 'PNG', 'WEBP', 'HEIC'})
OOXML_KIND_PARTS = {
    'DOCX': 'word/document.xml',
    'XLSX': 'xl/workbook.xml',
    'PPTX': 'ppt/presentation.xml',
}

_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_ZIP_MAGIC = b'PK\x03\x04'


@dataclass(frozen=True)
class MediaInfo:
    kind: str
    mime: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PdfProbe:
    pages: int
    sizes: tuple[tuple[float, float], ...]
    rotations: tuple[int, ...]


@dataclass(frozen=True)
class ImageProbe:
    width: int
    height: int
    mode: str
    format: str
    exif_orientation: int | None
    frames: int


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def signature_kind(head: bytes) -> str | None:
    if b'%PDF-' in head[:1024]:
        return 'PDF'
    if head.startswith(b'\xff\xd8\xff'):
        return 'JPEG'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if len(head) >= 12 and head[0:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1', b'avif'):
            return 'HEIC'
    if head.startswith(_OLE_MAGIC):
        return 'OLE'
    if head.startswith(_ZIP_MAGIC):
        return 'OOXML'
    return None


def _zip_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [name for name in archive.namelist()]
    except zipfile.BadZipFile as error:
        raise EngineError('INVALID_OFFICE', 'Office 文件结构损坏，无法读取', retryable=False) from error


def _office_container_kind(path: Path, expected: str | None) -> str:
    raw_names = _zip_names(path)
    if len(raw_names) != len(set(raw_names)):
        raise EngineError('INVALID_OFFICE', 'Office 文件包含重复内部条目，出于安全原因不能处理', retryable=False)
    names = {name.lower() for name in raw_names}
    if '[content_types].xml' not in names:
        raise EngineError('INVALID_OFFICE', '不是有效的 Office 文档容器', retryable=False)
    if any(name.endswith('vbaproject.bin') for name in names):
        raise EngineError(
            'OFFICE_MACROS_FORBIDDEN', '不支持包含宏的 Office 文件，请另存为普通文档后重新上传', retryable=False
        )
    if (
        any(name.startswith('xl/externallinks/') or name.startswith('word/externallinks/') for name in names)
        or any('/embeddings/' in name or name.endswith('oleobject.bin') for name in names)
        or _has_unsafe_relationship_targets(path, raw_names)
    ):
        raise EngineError(
            'OFFICE_EXTERNAL_CONTENT_FORBIDDEN', '文档包含外部链接或嵌入对象，出于安全原因不能处理', retryable=False
        )

    matches = [kind for kind, part in OOXML_KIND_PARTS.items() if part in names]
    if len(matches) != 1:
        raise EngineError('INVALID_OFFICE', 'Office 文件类型无法识别', retryable=False)
    actual = matches[0]
    if expected and expected != actual:
        raise EngineError(
            'MIME_MISMATCH',
            f'文件内容实际为 {actual}，与扩展名/声明格式 {expected} 不一致',
            retryable=False,
        )
    return actual


def _has_unsafe_relationship_targets(path: Path, names: Sequence[str]) -> bool:
    """Reject OLE/package/frame relationships pointing outside the document.

    Ordinary https hyperlinks stay allowed; embedded objects and local file
    targets are the actual risk and would be resolved by LibreOffice.
    """
    with zipfile.ZipFile(path) as archive:
        for name in names:
            if not name.lower().endswith('.rels'):
                continue
            info = archive.getinfo(name)
            if info.file_size > 4_000_000:
                raise EngineError('INVALID_OFFICE', 'Office 关系文件异常', retryable=False)
            text = archive.read(name).decode('utf-8', 'replace')
            for match in re.finditer(r'<Relationship\b[^>]*>', text):
                tag = match.group(0)
                if 'TargetMode="External"' not in tag and "TargetMode='External'" not in tag:
                    continue
                type_match = re.search(r'Type="([^"]+)"', tag)
                target_match = re.search(r'Target="([^"]+)"', tag)
                relationship_type = (type_match.group(1) if type_match else '').lower()
                target = (target_match.group(1) if target_match else '').lower()
                if (
                    'oleobject' in relationship_type
                    or 'package' in relationship_type
                    or 'frame' in relationship_type
                    or 'attachedtemplate' in relationship_type
                    or target.startswith('file:')
                    or target.startswith('/')
                    or target.startswith('\\\\')
                ):
                    return True
    return False


def _ole_has_macros(path: Path) -> bool:
    """Conservative VBA stream-name scan for OLE (.doc/.xls/.ppt) files.

    LibreOffice does not execute macros in headless conversion, but V1 rejects
    macro-bearing documents outright rather than relying on that default.
    """
    # OLE directory stream/storage names are UTF-16LE. Avoid ASCII scanning so
    # ordinary document text containing "VBA" is not falsely rejected.
    needles = (
        b'V\x00B\x00A\x00',
        b'_\x00V\x00B\x00A\x00_\x00P\x00R\x00O\x00J\x00E\x00C\x00T\x00',
    )
    try:
        with open(path, 'rb') as handle:
            while True:
                block = handle.read(4 * 1024 * 1024)
                if not block:
                    return False
                if any(needle in block for needle in needles):
                    return True
    except OSError:
        return False


def _assert_mime(kind: str, declared_mime: str) -> None:
    declared = (declared_mime or '').split(';')[0].strip().lower()
    if not declared or declared in ('application/octet-stream', 'binary/octet-stream'):
        return
    expected = MIME_KINDS.get(declared)
    if expected is not None and expected != kind:
        raise EngineError(
            'MIME_MISMATCH',
            f'文件真实类型为 {kind}，与声明的 {declared} 不一致',
            retryable=False,
        )


def inspect_upload(path: str | Path, filename: str, declared_mime: str = '') -> MediaInfo:
    """Inspect a real uploaded file. Raises EngineError for unsupported/corrupt input."""
    path = Path(path)
    if not path.is_file():
        raise EngineError('INVALID_FILE', '上传文件不存在', retryable=False)
    size = path.stat().st_size
    if size <= 0:
        raise EngineError('INVALID_FILE', '上传文件为空', retryable=False)
    extension_kind = KIND_BY_EXTENSION.get(Path(filename or path.name).suffix.lower())
    with open(path, 'rb') as handle:
        head = handle.read(4096)
    signature = signature_kind(head)
    if signature is None:
        raise EngineError('UNSUPPORTED_FORMAT', '不支持该文件格式', retryable=False)
    if signature == 'OOXML':
        kind = _office_container_kind(path, extension_kind)
    elif signature == 'OLE':
        if extension_kind not in ('DOC', 'XLS', 'PPT'):
            raise EngineError('UNSUPPORTED_FORMAT', '旧版 Office 格式无法识别', retryable=False)
        kind = extension_kind
        if _ole_has_macros(path):
            raise EngineError(
                'OFFICE_MACROS_FORBIDDEN',
                '不支持包含宏的 Office 文件，请另存为普通文档后重新上传',
                retryable=False,
            )
    else:
        kind = signature
        if extension_kind and extension_kind != kind and not (
            kind == 'HEIC' and extension_kind == 'HEIC'
        ):
            # e.g. PNG bytes in a .pdf upload: reject instead of guessing.
            raise EngineError(
                'MIME_MISMATCH',
                f'文件内容为 {kind}，与扩展名声明的 {extension_kind} 不一致',
                retryable=False,
            )
    _assert_mime(kind, declared_mime)
    if kind == 'PDF':
        validate_pdf(path, max_pages=100_000)
    elif kind in IMAGE_KINDS:
        validate_image(path, kind)
    return MediaInfo(kind=kind, mime=KIND_MIME[kind], size=size, sha256=sha256_file(path))


def validate_pdf(path: str | Path, max_pages: int) -> PdfProbe:
    try:
        document = pymupdf.open(path)
    except Exception as error:  # pymupdf raises various low-level exceptions
        raise EngineError('INVALID_PDF', 'PDF 文件损坏或无法解析', retryable=False) from error
    try:
        if document.needs_pass or document.is_encrypted:
            raise EngineError('ENCRYPTED_PDF', 'PDF 已加密，请移除密码后重新上传', retryable=False)
        pages = document.page_count
        if pages <= 0:
            raise EngineError('INVALID_PDF', 'PDF 没有可打印页面', retryable=False)
        if pages > max_pages:
            raise EngineError(
                'PAGE_LIMIT_EXCEEDED', f'文档页数 {pages} 超过允许上限 {max_pages}', retryable=False
            )
        if _pdf_has_active_content(document):
            raise EngineError('PDF_ACTIVE_CONTENT_FORBIDDEN', 'PDF 包含脚本等可执行内容，不能处理', retryable=False)
        sizes = tuple((round(page.rect.width, 3), round(page.rect.height, 3)) for page in document)
        rotations = tuple(page.rotation for page in document)
        return PdfProbe(pages=pages, sizes=sizes, rotations=rotations)
    finally:
        document.close()


def _pdf_has_active_content(document) -> bool:
    """Reject PDF JavaScript/launch actions without executing anything."""
    try:
        length = document.xref_length()
    except Exception:  # pragma: no cover - defensive
        return False
    for xref in range(1, length):
        for key in ('JS', 'JavaScript', 'Launch'):
            try:
                kind, value = document.xref_get_key(xref, key)
            except Exception:
                continue
            if kind != 'null' and value not in (None, '', 'null'):
                return True
    return False


def validate_image(path: str | Path, kind: str | None = None, max_pixels: int | None = None) -> ImageProbe:
    try:
        with Image.open(path) as image:
            image_format = (image.format or '').upper()
            frames = getattr(image, 'n_frames', 1)
            exif = image.getexif()
            orientation = exif.get(274) if exif else None
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise EngineError('IMAGE_UNDECODABLE', '图片无法解码，请重新导出后上传', retryable=False) from error
    expected_formats = {
        'JPEG': {'JPEG', 'MPO'},
        'PNG': {'PNG'},
        'WEBP': {'WEBP'},
        'HEIC': {'HEIF', 'HEIC'},
    }
    if kind and image_format not in expected_formats.get(kind, {image_format}):
        raise EngineError('MIME_MISMATCH', f'图片内容为 {image_format}，与声明格式 {kind} 不一致', retryable=False)
    if frames > 1:
        raise EngineError('ANIMATED_IMAGE', '暂不支持动图，请上传单帧图片', retryable=False)
    if max_pixels and width * height > max_pixels:
        raise EngineError(
            'IMAGE_TOO_LARGE', f'图片像素 {width}×{height} 超过允许上限', retryable=False
        )
    if width <= 0 or height <= 0:
        raise EngineError('IMAGE_UNDECODABLE', '图片尺寸无效', retryable=False)
    return ImageProbe(
        width=width,
        height=height,
        mode='',
        format=image_format,
        exif_orientation=int(orientation) if orientation else None,
        frames=frames,
    )


# ---------------------------------------------------------------------------
# LibreOffice / Ghostscript discovery
# ---------------------------------------------------------------------------


def _executable(path: str | os.PathLike | None) -> str | None:
    if not path:
        return None
    value = os.fspath(path)
    if os.path.isfile(value) and os.access(value, os.X_OK):
        return value
    found = shutil.which(value)
    return found


def resolve_soffice(configured: str | None = None) -> str | None:
    """Resolve LibreOffice from configured value, PATH (`command -v`) or bundle."""
    for candidate in (
        configured,
        os.environ.get('DORMPRINT_SOFFICE_PATH'),
        'soffice',
        'libreoffice',
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
        '/usr/lib/libreoffice/program/soffice',
        '/usr/local/bin/soffice',
        '/opt/libreoffice/program/soffice',
    ):
        found = _executable(candidate)
        if found:
            return found
    return None


def resolve_ghostscript(configured: str | None = None) -> str | None:
    for candidate in (
        configured,
        os.environ.get('DORMPRINT_GHOSTSCRIPT_PATH'),
        'gs',
        '/usr/local/bin/gs',
        '/opt/homebrew/bin/gs',
        '/usr/bin/gs',
    ):
        found = _executable(candidate)
        if found:
            return found
    return None


class _Sandbox:
    """Create a private, empty HOME/profile so LibreOffice never shares locks."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.home = workdir / 'home'
        self.home.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict[str, str]:
        base = {
            'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': str(self.home),
            'TMPDIR': str(self.workdir),
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
            'SAL_DISABLE_OPENCL': '1',
        }
        return base


def _run(command: Sequence[str], *, timeout: int, env: Mapping[str, str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            timeout=timeout,
            env=dict(env),
            cwd=str(cwd),
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise EngineError(
            'CONVERTER_TIMEOUT',
            '文档转换超时，请稍后重试',
            retryable=True,
            details={'timeoutSeconds': timeout},
        ) from error


def _minimal_process_env() -> dict[str, str]:
    """Version probes must never receive database/master-key environment."""
    keys = ('PATH', 'HOME', 'LANG', 'LC_ALL')
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.setdefault('PATH', '/usr/bin:/bin')
    return env


@functools.lru_cache(maxsize=1)
def _soffice_version_cached(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, '--version'], capture_output=True, timeout=20, check=False,
            env=_minimal_process_env(),
        )
        text = (result.stdout or result.stderr).decode('utf-8', 'replace').strip().splitlines()
        return text[0] if text else 'unknown'
    except Exception:  # pragma: no cover - version is metadata only
        return 'unknown'


@functools.lru_cache(maxsize=1)
def _gs_version_cached(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, '--version'], capture_output=True, timeout=20, check=False,
            env=_minimal_process_env(),
        )
        return (result.stdout or b'').decode('utf-8', 'replace').strip().splitlines()[0]
    except Exception:  # pragma: no cover
        return 'unknown'


def toolchain_versions(soffice_path: str | None = None, gs_path: str | None = None) -> dict:
    import PIL

    soffice = resolve_soffice(soffice_path)
    gs = resolve_ghostscript(gs_path)
    return {
        'pymupdf': getattr(pymupdf, '__version__', 'unknown'),
        'pillow': PIL.__version__,
        'pillowHeif': _module_version('pillow_heif'),
        'openpyxl': _module_version('openpyxl'),
        'libreoffice': _soffice_version_cached(soffice) if soffice else None,
        'libreofficePath': soffice,
        'ghostscript': _gs_version_cached(gs) if gs else None,
        'ghostscriptPath': gs,
    }


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
        return getattr(module, '__version__', None)
    except ImportError:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Office conversion (LibreOffice)
# ---------------------------------------------------------------------------


def _unique_profile(workdir: Path) -> Path:
    # A stale profile lock from a previous run makes a later LibreOffice
    # invocation wait forever on macOS. Every conversion gets its own profile.
    import uuid

    profile = workdir / f'lo-profile-{uuid.uuid4().hex}'
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def office_to_pdf(
    source: str | Path,
    workdir: str | Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 120,
    target_format: str = 'pdf',
) -> Path:
    source = Path(source)
    workdir = Path(workdir)
    binary = resolve_soffice(soffice_path)
    if not binary:
        raise EngineError(
            'OFFICE_CONVERTER_MISSING',
            '服务器未安装 LibreOffice，暂时无法转换 Office 文件',
            retryable=True,
        )
    outdir = workdir / 'converted'
    outdir.mkdir(parents=True, exist_ok=True)
    sandbox = _Sandbox(workdir)
    profile = _unique_profile(workdir)
    argv = [
        binary,
        '--headless',
        '--norestore',
        '--nolockcheck',
        '--nodefault',
        '--nofirststartwizard',
        f'-env:UserInstallation={profile.as_uri()}',
        '--convert-to',
        target_format,
        '--outdir',
        str(outdir),
        str(source),
    ]
    try:
        result = _run(argv, timeout=timeout, env=sandbox.env(), cwd=workdir)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    output = outdir / (source.stem + '.' + target_format.split(':')[0])
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        stderr = (result.stderr or b'').decode('utf-8', 'replace')[-800:]
        raise EngineError(
            'OFFICE_CONVERT_FAILED',
            'Office 文件转换失败，请检查文件是否损坏或受密码保护',
            retryable=True,
            details={'exitCode': result.returncode, 'stderr': stderr},
        )
    return output


# ---------------------------------------------------------------------------
# Excel print area / pagination options
# ---------------------------------------------------------------------------

_EXCEL_AREA = re.compile(r'^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6}))?$')
_EXCEL_ROW_RANGE = re.compile(r'^\$?([1-9][0-9]{0,6})\s*:\s*\$?([1-9][0-9]{0,6})$')
_EXCEL_COL_RANGE = re.compile(r'^\$?([A-Za-z]{1,3})\s*:\s*\$?([A-Za-z]{1,3})$')
_EXCEL_MAX_ROW = 1_048_576
_EXCEL_MAX_COL = 16_384


def _column_number(letters: str) -> int:
    value = 0
    for character in letters.upper():
        value = value * 26 + (ord(character) - 64)
    return value


def _check_area(value: str) -> str:
    match = _EXCEL_AREA.match(value.strip())
    if not match:
        raise EngineError('EXCEL_OPTIONS_INVALID', '打印区域格式无效，请使用 A1:F40 形式')
    first_col, first_row = _column_number(match.group(1)), int(match.group(2))
    last_col = _column_number(match.group(3)) if match.group(3) else first_col
    last_row = int(match.group(4)) if match.group(4) else first_row
    if not (1 <= first_col <= _EXCEL_MAX_COL and 1 <= last_col <= _EXCEL_MAX_COL):
        raise EngineError('EXCEL_OPTIONS_INVALID', '打印区域列号超出 Excel 范围')
    if not (1 <= first_row <= _EXCEL_MAX_ROW and 1 <= last_row <= _EXCEL_MAX_ROW):
        raise EngineError('EXCEL_OPTIONS_INVALID', '打印区域行号超出 Excel 范围')
    if last_col < first_col or last_row < first_row:
        raise EngineError('EXCEL_OPTIONS_INVALID', '打印区域起止顺序无效')
    cells = (last_col - first_col + 1) * (last_row - first_row + 1)
    if cells > MAX_EXCEL_AREA_CELLS:
        raise EngineError(
            'EXCEL_OPTIONS_INVALID', f'打印区域包含 {cells} 个单元格，超过安全上限',
        )
    return value.strip()


@dataclass(frozen=True)
class ExcelOptions:
    sheet_name: str | None = None
    print_area: str | None = None
    print_titles_rows: str | None = None
    print_titles_columns: str | None = None
    orientation: str = 'PORTRAIT'
    paper_size: str = 'A4'
    fit_to_width: int = 1
    fit_to_height: int = 0
    scale_percent: int | None = None
    gridlines: bool = False
    margins_mm: Mapping[str, float] = field(
        default_factory=lambda: {'top': 10.0, 'right': 8.0, 'bottom': 10.0, 'left': 8.0, 'header': 5.0, 'footer': 5.0}
    )

    def as_manifest(self) -> dict:
        return {
            'sheetName': self.sheet_name,
            'printArea': self.print_area,
            'printTitlesRows': self.print_titles_rows,
            'printTitlesColumns': self.print_titles_columns,
            'orientation': self.orientation,
            'paperSize': self.paper_size,
            'fitToWidth': self.fit_to_width,
            'fitToHeight': self.fit_to_height,
            'scalePercent': self.scale_percent,
            'gridlines': self.gridlines,
            'marginsMm': dict(self.margins_mm),
        }


ALLOWED_EXCEL_KEYS = frozenset(
    {
        'sheetName',
        'printArea',
        'printTitlesRows',
        'printTitlesColumns',
        'orientation',
        'paperSize',
        'fitToWidth',
        'fitToHeight',
        'scalePercent',
        'gridlines',
        'marginsMm',
    }
)
_MARGIN_KEYS = frozenset({'top', 'right', 'bottom', 'left', 'header', 'footer'})


def validate_excel_options(raw: Mapping | None) -> ExcelOptions:
    """Strict, bounded Excel print options. Unknown keys are rejected."""
    raw = dict(raw or {})
    unknown = set(raw) - ALLOWED_EXCEL_KEYS
    if unknown:
        raise EngineError('EXCEL_OPTIONS_INVALID', f'Excel 打印选项包含未知字段: {sorted(unknown)}')

    sheet_name = raw.get('sheetName')
    if sheet_name is not None:
        if not isinstance(sheet_name, str) or not sheet_name.strip() or len(sheet_name) > 64:
            raise EngineError('EXCEL_OPTIONS_INVALID', '工作表名称无效')
        if any(ord(character) < 32 for character in sheet_name):
            raise EngineError('EXCEL_OPTIONS_INVALID', '工作表名称包含非法字符')
        sheet_name = sheet_name.strip()

    print_area = raw.get('printArea')
    if print_area is not None:
        if not isinstance(print_area, str):
            raise EngineError('EXCEL_OPTIONS_INVALID', '打印区域格式无效')
        print_area = _check_area(print_area)

    titles_rows = raw.get('printTitlesRows')
    if titles_rows is not None:
        if not isinstance(titles_rows, str) or not _EXCEL_ROW_RANGE.match(titles_rows.strip()):
            raise EngineError('EXCEL_OPTIONS_INVALID', '重复标题行格式无效，请使用 1:2')
        first, second = (int(part) for part in re.findall(r'\d+', titles_rows))
        if first < 1 or second < first or second > _EXCEL_MAX_ROW:
            raise EngineError('EXCEL_OPTIONS_INVALID', '重复标题行超出 Excel 范围')
        titles_rows = f'{first}:{second}'

    titles_columns = raw.get('printTitlesColumns')
    if titles_columns is not None:
        if not isinstance(titles_columns, str) or not _EXCEL_COL_RANGE.match(titles_columns.strip()):
            raise EngineError('EXCEL_OPTIONS_INVALID', '重复标题列格式无效，请使用 A:B')
        first, second = (_column_number(part) for part in re.findall(r'[A-Za-z]+', titles_columns))
        if not (1 <= first <= second <= _EXCEL_MAX_COL):
            raise EngineError('EXCEL_OPTIONS_INVALID', '重复标题列超出 Excel 范围')
        titles_columns = f'{titles_columns.split(":")[0]}:{titles_columns.split(":")[1]}'

    orientation = str(raw.get('orientation', 'PORTRAIT')).upper()
    if orientation not in ('PORTRAIT', 'LANDSCAPE'):
        raise EngineError('EXCEL_OPTIONS_INVALID', '纸张方向无效')
    paper_size = str(raw.get('paperSize', 'A4')).upper()
    if paper_size != 'A4':
        raise EngineError('EXCEL_OPTIONS_INVALID', 'V1 仅支持 A4 纸张')

    def integer(name, default, minimum, maximum):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise EngineError('EXCEL_OPTIONS_INVALID', f'{name} 必须为整数')
        if not (minimum <= value <= maximum):
            raise EngineError('EXCEL_OPTIONS_INVALID', f'{name} 超出允许范围')
        return value

    fit_width = integer('fitToWidth', 1, 0, 20)
    fit_height = integer('fitToHeight', 0, 0, 50)
    scale_raw = raw.get('scalePercent')
    scale_percent = None if scale_raw is None else integer('scalePercent', None, 10, 400)
    gridlines = raw.get('gridlines', False)
    if not isinstance(gridlines, bool):
        raise EngineError('EXCEL_OPTIONS_INVALID', 'gridlines 必须为布尔值')

    margins_raw = raw.get('marginsMm', {})
    if not isinstance(margins_raw, Mapping):
        raise EngineError('EXCEL_OPTIONS_INVALID', '页边距必须为对象')
    unknown_margins = set(margins_raw) - _MARGIN_KEYS
    if unknown_margins:
        raise EngineError('EXCEL_OPTIONS_INVALID', f'未知页边距字段: {sorted(unknown_margins)}')
    margins = {}
    for key, default in (('top', 10.0), ('right', 8.0), ('bottom', 10.0), ('left', 8.0), ('header', 5.0), ('footer', 5.0)):
        value = margins_raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise EngineError('EXCEL_OPTIONS_INVALID', '页边距必须为数字')
        if not (0 <= float(value) <= 50):
            raise EngineError('EXCEL_OPTIONS_INVALID', '页边距须在 0–50mm 之间')
        margins[key] = float(value)
    if margins['left'] + margins['right'] >= 210 or margins['top'] + margins['bottom'] >= 297:
        raise EngineError('EXCEL_OPTIONS_INVALID', '页边距之和必须小于 A4 纸张尺寸')

    return ExcelOptions(
        sheet_name=sheet_name,
        print_area=print_area,
        print_titles_rows=titles_rows,
        print_titles_columns=titles_columns,
        orientation=orientation,
        paper_size=paper_size,
        fit_to_width=fit_width,
        fit_to_height=fit_height,
        scale_percent=scale_percent,
        gridlines=gridlines,
        margins_mm=margins,
    )


def _apply_excel_options(workbook_path: Path, options: ExcelOptions, workdir: Path) -> Path:
    import openpyxl
    from openpyxl.worksheet.properties import PageSetupProperties
    from openpyxl.worksheet.page import PageMargins

    try:
        # Content-addressed storage has no filename suffix; openpyxl infers the
        # format from the extension, so always hand it real bytes.
        workbook = openpyxl.load_workbook(
            io.BytesIO(Path(workbook_path).read_bytes()), keep_links=False, data_only=False
        )
    except Exception as error:
        raise EngineError('EXCEL_WORKBOOK_INVALID', 'Excel 文件无法读取，请检查是否损坏或受密码保护', retryable=False) from error
    if getattr(workbook, 'vba_archive', None) is not None:
        raise EngineError('OFFICE_MACROS_FORBIDDEN', '不支持包含宏的工作簿', retryable=False)
    try:
        if options.sheet_name:
            matches = [sheet for sheet in workbook.worksheets if sheet.title == options.sheet_name]
            if not matches:
                raise EngineError('EXCEL_SHEET_NOT_FOUND', '指定的工作表不存在', retryable=False)
            sheet = matches[0]
        else:
            sheet = workbook.active
        if sheet is None:
            raise EngineError('EXCEL_WORKBOOK_INVALID', '工作簿没有可打印工作表', retryable=False)

        targets = workbook.worksheets
        if options.print_area or options.sheet_name:
            # A print area is meaningless if other sheets also print. Keep only
            # the selected sheet visible for this conversion.
            for target in workbook.worksheets:
                target.sheet_state = 'visible' if target is sheet else 'hidden'
            workbook.active = workbook.worksheets.index(sheet)
            targets = [sheet]

        if options.print_area:
            sheet.print_area = options.print_area
        if options.print_titles_rows:
            sheet.print_title_rows = options.print_titles_rows
        if options.print_titles_columns:
            sheet.print_title_cols = options.print_titles_columns

        for target in targets:
            target.page_setup.orientation = (
                'landscape' if options.orientation == 'LANDSCAPE' else 'portrait'
            )
            target.page_setup.paperSize = 9  # A4
            target.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
            target.page_setup.fitToWidth = options.fit_to_width
            target.page_setup.fitToHeight = options.fit_to_height
            if options.scale_percent is not None:
                target.page_setup.scale = options.scale_percent
            target.print_options.gridLines = options.gridlines
            target.page_margins = PageMargins(
                left=options.margins_mm['left'] / 25.4,
                right=options.margins_mm['right'] / 25.4,
                top=options.margins_mm['top'] / 25.4,
                bottom=options.margins_mm['bottom'] / 25.4,
                header=options.margins_mm['header'] / 25.4,
                footer=options.margins_mm['footer'] / 25.4,
            )
        output = workdir / f'print-{workbook_path.stem}.xlsx'
        workbook.save(output)
    finally:
        workbook.close()
    return output


def excel_to_pdf(
    source: str | Path,
    workdir: str | Path,
    kind: str,
    options: ExcelOptions,
    *,
    soffice_path: str | None = None,
    timeout: int = 120,
) -> Path:
    source = Path(source)
    workdir = Path(workdir)
    workbook = source
    if kind == 'XLS':
        workbook = office_to_pdf(source, workdir, soffice_path=soffice_path, timeout=timeout, target_format='xlsx')
    prepared = _apply_excel_options(workbook, options, workdir)
    return office_to_pdf(prepared, workdir, soffice_path=soffice_path, timeout=timeout)


# ---------------------------------------------------------------------------
# A4 normalisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizeResult:
    pages: int
    page_geometry: tuple[dict, ...]
    adapted_pages: int


def _is_a4(rect) -> bool:
    return (
        abs(rect.width - A4_WIDTH_PT) <= A4_TOLERANCE_PT
        and abs(rect.height - A4_HEIGHT_PT) <= A4_TOLERANCE_PT
    )


def normalize_pdf(source: str | Path, destination: str | Path, *, max_pages: int = 1000) -> NormalizeResult:
    """Normalize every page into portrait A4, fitting content without cropping."""
    probe = validate_pdf(source, max_pages=max_pages)
    document = pymupdf.open(source)
    output = pymupdf.open()
    geometry: list[dict] = []
    try:
        for index, page in enumerate(document):
            display = page.rect
            rotation = page.rotation
            if _is_a4(display) and rotation == 0:
                output.insert_pdf(document, from_page=index, to_page=index, annots=True)
                geometry.append(
                    {
                        'sourcePage': index + 1,
                        'sourceWidthPt': round(display.width, 3),
                        'sourceHeightPt': round(display.height, 3),
                        'sourceRotation': rotation,
                        'targetWidthPt': round(display.width, 3),
                        'targetHeightPt': round(display.height, 3),
                        'scale': 1.0,
                        'offsetXPt': 0.0,
                        'offsetYPt': 0.0,
                        'adapted': False,
                    }
                )
                continue
            scale = min(A4_WIDTH_PT / display.width, A4_HEIGHT_PT / display.height)
            width, height = display.width * scale, display.height * scale
            x, y = (A4_WIDTH_PT - width) / 2, (A4_HEIGHT_PT - height) / 2
            new_page = output.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
            new_page.show_pdf_page(
                pymupdf.Rect(x, y, x + width, y + height), document, index, keep_proportion=True
            )
            geometry.append(
                {
                    'sourcePage': index + 1,
                    'sourceWidthPt': round(display.width, 3),
                    'sourceHeightPt': round(display.height, 3),
                    'sourceRotation': rotation,
                    'targetWidthPt': A4_WIDTH_PT,
                    'targetHeightPt': A4_HEIGHT_PT,
                    'scale': round(scale, 6),
                    'offsetXPt': round(x, 3),
                    'offsetYPt': round(y, 3),
                    'adapted': True,
                }
            )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        output.save(destination, garbage=4, deflate=True, clean=True)
    finally:
        output.close()
        document.close()

    verified = pymupdf.open(destination)
    try:
        if verified.page_count != probe.pages:
            raise EngineError('ARTIFACT_VERIFY_FAILED', 'PDF 标准化后页数不一致', retryable=True)
        for page in verified:
            if not _is_a4(page.rect):
                raise EngineError('ARTIFACT_VERIFY_FAILED', 'PDF 标准化后页面尺寸不是 A4', retryable=True)
    finally:
        verified.close()
    return NormalizeResult(
        pages=probe.pages,
        page_geometry=tuple(geometry),
        adapted_pages=sum(1 for item in geometry if item['adapted']),
    )


def source_to_normalized_pdf(
    source: str | Path,
    kind: str,
    workdir: str | Path,
    *,
    max_pages: int = 1000,
    soffice_path: str | None = None,
    timeout: int = 120,
    excel_options: Mapping | None = None,
) -> tuple[Path, dict]:
    """Convert an already-inspected source into a normalized A4 PDF."""
    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    kind = kind.upper()
    # Content-addressed storage paths have no extension; supply a synthetic
    # name so OLE (.doc/.xls/.ppt) detection still has the required hint.
    info = inspect_upload(source, f'source.{kind.lower()}', '')
    if info.kind != kind:
        raise EngineError('MIME_MISMATCH', f'文件实际类型为 {info.kind}，与 {kind} 不一致', retryable=False)
    meta: dict = {'sourceKind': kind, 'sourceSha256': info.sha256, 'converter': None, 'excel': None}
    if kind == 'PDF':
        converted = source
        meta['converter'] = 'native-pdf'
    elif kind in OFFICE_KINDS:
        if not resolve_soffice(soffice_path):
            raise EngineError(
                'OFFICE_CONVERTER_MISSING', '服务器未安装 LibreOffice，暂时无法转换 Office 文件', retryable=True
            )
        if kind in ('XLS', 'XLSX'):
            options = validate_excel_options(excel_options)
            converted = excel_to_pdf(source, workdir, kind, options, soffice_path=soffice_path, timeout=timeout)
            meta['excel'] = options.as_manifest()
        else:
            converted = office_to_pdf(source, workdir, soffice_path=soffice_path, timeout=timeout)
        meta['converter'] = 'libreoffice'
    else:
        raise EngineError('UNSUPPORTED_FORMAT', '图片需按照片流程或先转换为 A4 PDF', retryable=False)

    converted_probe = validate_pdf(converted, max_pages=max_pages)
    normalized = workdir / 'normalized.pdf'
    result = normalize_pdf(converted, normalized, max_pages=max_pages)
    meta.update(
        {
            'normalizedPages': result.pages,
            'pageGeometry': list(result.page_geometry),
            'adaptedPages': result.adapted_pages,
            'convertedPages': converted_probe.pages,
            'toolchain': toolchain_versions(soffice_path),
        }
    )
    return normalized, meta


# ---------------------------------------------------------------------------
# Page allocation / artifacts / page-sheet maps
# ---------------------------------------------------------------------------


def validate_page_allocation(total_pages: int, ranges_by_segment: Sequence[Sequence[int]], excluded_pages: Sequence[int]) -> None:
    """Every source page must be printed exactly once or explicitly excluded."""
    if total_pages <= 0:
        raise EngineError('PAGE_ALLOCATION_INVALID', '文档没有可打印页')
    seen: dict[int, int] = {}
    for segment_index, pages in enumerate(ranges_by_segment):
        for page in pages:
            if not isinstance(page, int) or page < 1 or page > total_pages:
                raise EngineError('PAGE_RANGE_INVALID', f'第 {page} 页超出 1–{total_pages} 范围')
            if page in seen:
                raise EngineError('PAGE_ALLOCATION_INVALID', f'第 {page} 页被重复分配')
            seen[page] = segment_index
    excluded = set()
    for page in excluded_pages:
        if not isinstance(page, int) or page < 1 or page > total_pages:
            raise EngineError('PAGE_RANGE_INVALID', f'跳过页 {page} 超出 1–{total_pages} 范围')
        if page in excluded:
            raise EngineError('PAGE_ALLOCATION_INVALID', f'第 {page} 页被重复跳过')
        if page in seen:
            raise EngineError('PAGE_ALLOCATION_INVALID', f'第 {page} 页同时被打印与跳过')
        excluded.add(page)
    missing = [page for page in range(1, total_pages + 1) if page not in seen and page not in excluded]
    if missing:
        preview = ', '.join(str(page) for page in missing[:10])
        raise EngineError('PAGE_ALLOCATION_INVALID', f'第 {preview} 页未分配，请明确跳过或加入分段')


def production_count(pages: int, copies: int, sides: str) -> tuple[int, int, int]:
    if not isinstance(pages, int) or pages < 1 or not isinstance(copies, int) or copies < 1:
        raise EngineError('PAGE_RANGE_INVALID', '页数和份数必须为正整数')
    if sides == 'DUPLEX':
        per_copy = math.ceil(pages / 2)
        return per_copy * 2 * copies, per_copy * copies, (copies if pages % 2 else 0)
    return pages * copies, pages * copies, 0


@dataclass(frozen=True)
class ArtifactBuild:
    pages: int
    sheets: int
    blank_backs: int
    page_map: tuple[dict, ...]
    sha256: str
    path: Path


def build_document_artifact(
    normalized_pdf: str | Path,
    destination: str | Path,
    *,
    segment_id: str,
    pages: Sequence[int],
    sides: str,
    copies: int,
    color_mode: str = 'BW',
    flip: str = 'NONE',
    margins_mm: Mapping[str, float] | None = None,
) -> ArtifactBuild:
    """Generate one task's immutable multi-copy PDF and page/sheet map.

    ``margins_mm`` is the operator-configured minimum printable margin for A4.
    The normalized source is already A4, so content is scaled down just enough
    to stay inside that printable rectangle; zero margins keep the historical
    full-bleed insertion unchanged.
    """
    if sides not in ('SINGLE', 'DUPLEX'):
        raise EngineError('ARTIFACT_INVALID', '单双面设置无效')
    if color_mode not in ('BW', 'COLOR'):
        raise EngineError('ARTIFACT_INVALID', '颜色设置无效')
    if flip not in ('NONE', 'LONG_EDGE', 'SHORT_EDGE'):
        raise EngineError('ARTIFACT_INVALID', '翻转设置无效')
    if not pages:
        raise EngineError('PAGE_RANGE_INVALID', '分段至少需要一页')
    if len(set(pages)) != len(pages):
        raise EngineError('PAGE_RANGE_INVALID', '分段内页码重复')
    if not isinstance(copies, int) or copies < 1 or copies > 999:
        raise EngineError('PAGE_RANGE_INVALID', '份数须在 1–999 之间')

    margins = {}
    for side in ('top', 'right', 'bottom', 'left'):
        raw = 0 if margins_mm is None else margins_mm.get(side, 0)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise EngineError('PAPER_GEOMETRY_INVALID', 'A4 留白必须是数字') from None
        if not math.isfinite(value) or value < 0:
            raise EngineError('PAPER_GEOMETRY_INVALID', 'A4 留白必须是非负数')
        margins[side] = value
    printable = None
    if any(margins.values()):
        printable_width = A4_WIDTH_PT - (margins['left'] + margins['right']) * MM_TO_PT
        printable_height = A4_HEIGHT_PT - (margins['top'] + margins['bottom']) * MM_TO_PT
        if printable_width <= 0.5 or printable_height <= 0.5:
            raise EngineError('PAPER_GEOMETRY_INVALID', 'A4 留白必须小于纸张尺寸')
        printable = pymupdf.Rect(
            margins['left'] * MM_TO_PT,
            margins['top'] * MM_TO_PT,
            A4_WIDTH_PT - margins['right'] * MM_TO_PT,
            A4_HEIGHT_PT - margins['bottom'] * MM_TO_PT,
        )

    source = pymupdf.open(normalized_pdf)
    if printable is not None:
        # show_pdf_page only copies page content. Bake annotations and form
        # fields into content first so printable stamps are not dropped.
        source.bake(annots=True, widgets=True)
    page_count = source.page_count
    for page in pages:
        if not isinstance(page, int) or page < 1 or page > page_count:
            raise EngineError('PAGE_RANGE_INVALID', f'第 {page} 页超出 1–{page_count} 范围')
    expected_pages, expected_sheets, blank_backs = production_count(len(pages), copies, sides)
    output = pymupdf.open()
    page_map: list[dict] = []
    pdf_index = 1
    sheet_ordinal = 1
    try:
        for copy_index in range(1, copies + 1):
            for position, source_page in enumerate(pages):
                if printable is None:
                    output.insert_pdf(source, from_page=source_page - 1, to_page=source_page - 1, annots=True)
                else:
                    output_page = output.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
                    output_page.show_pdf_page(
                        printable, source, source_page - 1, keep_proportion=True)
                if sides == 'DUPLEX':
                    sheet_index = position // 2 + 1
                    side = 'FRONT' if position % 2 == 0 else 'BACK'
                else:
                    sheet_index = position + 1
                    side = 'FRONT'
                page_map.append(
                    {
                        'pdfPageIndex': pdf_index,
                        'pdfPage': pdf_index,
                        'sourcePageIndex': source_page,
                        'sourcePage': source_page,
                        'copyIndex': copy_index,
                        'sheetIndex': sheet_index,
                        'sheetOrdinal': sheet_ordinal,
                        'sheetId': f'c{copy_index}-s{sheet_index}',
                        'side': side,
                        'blank': False,
                    }
                )
                pdf_index += 1
                if side == 'BACK' or (sides == 'SINGLE' and position == len(pages) - 1):
                    sheet_ordinal += 1
            if sides == 'DUPLEX' and len(pages) % 2:
                output.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
                sheet_index = math.ceil(len(pages) / 2)
                page_map.append(
                    {
                        'pdfPageIndex': pdf_index,
                        'pdfPage': pdf_index,
                        'sourcePageIndex': None,
                        'sourcePage': None,
                        'copyIndex': copy_index,
                        'sheetIndex': sheet_index,
                        'sheetOrdinal': sheet_ordinal,
                        'sheetId': f'c{copy_index}-s{sheet_index}',
                        'side': 'BACK',
                        'blank': True,
                    }
                )
                pdf_index += 1
                sheet_ordinal += 1
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        output.save(destination, garbage=4, deflate=True, clean=True)
    finally:
        output.close()
        source.close()

    verified = pymupdf.open(destination)
    try:
        if verified.page_count != expected_pages:
            raise EngineError('ARTIFACT_VERIFY_FAILED', '最终 PDF 页数与计纸结果不一致', retryable=True)
        for page in verified:
            if not _is_a4(page.rect):
                raise EngineError('ARTIFACT_VERIFY_FAILED', '最终 PDF 页面尺寸不是 A4', retryable=True)
    finally:
        verified.close()
    digest = sha256_file(destination)
    return ArtifactBuild(
        pages=expected_pages,
        sheets=expected_sheets,
        blank_backs=blank_backs,
        page_map=tuple(page_map),
        sha256=digest,
        path=Path(destination),
    )


@dataclass(frozen=True)
class ReprintBuild:
    pages: int
    sheets: int
    sha256: str
    selected_sheets: tuple[str, ...]
    path: Path


def build_sheet_reprint_pdf(
    source_pdf: str | Path,
    page_map: Sequence[Mapping],
    sheet_ids: Sequence[str],
    destination: str | Path,
) -> ReprintBuild:
    """Rebuild complete physical sheets (front+back, including a blank back)."""
    requested = list(dict.fromkeys(sheet_ids))
    if not requested:
        raise EngineError('REPRINT_SHEETS_REQUIRED', '请选择需要补打的实体纸张')
    by_sheet: dict[str, list[Mapping]] = {}
    for entry in page_map:
        by_sheet.setdefault(str(entry['sheetId']), []).append(entry)
    unknown = [sheet for sheet in requested if sheet not in by_sheet]
    if unknown:
        raise EngineError('REPRINT_SHEET_NOT_FOUND', f'纸张 {unknown[0]} 不属于该打印任务')
    seen: set[int] = set()
    ordered: list[int] = []
    # Restore document order regardless of the order the UI listed sheets in.
    for sheet in sorted(
        requested,
        key=lambda value: min(
            int(entry.get('pdfPageIndex') or 0) for entry in by_sheet[value]
        ),
    ):
        for entry in sorted(by_sheet[sheet], key=lambda row: int(row.get('pdfPageIndex') or 0)):
            index = int(entry['pdfPageIndex'])
            if index not in seen:
                seen.add(index)
                ordered.append(index)
    source = pymupdf.open(source_pdf)
    output = pymupdf.open()
    try:
        for pdf_page in ordered:
            index = int(pdf_page) - 1
            if index < 0 or index >= source.page_count:
                raise EngineError('ARTIFACT_VERIFY_FAILED', '页纸映射指向不存在的页', retryable=True)
            output.insert_pdf(source, from_page=index, to_page=index, annots=True)
        # Preserve document order inside each copy even if the caller listed
        # sheets out of order; physical reprints still contain complete sheets.
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        output.save(destination, garbage=4, deflate=True, clean=True)
    finally:
        output.close()
        source.close()
    return ReprintBuild(
        pages=len(ordered),
        sheets=len(requested),
        sha256=sha256_file(destination),
        selected_sheets=tuple(requested),
        path=Path(destination),
    )


# ---------------------------------------------------------------------------
# Photo paper geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperSpec:
    width_mm: float
    height_mm: float
    margins_mm: Mapping[str, float]

    @property
    def print_width_mm(self) -> float:
        return self.width_mm - self.margins_mm['left'] - self.margins_mm['right']

    @property
    def print_height_mm(self) -> float:
        return self.height_mm - self.margins_mm['top'] - self.margins_mm['bottom']

    def validate(self) -> None:
        if not (math.isfinite(self.width_mm) and math.isfinite(self.height_mm)) or self.width_mm <= 0 or self.height_mm <= 0:
            raise EngineError('PHOTO_GEOMETRY_INVALID', '照片纸张尺寸无效')
        for key in ('top', 'right', 'bottom', 'left'):
            value = self.margins_mm.get(key)
            if value is None or not math.isfinite(value) or value < 0:
                raise EngineError('PHOTO_GEOMETRY_INVALID', '照片留白必须为非负毫米值')
        if self.print_width_mm <= 0 or self.print_height_mm <= 0:
            raise EngineError('PHOTO_GEOMETRY_INVALID', '照片留白超过纸张尺寸')


def photo_crop(
    image_width: float,
    image_height: float,
    print_width: float,
    print_height: float,
    zoom: float,
    x: float,
    y: float,
) -> dict:
    """Mirror the frontend photo-layout crop so both sides agree."""
    if image_width <= 0 or image_height <= 0 or print_width <= 0 or print_height <= 0:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪尺寸无效')
    scale = max(print_width / image_width, print_height / image_height) * max(zoom, 1)
    width = min(1.0, print_width / (image_width * scale))
    height = min(1.0, print_height / (image_height * scale))
    return {
        'x': ((1 - width) * x) / 100,
        'y': ((1 - height) * y) / 100,
        'width': width,
        'height': height,
    }


@dataclass(frozen=True)
class PhotoRender:
    pages: int
    copies: int
    width_mm: float
    height_mm: float
    margins_mm: dict
    print_width_mm: float
    print_height_mm: float
    crop: dict | None
    crop_adjusted: bool
    rotation_degrees: int
    orientation: str
    fit: str
    source_width: int
    source_height: int
    exif_orientation: int | None
    dpi: int
    warnings: tuple[dict, ...]
    path: Path


def _normalise_crop(crop: Mapping | None, width: float, height: float, target_aspect: float) -> tuple[dict, bool] | None:
    if crop is None:
        return None
    if not isinstance(crop, Mapping):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪参数无效')
    try:
        x = float(crop['x'])
        y = float(crop['y'])
        crop_width = float(crop['width'])
        crop_height = float(crop['height'])
    except (KeyError, TypeError, ValueError) as error:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪参数不完整') from error
    if not all(math.isfinite(value) for value in (x, y, crop_width, crop_height)):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪参数无效')
    if x < -1e-6 or y < -1e-6 or crop_width <= 0 or crop_height <= 0:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪框必须位于图片内')
    if x + crop_width > 1 + 1e-6 or y + crop_height > 1 + 1e-6:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪框超出图片范围')
    pixel_width = crop_width * width
    pixel_height = crop_height * height
    if pixel_width < 1 or pixel_height < 1:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪区域过小')
    aspect = pixel_width / pixel_height
    if abs(aspect - target_aspect) / target_aspect > ASPECT_TOLERANCE:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '裁剪框比例与可打印区域不一致，请重新调整构图')
    adjusted = False
    if abs(aspect - target_aspect) > 1e-4:
        # Expand (never crop further) to the exact print-area aspect.
        if aspect < target_aspect:
            new_pixel_width = min(width, pixel_height * target_aspect)
            new_pixel_height = new_pixel_width / target_aspect
        else:
            new_pixel_height = min(height, pixel_width / target_aspect)
            new_pixel_width = new_pixel_height * target_aspect
        centre_x = (x + crop_width / 2) * width
        centre_y = (y + crop_height / 2) * height
        left = centre_x - new_pixel_width / 2
        top = centre_y - new_pixel_height / 2
        left = max(0.0, min(width - new_pixel_width, left))
        top = max(0.0, min(height - new_pixel_height, top))
        x, y = left / width, top / height
        crop_width, crop_height = new_pixel_width / width, new_pixel_height / height
        adjusted = True
    return {'x': x, 'y': y, 'width': crop_width, 'height': crop_height}, adjusted


def validate_photo_crop(
    image_width: float,
    image_height: float,
    print_width_mm: float,
    print_height_mm: float,
    crop: Mapping | None,
) -> dict:
    if print_width_mm <= 0 or print_height_mm <= 0:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片可打印区域无效')
    result = _normalise_crop(crop, image_width, image_height, print_width_mm / print_height_mm)
    if result is None:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '铺满可打印区域时必须提供裁剪框')
    return result[0]


def render_photo_pdf(
    image_path: str | Path,
    destination: str | Path,
    *,
    copies: int,
    orientation: str,
    fit: str,
    rotation_degrees: int,
    crop: Mapping | None,
    paper: PaperSpec,
    dpi: int = 300,
) -> PhotoRender:
    paper.validate()
    if not isinstance(copies, int) or copies < 1 or copies > 999:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片份数须在 1–999 之间')
    if orientation not in ('PORTRAIT', 'LANDSCAPE'):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片纸张方向无效')
    if fit not in ('COVER', 'CONTAIN'):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片适配方式无效')
    if rotation_degrees not in (0, 90, 180, 270):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '照片旋转角度无效')
    if fit == 'CONTAIN' and crop is not None:
        raise EngineError('PHOTO_GEOMETRY_INVALID', '完整保留模式不能同时裁剪')
    if not (72 <= dpi <= 600):
        raise EngineError('PHOTO_GEOMETRY_INVALID', '渲染分辨率超出安全范围')

    validate_image(image_path, None)
    with Image.open(image_path) as opened:
        exif = opened.getexif()
        exif_orientation = int(exif.get(274)) if exif and exif.get(274) else None
        image = ImageOps.exif_transpose(opened)
        if image.mode in ('RGBA', 'LA', 'P'):
            image = image.convert('RGBA')
            background = Image.new('RGBA', image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, image)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        if rotation_degrees:
            image = image.rotate(-rotation_degrees, expand=True)
        source_width, source_height = image.size
        target_aspect = paper.print_width_mm / paper.print_height_mm
        result_crop = None
        crop_adjusted = False
        if fit == 'COVER':
            if crop is None:
                crop = {'x': 0.0, 'y': 0.0, 'width': 1.0, 'height': 1.0}
            normalised = _normalise_crop(crop, source_width, source_height, target_aspect)
            assert normalised is not None
            result_crop, crop_adjusted = normalised
        elif crop is not None:
            raise EngineError('PHOTO_GEOMETRY_INVALID', '完整保留模式不能同时裁剪')

        width_px = max(1, round(paper.width_mm / 25.4 * dpi))
        height_px = max(1, round(paper.height_mm / 25.4 * dpi))
        print_left = round(paper.margins_mm['left'] / 25.4 * dpi)
        print_top = round(paper.margins_mm['top'] / 25.4 * dpi)
        print_width = max(1, round(paper.print_width_mm / 25.4 * dpi))
        print_height = max(1, round(paper.print_height_mm / 25.4 * dpi))
        canvas = Image.new('RGB', (width_px, height_px), (255, 255, 255))
        if fit == 'COVER' and result_crop is not None:
            box = (
                max(0, round(result_crop['x'] * source_width)),
                max(0, round(result_crop['y'] * source_height)),
                min(source_width, round((result_crop['x'] + result_crop['width']) * source_width)),
                min(source_height, round((result_crop['y'] + result_crop['height']) * source_height)),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                raise EngineError('PHOTO_GEOMETRY_INVALID', '照片裁剪区域为空')
            region = image.crop(box).resize((print_width, print_height), Image.Resampling.LANCZOS)
            canvas.paste(region, (print_left, print_top))
        else:
            scale = min(print_width / source_width, print_height / source_height)
            draw_width = max(1, round(source_width * scale))
            draw_height = max(1, round(source_height * scale))
            resized = image.resize((draw_width, draw_height), Image.Resampling.LANCZOS)
            offset_x = print_left + (print_width - draw_width) // 2
            offset_y = print_top + (print_height - draw_height) // 2
            canvas.paste(resized, (offset_x, offset_y))
        image.close()

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Write one page, then duplicate it as real PDF pages. Building every
        # copy as an in-memory Pillow image would exhaust memory at 999 copies.
        single = destination.with_name(f'.{destination.name}.single.pdf')
        try:
            canvas.save(
                single,
                'PDF',
                resolution=float(dpi),
                quality=95,
                optimize=True,
            )
            canvas.close()
            source_document = pymupdf.open(single)
            output_document = pymupdf.open()
            try:
                for _ in range(copies):
                    output_document.insert_pdf(source_document)
                output_document.save(destination, garbage=4, deflate=True, clean=True)
            finally:
                output_document.close()
                source_document.close()
        finally:
            single.unlink(missing_ok=True)
            canvas.close()

    verified = pymupdf.open(destination)
    try:
        if verified.page_count != copies:
            raise EngineError('ARTIFACT_VERIFY_FAILED', '照片 PDF 页数与份数不一致', retryable=True)
        expected_width = paper.width_mm * MM_TO_PT
        expected_height = paper.height_mm * MM_TO_PT
        for page in verified:
            if abs(page.rect.width - expected_width) > 0.6 or abs(page.rect.height - expected_height) > 0.6:
                raise EngineError('ARTIFACT_VERIFY_FAILED', '照片 PDF 纸张尺寸不一致', retryable=True)
    finally:
        verified.close()

    return PhotoRender(
        pages=copies,
        copies=copies,
        width_mm=paper.width_mm,
        height_mm=paper.height_mm,
        margins_mm=dict(paper.margins_mm),
        print_width_mm=paper.print_width_mm,
        print_height_mm=paper.print_height_mm,
        crop=result_crop,
        crop_adjusted=crop_adjusted,
        rotation_degrees=rotation_degrees,
        orientation=orientation,
        fit=fit,
        source_width=source_width,
        source_height=source_height,
        exif_orientation=exif_orientation,
        dpi=dpi,
        warnings=(),
        path=Path(destination),
    )


# ---------------------------------------------------------------------------
# Grayscale (BW) conversion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrayscaleResult:
    method: str
    rasterized: bool
    warning: dict | None
    sha256: str
    path: Path


def convert_pdf_to_grayscale(
    source: str | Path,
    destination: str | Path,
    *,
    gs_path: str | None = None,
    timeout: int = 120,
    dpi: int = 300,
) -> GrayscaleResult:
    """Prefer Ghostscript's vector-preserving DeviceGray output.

    Only when no Ghostscript binary exists do we rasterize; that path returns a
    machine-readable warning so the UI can demand a fresh preview confirmation.
    """
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    probe = validate_pdf(source, max_pages=100_000)
    binary = resolve_ghostscript(gs_path)
    if binary:
        argv = [
            binary,
            '-q',
            '-dNOPAUSE',
            '-dBATCH',
            '-dSAFER',
            '-sDEVICE=pdfwrite',
            '-dCompatibilityLevel=1.7',
            '-dAutoRotatePages=/None',
            '-sColorConversionStrategy=Gray',
            '-dProcessColorModel=/DeviceGray',
            f'-dJPEGQ=95',
            f'-sOutputFile={destination}',
            str(source),
        ]
        result = _run(argv, timeout=timeout, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')}, cwd=destination.parent)
        if result.returncode != 0 or not destination.is_file():
            raise EngineError(
                'GRAYSCALE_CONVERSION_FAILED',
                '黑白转换失败，请稍后重试',
                retryable=True,
                details={'stderr': (result.stderr or b'').decode('utf-8', 'replace')[-400:]},
            )
        verified = pymupdf.open(destination)
        try:
            if verified.page_count != probe.pages:
                raise EngineError('ARTIFACT_VERIFY_FAILED', '黑白转换后页数不一致', retryable=True)
        finally:
            verified.close()
        return GrayscaleResult(
            method='ghostscript',
            rasterized=False,
            warning=None,
            sha256=sha256_file(destination),
            path=destination,
        )

    # Honest fallback: fixed-DPI raster. Text is no longer vector; warn loudly.
    document = pymupdf.open(source)
    output = pymupdf.open()
    try:
        for page in document:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            image = Image.frombytes('L', (pixmap.width, pixmap.height), pixmap.samples)
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=92)
            image.close()
            pdf_page = output.new_page(width=page.rect.width, height=page.rect.height)
            pdf_page.insert_image(pdf_page.rect, stream=buffer.getvalue())
        output.save(destination, garbage=4, deflate=True, clean=True)
    finally:
        output.close()
        document.close()
    warning = {
        'code': 'BW_GRAYSCALE_RASTERIZED',
        'message': '服务器未提供矢量灰度转换器，黑白文件已按固定分辨率栅格化，请重新预览确认清晰度',
        'dpi': dpi,
    }
    return GrayscaleResult(
        method='pymupdf-raster',
        rasterized=True,
        warning=warning,
        sha256=sha256_file(destination),
        path=destination,
    )


# ---------------------------------------------------------------------------
# Immutable publishing
# ---------------------------------------------------------------------------


def publish_file(temporary: str | Path, final: str | Path, expected_sha256: str) -> str:
    """Verify then atomically publish an immutable artifact."""
    temporary = Path(temporary)
    final = Path(final)
    expected = (expected_sha256 or '').lower()
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise EngineError('ARTIFACT_VERIFY_FAILED', '产物摘要格式无效', retryable=True)
    if not temporary.is_file():
        raise EngineError('ARTIFACT_VERIFY_FAILED', '产物临时文件不存在', retryable=True)
    actual = sha256_file(temporary)
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise EngineError(
            'ARTIFACT_VERIFY_FAILED',
            '产物校验失败，未发布',
            retryable=True,
            details={'expected': expected, 'actual': actual},
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        existing = sha256_file(final)
        if existing != expected:
            temporary.unlink(missing_ok=True)
            raise EngineError(
                'ARTIFACT_VERIFY_FAILED',
                '同名产物已存在且摘要不一致，拒绝覆盖',
                retryable=True,
                details={'expected': expected, 'existing': existing},
            )
        temporary.unlink(missing_ok=True)
        return expected
    with open(temporary, 'rb') as handle:
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, final)
    except OSError:
        # Different filesystems: copy into the destination directory first.
        staging = final.with_name(f'.{final.name}.{os.getpid()}.part')
        shutil.copyfile(temporary, staging)
        with open(staging, 'rb') as handle:
            os.fsync(handle.fileno())
        shutil.copystat(temporary, staging)
        os.replace(staging, final)
        temporary.unlink(missing_ok=True)
    try:
        directory = os.open(final.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:  # pragma: no cover - not all platforms allow directory fsync
        pass
    try:
        os.chmod(final, 0o600)
    except OSError:  # pragma: no cover
        pass
    return expected
