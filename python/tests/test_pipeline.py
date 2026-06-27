"""
Test end-to-end cho phần lõi (Module 1 decode, 2 store, 3 FXT/HST/spread).

Thiết kế để chạy KHÔNG cần mạng và KHÔNG cần cài gói nào:
    * pytest:   python -m pytest python/tests
    * hoặc:     python python/tests/test_pipeline.py   (tự chạy, in PASS/FAIL)

Mọi test dùng data tổng hợp -> không phụ thuộc Dukascopy thật.
"""

from __future__ import annotations

import lzma
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Cho phép chạy trực tiếp: thêm thư mục 'python' vào sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tdsclone.convert.fxt import (
    FXT_HEADER_SIZE, FxtBuilder, dump_header, read_fxt_ticks,
)
from tdsclone.convert.hst import HstBuilder, dump_hst_header, ticks_to_bars
from tdsclone.convert.spread_file import export_spread_file, read_spread_file
from tdsclone.convert.spread_model import FixedSpread, RealSpread, SessionSpread
from tdsclone.download.dukascopy import _BI5_RECORD, decode_bi5
from tdsclone.model import TickFrame, US_PER_SEC
from tdsclone.store.tickstore import TickStore


# =============================================================================
#  Helpers
# =============================================================================

def make_synthetic_ticks(start: datetime, n: int, step_sec: int = 1) -> TickFrame:
    """Sinh n tick giả: giá đi bộ nhẹ, spread 10..14 points."""
    tf = TickFrame()
    base_us = int(start.timestamp()) * US_PER_SEC
    price = 1.10000
    for i in range(n):
        ts = base_us + i * step_sec * US_PER_SEC
        bid = round(price + 0.00001 * (i % 7), 5)
        ask = round(bid + 0.00001 * (10 + i % 5), 5)  # spread 10..14 pts (5-digit)
        tf.append(ts, bid, ask, bid_vol=1.0, ask_vol=1.0)
    return tf


