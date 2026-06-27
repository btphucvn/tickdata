"""
Module 1 — Dukascopy downloader & decoder (mục 1.3 kiến trúc).

Dukascopy phát hành tick-data MIỄN PHÍ, mỗi file = 1 GIỜ, nén bằng LZMA, đuôi
``.bi5``. Đây là nguồn de-facto cho backtest tick-accurate.

URL pattern (CỰC KỲ DỄ SAI — đọc kỹ):
    https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM-1:02d}/{DD:02d}/{HH:02d}h_ticks.bi5
                                                          ^^^^^^^^^
    ⚠️ THÁNG 0-INDEXED: tháng 1 (January) = "00", tháng 12 = "11".
       Đây là lỗi phổ biến nhất khi tải Dukascopy. Hàm bên dưới xử lý sẵn.

Định dạng sau khi giải nén LZMA = mảng record 20 byte, BIG-ENDIAN:
    struct DukascopyTick {        # big-endian, packed
        uint32 ms_offset;         # mili-giây kể từ ĐẦU GIỜ của file
        uint32 ask;               # = giá_ask_thật * 10**digits
        uint32 bid;               # = giá_bid_thật * 10**digits
        float  ask_volume;
        float  bid_volume;
    };
=> struct format ">IIIff" (3 uint32 + 2 float) = 20 byte.

Giá thật = raw / 10**digits  (lấy digits từ :mod:`tdsclone.symbols`).

Lưu ý nghiệp vụ:
  * Cuối tuần / ngày lễ thị trường đóng -> file rỗng hoặc HTTP 404: ĐÂY LÀ BÌNH THƯỜNG.
  * Có resume: file .bi5 đã có trên đĩa thì bỏ qua, không tải lại.
  * Tải song song theo giờ bằng ThreadPool (I/O bound). Có retry + backoff cho 503.
"""

from __future__ import annotations

import logging
import lzma
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from tdsclone.model import TickFrame, US_PER_MS
from tdsclone.symbols import get_symbol_spec

logger = logging.getLogger(__name__)

# Base URL của feed Dukascopy.
DUKASCOPY_BASE = "https://datafeed.dukascopy.com/datafeed"

# Mỗi record .bi5 = 20 byte, big-endian: 3 uint32 + 2 float.
_BI5_RECORD = struct.Struct(">IIIff")
assert _BI5_RECORD.size == 20

# User-Agent "thật" để tránh bị feed từ chối.
_UA = "Mozilla/5.0 (compatible; tdsclone/0.1; +https://example.invalid)"


# =============================================================================
#  Decoder thuần — tách riêng để unit-test không cần mạng.
# =============================================================================

def decode_bi5(raw_lzma: bytes, hour_start_us: int, point_factor: int) -> TickFrame:
    """
    Giải nén & decode 1 file ``.bi5`` (bytes LZMA) thành :class:`TickFrame`.

    Tham số:
        raw_lzma      : nội dung file .bi5 (đã nén LZMA).
        hour_start_us : epoch micro-giây của ĐẦU GIỜ chứa các tick (UTC).
                        ms_offset trong record cộng vào mốc này ra timestamp tuyệt đối.
        point_factor  : 10**digits của symbol -> chia giá nguyên ra giá thật.

    Trả về TickFrame đã sort tăng (Dukascopy vốn đã theo thứ tự thời gian).
    """
    tf = TickFrame()
    if not raw_lzma:
        return tf  # file rỗng (cuối tuần / không có tick) -> frame rỗng.

    # .bi5 là LZMA stream "alone"/raw tuỳ thời kỳ; lzma.decompress tự nhận diện
    # định dạng .lzma chuẩn của Dukascopy.
    try:
        data = lzma.decompress(raw_lzma)
    except lzma.LZMAError as exc:  # file hỏng / không phải LZMA
        raise ValueError(f"Không giải nén được .bi5: {exc}") from exc

    inv = 1.0 / point_factor
    n = len(data) // _BI5_RECORD.size
    for i in range(n):
        ms_off, ask_i, bid_i, ask_vol, bid_vol = _BI5_RECORD.unpack_from(
            data, i * _BI5_RECORD.size
        )
        ts_us = hour_start_us + ms_off * US_PER_MS
        tf.append(
            ts_us,
            bid=bid_i * inv,
            ask=ask_i * inv,
            bid_vol=float(bid_vol),
            ask_vol=float(ask_vol),
        )
    return tf


# =============================================================================
#  Helpers URL & path
# =============================================================================

def bi5_url(symbol: str, dt_hour: datetime) -> str:
    """
    Dựng URL .bi5 cho 1 giờ cụ thể. ``dt_hour`` phải là UTC, đã làm tròn xuống giờ.

    ⚠️ Tháng 0-indexed: dùng ``dt_hour.month - 1``.
    """
    return (
        f"{DUKASCOPY_BASE}/{symbol.upper()}/"
        f"{dt_hour.year:04d}/{dt_hour.month - 1:02d}/{dt_hour.day:02d}/"
        f"{dt_hour.hour:02d}h_ticks.bi5"
    )


def bi5_cache_path(out_dir: Path, symbol: str, dt_hour: datetime) -> Path:
    """Đường dẫn cache thô: raw/{symbol}/{year}/{month}/{day}/{hour}.bi5 (mục 1.5)."""
    return (
        out_dir
        / symbol.upper()
        / f"{dt_hour.year:04d}"
        / f"{dt_hour.month:02d}"      # cache dùng tháng THẬT (1-12) cho dễ đọc
        / f"{dt_hour.day:02d}"
        / f"{dt_hour.hour:02d}.bi5"
    )


