"""
Orchestrator dùng chung cho CLI (Module 6 headless) và GUI (PySide6).

Gói các thao tác cấp cao "một nút bấm" mà cả dòng lệnh lẫn giao diện đều gọi, để
tránh trùng lặp logic:

    download_range()  : tải Dukascopy [symbol, start, end] -> nạp vào TickStore.
    build_fxt()       : query store -> dựng .fxt (+ tuỳ chọn .hst, .tdspread).
    make_spread_model(): dựng SpreadModel từ tên + tham số (cho UI chọn dropdown).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tdsclone.convert.fxt import FxtBuilder
from tdsclone.convert.hst import HstBuilder
from tdsclone.convert.spread_file import export_spread_file
from tdsclone.convert.spread_model import (
    SpreadModel, RealSpread, FixedSpread, RandomSpread, SessionSpread, NewsWidenSpread,
)
from tdsclone.download.dukascopy import DukascopyDownloader, DownloadReport
from tdsclone.store.tickstore import TickStore
from tdsclone.symbols import get_symbol_spec

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None] | None


@dataclass
class BuildResult:
    """Kết quả build (đường dẫn các file sinh ra)."""

    fxt_path: Path
    hst_path: Path | None = None
    spread_path: Path | None = None
    n_ticks: int = 0


# =============================================================================
#  Spread model factory (cho UI)
# =============================================================================

def make_spread_model(kind: str, **params) -> SpreadModel:
    """
    Dựng SpreadModel từ tên hiển thị trong UI.

    kind ∈ {real, fixed, random, session, news}. ``params`` là các tham số tương ứng,
    ví dụ make_spread_model("fixed", points=15).
    """
    kind = kind.lower()
    if kind == "real":
        return RealSpread(min_points=params.get("min_points", 0.0),
                          multiplier=params.get("multiplier", 1.0))
    if kind == "fixed":
        return FixedSpread(points=params.get("points", 10.0))
    if kind == "random":
        return RandomSpread(min_points=params.get("min_points", 8.0),
                            max_points=params.get("max_points", 20.0),
                            seed=params.get("seed", 42))
    if kind == "session":
        return SessionSpread()
    if kind == "news":
        return NewsWidenSpread()
    raise ValueError(f"Spread model không hợp lệ: {kind!r}")


# =============================================================================
#  Download
# =============================================================================

def download_range(symbol: str, start: datetime, end: datetime,
                   store: TickStore, cache_dir: Path | str = "raw",
                   max_workers: int = 8,
                   progress: ProgressCb = None) -> DownloadReport:
    """
    Tải Dukascopy [symbol, start, end] và nạp vào ``store``.

    Trả về DownloadReport. start/end naive được coi là UTC.
    """
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    dl = DukascopyDownloader(cache_dir=cache_dir, max_workers=max_workers)
    ticks, report = dl.download(symbol, start, end, progress=progress)
    if not ticks.is_empty():
        store.ingest(symbol, ticks)
    return report


# =============================================================================
#  Build FXT/HST
# =============================================================================

def build_fxt(symbol: str, period: int, start: datetime, end: datetime,
              store: TickStore, spread_model: SpreadModel,
              out_dir: Path | str, also_hst: bool = True,
              also_spread_file: bool = True, model: int = 0) -> BuildResult:
    """
    Query store [symbol, start, end] -> dựng .fxt (+ tuỳ chọn .hst, .tdspread).

    Tham số:
        period        : timeframe phút.
        spread_model  : mô hình spread áp dụng.
        out_dir       : thư mục xuất (nên là <terminal>\\tester\\history cho .fxt).
        also_hst      : sinh kèm .hst cho chart.
        also_spread_file: sinh kèm .tdspread cho EA DLL (Module 4 / C2).
    """
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    out_dir = Path(out_dir)

    ticks = store.query(symbol, start, end)
    if ticks.is_empty():
        raise ValueError(
            f"Không có tick cho {symbol} trong khoảng yêu cầu — hãy download trước."
        )

    fxt_path = FxtBuilder().build(symbol, period, ticks, spread_model,
                                  out_dir=out_dir, model=model)
    result = BuildResult(fxt_path=fxt_path, n_ticks=len(ticks))

    if also_hst:
        result.hst_path = HstBuilder().build_from_ticks(
            symbol, period, ticks, spread_model, out_dir=out_dir)

    if also_spread_file:
        sp_path = out_dir / f"{symbol.upper()}{period}.tdspread"
        result.spread_path = export_spread_file(sp_path, symbol, ticks, spread_model)

    return result


# =============================================================================
#  Helpers
# =============================================================================

def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
