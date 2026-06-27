r"""
Module 3 — HST Builder (mục 3.1 / 3.5 kiến trúc).

``.hst`` = history: dữ liệu giá theo timeframe, MT4 dùng để vẽ chart và cho EA
gọi ``iOpen/iHigh/iLow/iClose/iTime``. Đặt ở ``<terminal>\history\<server>\``.
Tên file: ``{SYMBOL}{PERIOD}.hst`` (vd ``EURUSD60.hst`` cho H1).

Ta ghi định dạng **version 401** (build 600+):

HEADER (148 byte):
    int  version;        // 401
    char copyright[64];
    char symbol[12];
    int  period;         // phút
    int  digits;
    int  timesign;       // epoch giây lúc tạo file
    int  last_sync;      // 0
    char reserved[52];   // đệm 13 * int

BAR RECORD (60 byte, version 401):
    int64  ctm;          // epoch giây mở bar (8 byte ở v401)
    double open, high, low, close;
    int64  tick_volume;
    int32  spread;       // points
    int64  real_volume;
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tdsclone.convert.spread_model import SpreadModel
from tdsclone.model import TickFrame, US_PER_SEC
from tdsclone.symbols import get_symbol_spec

logger = logging.getLogger(__name__)

HST_VERSION = 401
HST_HEADER_SIZE = 148
# Bar record v401: <q d d d d q i q  = 8+8*4+8+4+8 = 60 byte.
_BAR = struct.Struct("<qddddqiq")
assert _BAR.size == 60, _BAR.size


@dataclass
class Bar:
    """Một nến OHLCV cho .hst."""

    time_sec: int          # epoch giây mở bar (UTC)
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: int = 0
    real_volume: int = 0


def ticks_to_bars(ticks: TickFrame, period_sec: int,
                  spread_model: SpreadModel | None = None,
                  point: float = 0.00001) -> list[Bar]:
    """
    Gộp tick thành bar OHLC theo BID (mục 3.3: "mở/cao/thấp/đóng theo BID").

    ``spread_points`` mỗi bar = trung bình spread (points) trong bar (từ spread_model
    nếu có, hoặc từ chính data). ``tick_volume`` = số tick trong bar.
    """
    bars: list[Bar] = []
    cur_bar_time = -1
    o = h = l = c = 0.0
    vol = 0
    spread_acc = 0.0

    def flush() -> None:
        nonlocal o, h, l, c, vol, spread_acc
        if vol > 0:
            sp = int(round(spread_acc / vol)) if vol else 0
            bars.append(Bar(cur_bar_time, o, h, l, c, vol, sp))

    for i in range(len(ticks)):
        t = ticks.ts[i] // US_PER_SEC
        bar_time = (t // period_sec) * period_sec
        bid = ticks.bid[i]
        if spread_model is not None:
            sp_pts = spread_model.spread_points(
                ticks.ts[i], ticks.bid[i], ticks.ask[i], point)
        else:
            sp_pts = (ticks.ask[i] - ticks.bid[i]) / point

        if bar_time != cur_bar_time:
            flush()
            cur_bar_time = bar_time
            o = h = l = c = bid
            vol = 0
            spread_acc = 0.0
        h = max(h, bid)
        l = min(l, bid)
        c = bid
        vol += 1
        spread_acc += sp_pts
    flush()
    return bars


class HstBuilder:
    """
    Dựng file ``.hst`` từ danh sách :class:`Bar` (hoặc trực tiếp từ tick).

    Ví dụ::

        bars = ticks_to_bars(tf, 60*60, RealSpread(), spec.point)
        HstBuilder().build("EURUSD", 60, bars, Path("history/TDSClone-Server"))
    """

    def build(self, symbol: str, period: int, bars: list[Bar],
              out_dir: Path | str = ".", copyright_str: str = "TDS-Clone") -> Path:
        spec = get_symbol_spec(symbol)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{symbol.upper()}{period}.hst"

        with open(path, "wb") as fh:
            fh.write(self._make_header(spec.name, period, spec.digits, copyright_str))
            pack = _BAR.pack
            for b in bars:
                fh.write(pack(
                    int(b.time_sec), b.open, b.high, b.low, b.close,
                    int(b.tick_volume), int(b.spread_points), int(b.real_volume),
                ))
        logger.info("HST đã ghi: %s (%d bar)", path, len(bars))
        return path

    def build_from_ticks(self, symbol: str, period: int, ticks: TickFrame,
                         spread_model: SpreadModel | None = None,
                         out_dir: Path | str = ".") -> Path:
        """Tiện ích: gộp tick -> bar -> ghi .hst trong một bước."""
        spec = get_symbol_spec(symbol)
        bars = ticks_to_bars(ticks, period * 60, spread_model, spec.point)
        return self.build(symbol, period, bars, out_dir)

    # ----- nội bộ -------------------------------------------------------

    @staticmethod
    def _make_header(symbol: str, period: int, digits: int, copyright_str: str) -> bytes:
        buf = bytearray(HST_HEADER_SIZE)
        struct.pack_into("<i", buf, 0, HST_VERSION)
        struct.pack_into("64s", buf, 4, copyright_str.encode("latin-1", "replace")[:63])
        struct.pack_into("12s", buf, 68, symbol.upper().encode("latin-1")[:11])
        struct.pack_into("<i", buf, 80, period)
        struct.pack_into("<i", buf, 84, digits)
        struct.pack_into("<i", buf, 88, int(time.time()))  # timesign
        struct.pack_into("<i", buf, 92, 0)                 # last_sync
        # 96..148 = reserved (đã là 0).
        assert len(buf) == HST_HEADER_SIZE
        return bytes(buf)


def dump_hst_header(path: Path | str) -> dict:
    """Đọc header .hst để verify (so với file MT4 sinh)."""
    raw = Path(path).read_bytes()[:HST_HEADER_SIZE]
    return {
        "version": struct.unpack_from("<i", raw, 0)[0],
        "copyright": raw[4:68].split(b"\x00", 1)[0].decode("latin-1", "replace"),
        "symbol": raw[68:80].split(b"\x00", 1)[0].decode("latin-1", "replace"),
        "period": struct.unpack_from("<i", raw, 80)[0],
        "digits": struct.unpack_from("<i", raw, 84)[0],
        "timesign": struct.unpack_from("<i", raw, 88)[0],
    }