def _iter_hours(start: datetime, end: datetime):
    """Sinh từng mốc giờ UTC trong [start, end) (đã làm tròn xuống giờ)."""
    cur = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = end.astimezone(timezone.utc)
    one_hour = timedelta(hours=1)
    while cur < end:
        yield cur
        cur += one_hour


# =============================================================================
#  Báo cáo kết quả tải
# =============================================================================

@dataclass
class DownloadReport:
    """Kết quả 1 lần download (để GUI hiển thị)."""

    symbol: str
    requested_hours: int = 0
    downloaded: int = 0          # số giờ tải mới từ mạng
    cached: int = 0              # số giờ lấy từ cache đĩa
    empty: int = 0               # số giờ rỗng (cuối tuần/holiday) — bình thường
    failed: list[datetime] = field(default_factory=list)  # giờ tải lỗi
    total_ticks: int = 0

    def summary(self) -> str:
        return (
            f"[{self.symbol}] yêu cầu {self.requested_hours} giờ | "
            f"tải mới {self.downloaded}, cache {self.cached}, rỗng {self.empty}, "
            f"lỗi {len(self.failed)} | tổng {self.total_ticks:,} tick"
        )


# =============================================================================
#  Downloader chính
# =============================================================================

class DukascopyDownloader:
    """
    Tải tick-data Dukascopy theo khoảng thời gian.

    Ví dụ::

        dl = DukascopyDownloader(cache_dir=Path("raw"))
        ticks, report = dl.download(
            "EURUSD",
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
        print(report.summary())
    """

    def __init__(
        self,
        cache_dir: Path | str = "raw",
        max_workers: int = 8,
        max_retries: int = 4,
        timeout: float = 30.0,
        backoff_base: float = 1.5,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout
        self.backoff_base = backoff_base

    # ----- tải 1 giờ (network + cache) ---------------------------------

    def _fetch_hour_bytes(self, symbol: str, dt_hour: datetime) -> tuple[bytes, bool]:
        """
        Lấy bytes .bi5 cho 1 giờ. Trả ``(data, from_cache)``.

        Resume: nếu file đã có trong cache -> đọc đĩa, không gọi mạng.
        Có retry + exponential backoff cho lỗi tạm thời (503/timeout).
        File rỗng được cache thành file 0 byte để khỏi tải lại.
        """
        cache_path = bi5_cache_path(self.cache_dir, symbol, dt_hour)
        if cache_path.exists():
            return cache_path.read_bytes(), True

        url = bi5_url(symbol, dt_hour)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                # Lưu cache (kể cả rỗng) để resume lần sau.
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
                return data, False
            except urllib.error.HTTPError as exc:
                # 404 = không có data cho giờ này (cuối tuần/holiday) -> coi như rỗng.
                if exc.code == 404:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(b"")
                    return b"", False
                last_exc = exc
                if exc.code in (500, 502, 503, 504):
                    time.sleep(self.backoff_base ** attempt)
                    continue
                raise  # lỗi HTTP khác -> ném ra ngoài
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                time.sleep(self.backoff_base ** attempt)

        raise RuntimeError(
            f"Tải thất bại sau {self.max_retries} lần: {url} ({last_exc})"
        )

    # ----- API công khai -----------------------------------------------

    def download(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[TickFrame, DownloadReport]:
        """
        Tải toàn bộ tick trong ``[start, end)`` (UTC).

        Tham số:
            symbol   : ví dụ "EURUSD".
            start/end: datetime UTC (nếu naive sẽ được coi là UTC).
            progress : callback(done, total) để GUI cập nhật thanh tiến trình.

        Trả về ``(TickFrame đã sort, DownloadReport)``.
        Tải song song theo giờ; decode sau khi tải xong từng giờ.
        """
        spec = get_symbol_spec(symbol)
        hours = list(_iter_hours(start, end))
        report = DownloadReport(symbol=symbol.upper(), requested_hours=len(hours))

        # Kết quả từng giờ -> giữ theo index để ghép đúng thứ tự thời gian.
        results: dict[int, TickFrame] = {}

        def work(idx_hour: tuple[int, datetime]) -> tuple[int, TickFrame, bool]:
            idx, dt_hour = idx_hour
            raw, from_cache = self._fetch_hour_bytes(symbol, dt_hour)
            hour_start_us = int(dt_hour.timestamp()) * 1_000_000
            tf = decode_bi5(raw, hour_start_us, spec.point_factor)
            return idx, tf, from_cache

        done = 0
        total = len(hours)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(work, (i, h)): (i, h) for i, h in enumerate(hours)
            }
            for fut in as_completed(futures):
                i, dt_hour = futures[fut]
                try:
                    idx, tf, from_cache = fut.result()
                except Exception as exc:  # noqa: BLE001 — gom lỗi để báo cáo, không dừng
                    logger.warning("Lỗi tải %s %s: %s", symbol, dt_hour, exc)
                    report.failed.append(dt_hour)
                else:
                    results[idx] = tf
                    if from_cache:
                        report.cached += 1
                    else:
                        report.downloaded += 1
                    if tf.is_empty():
                        report.empty += 1
                done += 1
                if progress:
                    progress(done, total)

        # Ghép theo thứ tự giờ tăng dần.
        merged = TickFrame()
        for idx in range(len(hours)):
            tf = results.get(idx)
            if tf is not None:
                merged.extend(tf)
        merged.sort()  # an toàn: đảm bảo monotonic kể cả khi data lệch
        report.total_ticks = len(merged)
        logger.info(report.summary())
        return merged, report
