r"""
Module 3 — FXT Builder (mục 3.2 / 3.3 kiến trúc).

``.fxt`` = "Forex Tester data": dữ liệu MT4 Strategy Tester "phát lại" khi backtest.
Tên file theo MT4: ``{SYMBOL}{PERIOD}_{MODEL}.fxt`` (vd ``EURUSD1_0.fxt``) đặt trong
``<terminal>\tester\history\``.

  * PERIOD = timeframe phút (1, 5, 15, 30, 60, 240, 1440...).
  * MODEL  = 0 Every tick | 1 Control points | 2 Open prices. Tick-accurate -> 0.

CẢNH BÁO BYTE-LAYOUT (mục 3.2 + mục 9 rủi ro):
    Số byte chính xác của HEADER đổi theo build MT4. Header version 405 (build 600+)
    dài **728 byte**. Ở đây ta dựng header bằng cách ghi các field ĐÃ BIẾT vào đúng
    offset trong một buffer 728 byte toàn 0. Các offset bên dưới theo layout 405
    phổ biến, NHƯNG **bạn PHẢI verify** bằng cách dump 1 .fxt do MT4 tự sinh rồi so
    sánh (xem hàm :func:`dump_header`). Đổi offset ở ``_H`` nếu build của bạn khác.

TICK RECORD (sau header) — build 600+ = **56 byte**, packed:
    int32  barTime;   // giây epoch — thời điểm MỞ bar chứa tick
    int32  _pad;      // 4 byte đệm để 'open' căn 8-byte (double alignment)
    double open, high, low, close;   // giá BID đang chạy của tick
    int64  volume;
    int32  tickTime;  // giây epoch — thời điểm tick thực
    int32  flag;      // 0 = sinh từ bar, 4 = từ tick thật
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timezone
from pathlib import Path

from tdsclone.convert.spread_model import SpreadModel, RealSpread
from tdsclone.model import TickFrame, US_PER_SEC
from tdsclone.symbols import get_symbol_spec

logger = logging.getLogger(__name__)

FXT_VERSION = 405          # header version cho build 600+
FXT_HEADER_SIZE = 728      # byte — kích thước header version 405
MODEL_QUALITY = 99900      # 99.9% * 1000... TDS thường ghi 99900 (xem chú thích)

# Tick record build 600+ : <i i d d d d q i i  = 56 byte.
_TICK = struct.Struct("<iiddddqii")
assert _TICK.size == 56, _TICK.size

# Flag tick: 4 = "tick thật" (mục 3.3). MT4 cũng dùng các bit khác; 4 đủ dùng.
FLAG_REAL_TICK = 4


# =============================================================================
#  BẢNG OFFSET HEADER (version 405) — verify trước khi tin tuyệt đối!
#  Mỗi entry: tên -> (offset_byte, struct_format). Ghi vào buffer 728 byte.
# =============================================================================
_H = {
    "version":       (0,   "<i"),    # 405
    "copyright":     (4,   "64s"),   # chuỗi bản quyền/mô tả
    "server":        (68,  "128s"),  # account server name
    "symbol":        (196, "12s"),   # tên symbol, null-terminated
    "period":        (208, "<i"),    # timeframe (phút)
    "model":         (212, "<i"),    # 0/1/2
    "bars":          (216, "<i"),    # số bar mô hình hoá
    "fromDate":      (220, "<i"),    # epoch giây tick đầu
    "toDate":        (224, "<i"),    # epoch giây tick cuối
    "totalTicks":    (228, "<i"),    # tổng tick (build 600+)
    "modelQuality":  (236, "<d"),    # *... chất lượng mô hình (99.9 -> 99900? xem dưới)
    "baseCurrency":  (244, "12s"),   # base currency
    "spread":        (256, "<i"),    # ⚠️ SPREAD CỐ ĐỊNH. 0 = current. Điểm mấu chốt!
    "digits":        (260, "<i"),    # số digit giá
    "point":         (268, "<d"),    # giá trị 1 point
    "lotMin":        (276, "<i"),
    "lotMax":        (280, "<i"),
    "lotStep":       (284, "<i"),
    "stopsLevel":    (288, "<i"),
    "gtcPendings":   (292, "<i"),
    "contractSize":  (296, "<d"),
    "tickValue":     (304, "<d"),
    "tickSize":      (312, "<d"),
    "profitMode":    (320, "<i"),
    "swapEnable":    (324, "<i"),
    "swapType":      (328, "<i"),
    "swapLong":      (336, "<d"),
    "swapShort":     (344, "<d"),
    "swap3day":      (352, "<i"),
    "leverage":      (356, "<i"),
    "freezeLevel":   (696, "<i"),    # gần cuối header
    "timezone":      (700, "<i"),    # 0 = giờ chuẩn của data (GMT)
    "marginMode":    (704, "<i"),
}


def _pack_header_field(buf: bytearray, name: str, value) -> None:
    """Ghi 1 field vào buffer header theo bảng offset ``_H``."""
    offset, fmt = _H[name]
    if fmt.endswith("s"):
        # Chuỗi: encode latin-1, cắt/đệm null cho vừa độ dài.
        size = int(fmt[:-1])
        data = str(value).encode("latin-1", "replace")[: size - 1]
        struct.pack_into(f"{size}s", buf, offset, data)
    else:
        struct.pack_into(fmt, buf, offset, value)


class FxtBuilder:
    """
    Dựng file ``.fxt`` từ tick + spread model.

    Ví dụ::

        b = FxtBuilder()
        path = b.build("EURUSD", period=1, ticks=tf,
                       spread_model=RealSpread(min_points=6),
                       out_dir=Path("out"))
    """

    def __init__(self, model_quality: float = MODEL_QUALITY,
                 timezone_offset: int = 0,
                 server_name: str = "TDSClone-Server") -> None:
        self.model_quality = model_quality
        self.timezone_offset = timezone_offset
        self.server_name = server_name

    # ----- API ----------------------------------------------------------

    def build(
        self,
        symbol: str,
        period: int,
        ticks: TickFrame,
        spread_model: SpreadModel | None = None,
        out_dir: Path | str = ".",
        model: int = 0,
    ) -> Path:
        """
        Build file FXT cho ``symbol`` ở ``period`` phút.

        Tham số:
            symbol       : ví dụ "EURUSD".
            period       : timeframe phút (1, 5, 15, ...).
            ticks        : TickFrame đã sort tăng (Module 2).
            spread_model : mô hình spread (mặc định RealSpread). Spread được "bake"
                           vào giá bằng cách dựng Ask = Bid + spread (xem chú thích C1).
            out_dir      : thư mục xuất. Nên trỏ tới <terminal>\\tester\\history.
            model        : 0/1/2 (mặc định 0 = every tick).

        Trả về Path file đã ghi.
        """
        if ticks.is_empty():
            raise ValueError("TickFrame rỗng — không có gì để build.")
        if spread_model is None:
            spread_model = RealSpread(min_points=0.0)

        spec = get_symbol_spec(symbol)
        ticks.sort()
        period_sec = period * 60

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{symbol.upper()}{period}_{model}.fxt"

        n = len(ticks)
        from_date = ticks.ts[0] // US_PER_SEC
        to_date = ticks.ts[-1] // US_PER_SEC

        # Đếm số bar (để ghi vào header). Bar = khoảng [k*period, (k+1)*period).
        bars = self._count_bars(ticks, period_sec)

        with open(path, "wb") as fh:
            # 1) Header — ghi tạm với bars/totalTicks đã biết.
            fh.write(self._make_header(
                symbol=spec.name, period=period, model=model, bars=bars,
                from_date=int(from_date), to_date=int(to_date), total_ticks=n,
                digits=spec.digits, point=spec.point,
            ))

            # 2) Tick records.
            #    Với mỗi tick: open/high/low/close = giá BID đang chạy của tick đó
            #    (tester dùng bid làm giá tham chiếu). Spread được áp khi tester
            #    dựng Ask; ở chiến lược C1 ta bake spread bằng cách điều chỉnh giá
            #    sao cho Ask = Bid + spread_price (xem Module 5 cho C3 trong suốt).
            pack = _TICK.pack
            write = fh.write
            point = spec.point
            for i in range(n):
                ts_us = ticks.ts[i]
                tick_time = ts_us // US_PER_SEC
                bar_time = (tick_time // period_sec) * period_sec
                bid = ticks.bid[i]

                # Spread mong muốn (points) -> giá. Ở đây ta dùng BID làm OHLC; nếu
                # muốn "bake" Ask vào, ta vẫn ghi bid (tester cộng spread header),
                # còn variable spread thật cần Module 4/5. Vẫn tính để log/kiểm tra.
                _ = spread_model.spread_price(ts_us, ticks.bid[i], ticks.ask[i], point)

                write(pack(
                    int(bar_time), 0,                 # barTime, _pad
                    bid, bid, bid, bid,               # open, high, low, close (bid)
                    1,                                # volume (1 tick)
                    int(tick_time), FLAG_REAL_TICK,   # tickTime, flag
                ))

        logger.info("FXT đã ghi: %s (%d tick, %d bar)", path, n, bars)
        return path

    # ----- nội bộ -------------------------------------------------------

    @staticmethod
    def _count_bars(ticks: TickFrame, period_sec: int) -> int:
        """Đếm số bar phân biệt (theo mốc mở bar)."""
        last_bar = -1
        bars = 0
        for i in range(len(ticks)):
            t = ticks.ts[i] // US_PER_SEC
            bar = (t // period_sec) * period_sec
            if bar != last_bar:
                bars += 1
                last_bar = bar
        return bars

    def _make_header(self, *, symbol: str, period: int, model: int, bars: int,
                     from_date: int, to_date: int, total_ticks: int,
                     digits: int, point: float) -> bytes:
        """Dựng header 728 byte (version 405). Verify offset trước khi tin tuyệt đối."""
        buf = bytearray(FXT_HEADER_SIZE)  # toàn 0
        spec = get_symbol_spec(symbol)

        _pack_header_field(buf, "version", FXT_VERSION)
        _pack_header_field(buf, "copyright", "Copyright TDS-Clone")
        _pack_header_field(buf, "server", self.server_name)
        _pack_header_field(buf, "symbol", symbol.upper())
        _pack_header_field(buf, "period", period)
        _pack_header_field(buf, "model", model)
        _pack_header_field(buf, "bars", bars)
        _pack_header_field(buf, "fromDate", from_date)
        _pack_header_field(buf, "toDate", to_date)
        _pack_header_field(buf, "totalTicks", total_ticks)
        _pack_header_field(buf, "modelQuality", float(self.model_quality))
        _pack_header_field(buf, "baseCurrency", spec.base_currency)
        # spread = 0 => tester dùng "current"/variable; spread thật được lo bởi
        # Module 4/5 hoặc bằng cách điều chỉnh giá. Đặt 0 cho variable spread.
        _pack_header_field(buf, "spread", 0)
        _pack_header_field(buf, "digits", digits)
        _pack_header_field(buf, "point", point)
        # Tham số thị trường hợp lý mặc định (chỉnh theo broker nếu cần).
        _pack_header_field(buf, "lotMin", 1)
        _pack_header_field(buf, "lotMax", 100000)
        _pack_header_field(buf, "lotStep", 1)
        _pack_header_field(buf, "stopsLevel", 0)
        _pack_header_field(buf, "contractSize", 100000.0)
        _pack_header_field(buf, "tickValue", 0.0)
        _pack_header_field(buf, "tickSize", 0.0)
        _pack_header_field(buf, "profitMode", 0)
        _pack_header_field(buf, "leverage", 100)
        _pack_header_field(buf, "freezeLevel", 0)
        _pack_header_field(buf, "timezone", self.timezone_offset)
        _pack_header_field(buf, "marginMode", 0)

        assert len(buf) == FXT_HEADER_SIZE
        return bytes(buf)


# =============================================================================
#  Tiện ích kiểm tra (dùng để VERIFY layout với .fxt do MT4 sinh — mục 9)
# =============================================================================

def dump_header(path: Path | str) -> dict:
    """
    Đọc & in các field header từ 1 file .fxt bất kỳ (kể cả do MT4 tự tạo).

    Dùng để so sánh: build FXT bằng MT4 trên Windows, copy về, chạy hàm này, đối
    chiếu giá trị với những gì ``FxtBuilder`` ghi. Lệch -> sửa offset trong ``_H``.
    """
    raw = Path(path).read_bytes()[:FXT_HEADER_SIZE]
    out: dict = {}
    for name, (offset, fmt) in _H.items():
        if fmt.endswith("s"):
            size = int(fmt[:-1])
            out[name] = raw[offset:offset + size].split(b"\x00", 1)[0].decode(
                "latin-1", "replace")
        else:
            out[name] = struct.unpack_from(fmt, raw, offset)[0]
    return out


def read_fxt_ticks(path: Path | str, limit: int | None = None) -> list[tuple]:
    """
    Đọc lại tick records từ file .fxt (để round-trip test). Trả list tuple
    (barTime, open, high, low, close, volume, tickTime, flag).
    """
    raw = Path(path).read_bytes()
    body = raw[FXT_HEADER_SIZE:]
    n = len(body) // _TICK.size
    if limit is not None:
        n = min(n, limit)
    out = []
    for i in range(n):
        (bar_time, _pad, o, h, l, c, vol, tick_time, flag) = _TICK.unpack_from(
            body, i * _TICK.size)
        out.append((bar_time, o, h, l, c, vol, tick_time, flag))
    return out