def encode_bi5(records: list[tuple[int, int, int, float, float]]) -> bytes:
    """Đóng gói + nén LZMA giống file Dukascopy thật (để test decoder)."""
    raw = b"".join(_BI5_RECORD.pack(*r) for r in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


# =============================================================================
#  Tests
# =============================================================================

def test_decode_bi5_roundtrip():
    """Decoder Dukascopy decode đúng giá & timestamp."""
    point_factor = 100000  # EURUSD 5-digit
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    hour_us = int(hour.timestamp()) * US_PER_SEC
    # 2 record: ms_offset, ask_int, bid_int, ask_vol, bid_vol (big-endian).
    records = [
        (0,    110410, 110400, 1.5, 2.0),   # t=hour+0ms,  bid 1.10400 ask 1.10410
        (1500, 110420, 110405, 1.0, 1.0),   # t=hour+1500ms
    ]
    tf = decode_bi5(encode_bi5(records), hour_us, point_factor)
    assert len(tf) == 2
    assert abs(tf.bid[0] - 1.10400) < 1e-9
    assert abs(tf.ask[0] - 1.10410) < 1e-9
    # ms_offset 1500 -> +1.5s
    assert tf.ts[1] - tf.ts[0] == 1500 * 1000
    print("PASS test_decode_bi5_roundtrip")


def test_decode_bi5_empty():
    """File rỗng (cuối tuần/holiday) -> frame rỗng, không lỗi."""
    tf = decode_bi5(b"", 0, 100000)
    assert tf.is_empty()
    print("PASS test_decode_bi5_empty")


def test_tickstore_ingest_query_coverage():
    """Store nạp -> query đúng khoảng & coverage hợp lý, idempotent."""
    with tempfile.TemporaryDirectory() as d:
        store = TickStore(d)
        start = datetime(2024, 1, 2, 0, tzinfo=timezone.utc)
        ticks = make_synthetic_ticks(start, 3600)  # 1 giờ tick mỗi giây
        store.ingest("EURUSD", ticks)
        store.ingest("EURUSD", ticks)  # nạp lại -> không nhân đôi (dedup)

        q = store.query("EURUSD",
                        datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
                        datetime(2024, 1, 2, 0, 10, tzinfo=timezone.utc))
        assert len(q) == 600, len(q)  # 10 phút * 60 giây

        cov = store.coverage("EURUSD")
        assert len(cov) == 1
        assert cov[0].ticks == 3600
        store.close()
    print("PASS test_tickstore_ingest_query_coverage")


def test_fxt_build_and_header():
    """FXT build -> header 728 byte, record 56 byte, đọc lại đúng."""
    with tempfile.TemporaryDirectory() as d:
        start = datetime(2024, 1, 2, 0, tzinfo=timezone.utc)
        ticks = make_synthetic_ticks(start, 120)  # 2 phút
        path = FxtBuilder().build("EURUSD", period=1, ticks=ticks,
                                  spread_model=RealSpread(min_points=6),
                                  out_dir=d)
        raw = Path(path).read_bytes()
        # header + n*56
        assert (len(raw) - FXT_HEADER_SIZE) % 56 == 0
        assert (len(raw) - FXT_HEADER_SIZE) // 56 == 120

        hdr = dump_header(path)
        assert hdr["version"] == 405
        assert hdr["symbol"] == "EURUSD"
        assert hdr["period"] == 1
        assert hdr["digits"] == 5
        assert hdr["totalTicks"] == 120

        recs = read_fxt_ticks(path, limit=2)
        bar_time, o, h, l, c, vol, tick_time, flag = recs[0]
        assert flag == 4
        assert o == h == l == c          # open=high=low=close=bid theo thiết kế
        assert bar_time <= tick_time
        store_close = None
    print("PASS test_fxt_build_and_header")


def test_hst_build():
    """HST build -> header 148 byte, record 60 byte; bar count khớp."""
    with tempfile.TemporaryDirectory() as d:
        start = datetime(2024, 1, 2, 0, tzinfo=timezone.utc)
        ticks = make_synthetic_ticks(start, 3600)  # 1 giờ
        bars = ticks_to_bars(ticks, 60, SessionSpread(), point=0.00001)  # M1
        assert len(bars) == 60  # 60 phút -> 60 bar M1
        path = HstBuilder().build("EURUSD", 1, bars, out_dir=d)
        raw = Path(path).read_bytes()
        assert (len(raw) - 148) % 60 == 0
        assert (len(raw) - 148) // 60 == 60
        hdr = dump_hst_header(path)
        assert hdr["version"] == 401
        assert hdr["symbol"] == "EURUSD"
    print("PASS test_hst_build")


def test_spread_file_roundtrip():
    """File .tdspread sinh ra đọc lại đúng, sort tăng, dedup theo giây."""
    with tempfile.TemporaryDirectory() as d:
        start = datetime(2024, 1, 2, 0, tzinfo=timezone.utc)
        ticks = make_synthetic_ticks(start, 50, step_sec=1)
        p = export_spread_file(Path(d) / "x.tdspread", "EURUSD", ticks,
                               FixedSpread(points=12.5))
        recs = read_spread_file(p)
        assert len(recs) == 50
        assert all(abs(sp - 12.5) < 1e-4 for _, sp in recs)
        assert all(recs[i][0] <= recs[i + 1][0] for i in range(len(recs) - 1))
    print("PASS test_spread_file_roundtrip")


def test_spread_models():
    """Các spread model trả giá trị hợp lý."""
    ts = int(datetime(2024, 1, 2, 3, tzinfo=timezone.utc).timestamp()) * US_PER_SEC
    assert FixedSpread(points=15).spread_points(ts, 1.1, 1.10015, 1e-5) == 15
    # RealSpread: (ask-bid)/point = 15 pts
    assert abs(RealSpread().spread_points(ts, 1.10000, 1.10015, 1e-5) - 15) < 1e-6
    # SessionSpread phiên Á (giờ 3 UTC) -> dùng 'asia'
    assert SessionSpread().spread_points(ts, 1.1, 1.1, 1e-5) == SessionSpread().asia
    print("PASS test_spread_models")


# =============================================================================
#  Runner độc lập (không cần pytest)
# =============================================================================

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} test PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
