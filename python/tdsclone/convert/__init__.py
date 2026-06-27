"""
Module 3 — FXT / HST Builder + SpreadModel.

"Trái tim" convert: biến tick chuẩn hoá thành định dạng MT4 Strategy Tester đọc
được (``.fxt``) và lịch sử chart (``.hst``), với khả năng mô phỏng variable spread.
"""

from __future__ import annotations

from tdsclone.convert.spread_model import (
    SpreadModel,
    RealSpread,
    FixedSpread,
    RandomSpread,
    SessionSpread,
    NewsWidenSpread,
)
from tdsclone.convert.fxt import FxtBuilder, FXT_VERSION
from tdsclone.convert.hst import HstBuilder, Bar

__all__ = [
    "SpreadModel", "RealSpread", "FixedSpread", "RandomSpread",
    "SessionSpread", "NewsWidenSpread",
    "FxtBuilder", "FXT_VERSION", "HstBuilder", "Bar",
]
