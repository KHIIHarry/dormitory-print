"""Strict DTOs for the staff benefits policy surface."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field as PField

from .core import DTO

# 折扣减免比例：2000 表示减免 20%（员工支付 80%）。
GradeBps = Annotated[int, PField(ge=0, le=10000)]


class BenefitsPolicyDTO(DTO):
    id: str
    version: int = PField(ge=0)
    description: str = ''
    grades: list[GradeBps | None] = PField(min_length=6, max_length=6)
    updated_at: datetime


class BenefitsPolicyInput(DTO):
    expected_version: int = PField(ge=0)
    grades: list[GradeBps | None] = PField(min_length=6, max_length=6)
    reason: str = PField(default='', max_length=500)
