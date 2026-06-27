"""
Module 1 — Tick Data Downloader.

Tải tick-data lịch sử từ nhiều nguồn về :class:`~tdsclone.model.TickFrame`.

* :mod:`tdsclone.download.dukascopy` — nguồn chính (.bi5 LZMA, miễn phí, chất lượng cao).
* :mod:`tdsclone.download.histdata`  — CSV M1/tick từ HistData.com.
"""

from __future__ import annotations

from tdsclone.download.dukascopy import (
    DownloadReport,
    DukascopyDownloader,
    decode_bi5,
)

__all__ = ["DukascopyDownloader", "DownloadReport", "decode_bi5"]
