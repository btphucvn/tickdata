"""
Module 2 — Tick Store.

Chuẩn hoá tick từ mọi nguồn về 1 schema, lưu hiệu quả theo (symbol, tháng),
và query nhanh theo khoảng thời gian. Có manifest SQLite để biết "đã có gì,
thiếu khoảng nào".
"""

from __future__ import annotations

from tdsclone.store.tickstore import TickStore, CoverageRange

__all__ = ["TickStore", "CoverageRange"]
