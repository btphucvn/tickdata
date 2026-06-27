"""
Module 1 — HistData.com importer (mục 1.2 kiến trúc).

HistData.com phát hành data miễn phí nhưng KHÔNG có API tải tự động tiện như
Dukascopy (phải đăng ký + tải ZIP thủ công theo tháng). Vì vậy module này tập
trung vào việc **parse file CSV đã tải về** thành :class:`TickFrame`.

Hỗ trợ 2 định dạng phổ biến của HistData:

1) Tick data (ASCII "Tick Data"):
       YYYYMMDD HHMMSSmmm,BID,ASK,VOLUME
   ví dụ:  20240102 000000123,1.10395,1.10410,0

2) M1 bar (ASCII "1 Minute Bar Data"):
       YYYYMMDD HHMMSS,OPEN,HIGH,LOW,CLOSE,VOLUME
   -> không có bid/ask riêng; ta coi giá là "mid", spread = 0, mỗi bar sinh 1 tick
      tại thời điểm mở (đủ để dựng .hst, không lý tưởng cho .fxt tick-accurate).

Mốc thời gian HistData là **EST/EDT** đối với một số bộ; tham số ``tz_offset_minutes``
cho phép dịch về UTC. Mặc định coi input đã là UTC (offset 0) — chỉnh theo bộ data.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tdsclone.model import TickFrame, US_PER_MS, US_PER_SEC

logger = logging.getLogger(__name__)


def _to_utc_us(date_str: str, time_str: str, tz_offset_minutes: int) -> int:
    """
    Chuyển 'YYYYMMDD' + 'HHMMSS[mmm]' (giờ địa phương) -> epoch micro-giây UTC.
    """
    year = int(date_str[0:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    hour = int(time_str[0:2])
    minute = int(time_str[2:4])
    second = int(time_str[4:6])
    millis = int(time_str[6:9]) if len(time_str) >= 9 else 0

    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    dt -= timedelta(minutes=tz_offset_minutes)  # đưa giờ địa phương về UTC
    return int(dt.timestamp()) * US_PER_SEC + millis * US_PER_MS


def parse_tick_csv(path: Path | str, tz_offset_minutes: int = 0) -> TickFrame:
    """
    Parse file 'Tick Data' của HistData -> TickFrame.

    Dòng mẫu: ``20240102 000000123,1.10395,1.10410,0``
    """
    tf = TickFrame()
    with open(path, "r", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or len(row) < 3:
                continue
            stamp = row[0].strip()
            try:
                date_str, time_str = stamp.split(" ")
                ts_us = _to_utc_us(date_str, time_str, tz_offset_minutes)
                bid = float(row[1])
                ask = float(row[2])
                vol = float(row[3]) if len(row) > 3 and row[3] else 0.0
            except (ValueError, IndexError) as exc:
                logger.debug("Bỏ qua dòng hỏng %r: %s", row, exc)
                continue
            tf.append(ts_us, bid=bid, ask=ask, bid_vol=vol, ask_vol=vol)
    tf.sort()
    logger.info("HistData tick: đọc %d tick từ %s", len(tf), path)
    return tf


def parse_m1_csv(
    path: Path | str,
    tz_offset_minutes: int = 0,
    synthetic_spread_points: float = 0.0,
    point: float = 0.00001,
) -> TickFrame:
    """
    Parse file 'M1 Bar Data' -> TickFrame (1 tick / bar tại thời điểm mở).

    Vì M1 không có bid/ask, ta coi giá close là "mid" và (tuỳ chọn) tách spread
    tổng hợp: bid = mid - sp/2, ask = mid + sp/2 với sp = points * point.
    Dùng cho chart/.hst; KHÔNG dùng cho .fxt tick-accurate.

    Dòng mẫu: ``20240102 000000,1.10400,1.10420,1.10390,1.10410,0``
    """
    tf = TickFrame()
    half = synthetic_spread_points * point / 2.0
    with open(path, "r", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")  # M1 HistData dùng ';'
        for row in reader:
            # Một số bộ dùng dấu phẩy; tự fallback.
            if len(row) == 1:
                row = row[0].split(",")
            if len(row) < 5:
                continue
            try:
                date_str, time_str = row[0].strip().split(" ")
                ts_us = _to_utc_us(date_str, time_str, tz_offset_minutes)
                close = float(row[4])
            except (ValueError, IndexError):
                continue
            tf.append(ts_us, bid=close - half, ask=close + half)
    tf.sort()
    logger.info("HistData M1: đọc %d bar từ %s", len(tf), path)
    return tf
