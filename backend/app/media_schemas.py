"""Strict DTOs for the media/upload/preparation surface.

Field aliases follow docs/openapi.yaml and src/lib/types.ts. Upload resume and
cancel are additive V1 contracts (Q28); they never replace the OpenAPI asset,
preparation, quote or artifact shapes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from .core import DTO
from .growth_schemas import PromotionIntent, QuoteDTO

ColorMode = Literal['BW', 'COLOR']
Sides = Literal['SINGLE', 'DUPLEX']
Flip = Literal['NONE', 'LONG_EDGE', 'SHORT_EDGE']
BindingType = Literal['NONE', 'CORNER', 'SIDE']
PhotoFit = Literal['COVER', 'CONTAIN']
Orientation = Literal['PORTRAIT', 'LANDSCAPE']
AssetStatus = Literal['UPLOADING', 'QUEUED', 'PROCESSING', 'READY', 'FAILED', 'DELETED', 'PURGED']
PreparationStatus = Literal['QUEUED', 'PROCESSING', 'READY', 'FAILED', 'CANCELLED']
AssetPurpose = Literal[
    'PRINT_SOURCE',
    'PRINT_NORMALIZED',
    'PRINT_FINAL',
    'PRINT_REPRINT',
    'PUBLIC_WECHAT_QR',
    'PUBLIC_MAP',
    'DELIVERY_PROOF',
]
UploadSessionStatus = Literal['UPLOADING', 'COMPLETED', 'CANCELLED', 'EXPIRED']

SHA256_PATTERN = r'^[a-f0-9]{64}$'


class PageRange(DTO):
    from_page: int = Field(alias='from', ge=1)
    to_page: int = Field(alias='to', ge=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.to_page < self.from_page:
            raise ValueError('页码范围结束页不能小于起始页')
        return self


class SegmentInput(DTO):
    client_segment_id: str = Field(min_length=1, max_length=64)
    ranges: list[PageRange] = Field(min_length=1, max_length=200)
    color_mode: ColorMode
    sides: Sides
    flip: Flip
    copies: int = Field(ge=1, le=999)

    @model_validator(mode='after')
    def flip_matches_sides(self):
        if self.sides == 'SINGLE' and self.flip != 'NONE':
            raise ValueError('单面打印不能设置翻转方式')
        if self.sides == 'DUPLEX' and self.flip == 'NONE':
            raise ValueError('双面打印必须选择长边或短边翻转')
        return self


class DocumentInput(DTO):
    client_file_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    kind: Literal['DOCUMENT']
    segments: list[SegmentInput] = Field(min_length=1, max_length=100)
    excluded_pages: list[int] = Field(default_factory=list, max_length=10000)
    binding_type: BindingType = 'NONE'

    @field_validator('excluded_pages')
    @classmethod
    def excluded_positive(cls, value):
        if any(not isinstance(page, int) or page < 1 for page in value):
            raise ValueError('跳过页必须为正整数')
        if len(set(value)) != len(value):
            raise ValueError('跳过页不能重复')
        return value

    @model_validator(mode='after')
    def segment_ids_unique(self):
        ids = [segment.client_segment_id for segment in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError('分段标识不能重复')
        return self


class Crop(DTO):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def inside_image(self):
        if self.x + self.width > 1 + 1e-6 or self.y + self.height > 1 + 1e-6:
            raise ValueError('裁剪框必须位于图片内')
        return self


class PhotoInput(DTO):
    client_file_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    kind: Literal['PHOTO']
    copies: int = Field(ge=1, le=999)
    orientation: Orientation
    fit: PhotoFit
    rotation_degrees: Literal[0, 90, 180, 270]
    crop: Crop | None = None

    @model_validator(mode='after')
    def crop_matches_fit(self):
        if self.fit == 'COVER' and self.crop is None:
            raise ValueError('铺满可打印区域时必须提供裁剪框')
        if self.fit == 'CONTAIN' and self.crop is not None:
            raise ValueError('完整保留模式不能同时裁剪')
        return self


PreparationFile = Annotated[Union[DocumentInput, PhotoInput], Field(discriminator='kind')]


class PreparationInput(DTO):
    files: list[PreparationFile] = Field(min_length=1, max_length=100)
    replaces_order_id: str | None = Field(default=None, max_length=64)
    expected_order_version: int | None = Field(default=None, ge=0)
    promotion_intent: PromotionIntent | None = None

    @model_validator(mode='after')
    def unique_files_and_binding_rules(self):
        ids = [item.client_file_id for item in self.files]
        if len(set(ids)) != len(ids):
            raise ValueError('clientFileId 不能重复')
        for item in self.files:
            if isinstance(item, DocumentInput) and item.binding_type != 'NONE':
                copies = {segment.copies for segment in item.segments}
                if len(copies) > 1:
                    raise ValueError('同一文件各分段份数不一致，不能装订；请先统一份数')
        return self


class AssetUploadResult(DTO):
    asset_id: str
    status: AssetStatus
    kind: str
    name: str
    size: int
    sha256: str
    pages: int | None = None
    duplicate: bool = False
    url: str | None = None
    normalized_url: str | None = None


class AssetView(DTO):
    id: str
    asset_id: str
    status: AssetStatus
    purpose: str
    name: str
    mime: str
    size: int
    sha256: str
    pages: int | None = None
    kind: str | None = None
    error: str | None = None
    failure_code: str | None = None
    retryable: bool = False
    dimensions: dict | None = None
    url: str | None = None
    normalized_url: str | None = None
    preview_url: str | None = None
    cancel_requested: bool = False
    normalized_asset_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None


class AssetList(DTO):
    items: list[AssetView]
    total: int


class ByteRange(DTO):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class UploadSessionCreate(DTO):
    name: str = Field(min_length=1, max_length=255)
    purpose: AssetPurpose = 'PRINT_SOURCE'
    total_bytes: int = Field(ge=1, le=2 * 1024 * 1024 * 1024)
    sha256: str = Field(pattern=SHA256_PATTERN)
    mime: str | None = Field(default=None, max_length=120)
    excel_options: dict | None = None


class UploadSessionView(DTO):
    id: str
    status: UploadSessionStatus
    purpose: str
    name: str
    total_bytes: int
    received_bytes: int
    next_offset: int
    chunk_size: int
    ranges: list[ByteRange]
    sha256: str
    mime: str | None = None
    expires_at: datetime
    asset_id: str | None = None
    cancel_requested: bool = False


class UploadSessionComplete(DTO):
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)


class ValidationView(DTO):
    ok: bool
    errors: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)


class ArtifactView(DTO):
    id: str
    file_id: str
    segment_id: str
    sha256: str
    url: str
    pages: int
    sheets: int
    blank_backs: int
    range: str
    copies: int
    color: str
    sides: str
    flip: str
    content_revision: int = 1
    settings_hash: str
    input_sha256: str | None = None
    toolchain: dict = Field(default_factory=dict)
    warnings: list[dict] = Field(default_factory=list)
    page_map: list[dict] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    non_billable: bool = False
    reprint_of_artifact_id: str | None = None
    reprint_of_task_id: str | None = None
    created_at: datetime


class PreparationFileView(DTO):
    client_file_id: str
    asset_id: str
    kind: str
    name: str
    status: str
    pages: int | None = None
    excluded_pages: list[int] = Field(default_factory=list)
    binding_type: str = 'NONE'
    segments: list[dict] = Field(default_factory=list)
    copies: int | None = None
    orientation: str | None = None
    fit: str | None = None
    rotation_degrees: int | None = None
    crop: dict | None = None


class PreparationView(DTO):
    id: str
    version: int
    status: PreparationStatus
    content_revision: int
    artifacts: list[ArtifactView] = Field(default_factory=list)
    validation: ValidationView = Field(default_factory=ValidationView)
    quote: QuoteDTO | None = None
    files: list[PreparationFileView] = Field(default_factory=list)
    expires_at: datetime
    cancel_requested: bool = False
    checkout_account_id: str | None = None
    checkout_auth_kind: str | None = None
    source_grant_id: str | None = None
    can_adopt: bool = False
    failure_code: str | None = None
    failure_message: str | None = None
    replaces_order_id: str | None = None
    expected_order_version: int | None = None
    created_at: datetime
    updated_at: datetime


class PreparationList(DTO):
    items: list[PreparationView]
    total: int


class ConfirmCheckoutInput(DTO):
    expected_version: int = Field(ge=0)
    confirm: Literal[True]


class ReprocessResult(DTO):
    id: str
    status: PreparationStatus
    cancel_requested: bool = False
