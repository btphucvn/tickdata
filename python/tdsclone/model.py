"""
Mô hình dữ liệu tick chuẩn hoá (canonical tick) — Module 2, mục 2.2.

Đây là "ngôn ngữ chung" mà mọi module trao đổi với nhau. Để chạy được mà không
cần polars/pandas/pyarrow, ta hiện thực :class:`TickFrame` bằng ``array`` của
standard library (columnar, gọn bộ nhớ, đủ nhanh cho hàng triệu tick).

Schema (mục 2.2 kiến trúc):
    timestamp_utc : int64   epoch MICRO-giây (us). Dùng micro để không mất phân giải.
    bid           : float64
    ask           : float64
    bid_volume    : float64
    ask_volume    : float64

``spread = ask - bid`` được tính khi cần, không lưu thừa.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator


# Số micro-giây trong 1 giây / 1 mili-giây — hằng số dùng nhiều nơi.
US_PER_SEC = 1_000_000
US_PER_MS = 1_000


@dataclass(slots=True)
class Tick:
    """Một tick đơn lẻ (dùng khi lặp/đọc; lưu trữ thực tế là columnar)."""

    timestamp_us: int   # epoch micro-giây UTC
    bid: float
    ask: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0

    @property
    def spread(self) -> float:
        """Spread tuyệt đối (đơn vị giá), = ask - bid."""
        return self.ask - self.bid

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_us / US_PER_SEC, tz=timezone.utc)


class TickFrame:
    """
    Bộ chứa columnar cho nhiều tick — bất biến về schema, có thể append.

    Cột lưu bằng ``array`` (typed): ``q`` = signed long long (int64),
    ``d`` = double (float64). Truy cập theo cột rất nhanh, phù hợp build FXT/HST.
    """

    __slots__ = ("ts", "bid", "ask", "bid_vol", "ask_vol")

    def __init__(self) -> None:
        self.ts: array = array("q")       # int64 timestamp micro-giây
        self.bid: array = array("d")
        self.ask: array = array("d")
        self.bid_vol: array = array("d")
        self.ask_vol: array = array("d")

    # ----- xây dựng -----------------------------------------------------

    def append(self, ts_us: int, bid: float, ask: float,
               bid_vol: float = 0.0, ask_vol: float = 0.0) -> None:
        """Thêm 1 tick vào cuối frame."""
        self.ts.append(ts_us)
        self.bid.append(bid)
        self.ask.append(ask)
        self.bid_vol.append(bid_vol)
        self.ask_vol.append(ask_vol)

    def extend(self, other: "TickFrame") -> None:
        """Nối toàn bộ một frame khác vào cuối frame này."""
        self.ts.extend(other.ts)
        self.bid.extend(other.bid)
        self.ask.extend(other.ask)
        self.bid_vol.extend(other.bid_vol)
        self.ask_vol.extend(other.ask_vol)

    @classmethod
    def from_ticks(cls, ticks: Iterable[Tick]) -> "TickFrame":
        tf = cls()
        for t in ticks:
            tf.append(t.timestamp_us, t.bid, t.ask, t.bid_volume, t.ask_volume)
        return tf

    # ----- truy vấn -----------------------------------------------------

    def __len__(self) -> int:
        return len(self.ts)

    def __iter__(self) -> Iterator[Tick]:
        for i in range(len(self.ts)):
            yield Tick(self.ts[i], self.bid[i], self.ask[i],
                       self.bid_vol[i], self.ask_vol[i])

    def is_empty(self) -> bool:
        return len(self.ts) == 0

    def time_range(self) -> tuple[int, int] | None:
        """(min_ts, max_ts) micro-giây, hoặc None nếu rỗng. Giả định đã sort tăng."""
        if not self.ts:
            return None
        return self.ts[0], self.ts[-1]

    def sort(self) -> None:
        """
        Sắp xếp ổn định theo timestamp tăng dần (data nhiều nguồn có thể lệch thứ tự).

        Vì array không sort kèm được nhiều cột, ta tạo chỉ mục rồi rebuild — chấp
        nhận tốn bộ nhớ tạm. Với data rất lớn nên sort ở từng file rồi merge.
        """
        n = len(self.ts)
        if n < 2:
            return
        order = sorted(range(n), key=self.ts.__getitem__)
        # Nếu đã đúng thứ tự thì khỏi rebuild (trường hợp phổ biến).
        if all(order[i] == i for i in range(n)):
            return
        self.ts = array("q", (self.ts[i] for i in order))
        self.bid = array("d", (self.bid[i] for i in order))
        self.ask = array("d", (self.ask[i] for i in order))
        self.bid_vol = array("d", (self.bid_vol[i] for i in order))
        self.ask_vol = array("d", (self.ask_vol[i] for i in order))

    def slice_time(self, start_us: int, end_us: int) -> "TickFrame":
        """Trả frame con [start_us, end_us) — yêu cầu đã sort tăng dần."""
        import bisect

        lo = bisect.bisect_left(self.ts, start_us)
        hi = bisect.bisect_left(self.ts, end_us)
        out = TickFrame()
        out.ts = self.ts[lo:hi]
        out.bid = self.bid[lo:hi]
        out.ask = self.ask[lo:hi]
        out.bid_vol = self.bid_vol[lo:hi]
        out.ask_vol = self.ask_vol[lo:hi]
        return out

    # ----- tiện ích cho interop polars/pyarrow (tuỳ chọn) ----------------

    def to_columns(self) -> dict[str, list]:
        """Xuất ra dict cột thường (để đẩy vào polars/pyarrow nếu có)."""
        return {
            "timestamp_utc": list(self.ts),
            "bid": list(self.bid),
            "ask": list(self.ask),
            "bid_volume": list(self.bid_vol),
            "ask_volume": list(self.ask_vol),
        }
