"""
Module 3 — Spread Model (mục 3.4 kiến trúc).

Đây là điểm "ăn tiền": MT4 tester mặc định chỉ dùng MỘT spread cố định. Để có
spread biến động giống thị trường thật, ta định nghĩa các mô hình spread dùng
chung cho cả 3 chiến lược C1/C2/C3.

Mọi mô hình kế thừa :class:`SpreadModel` và cài đặt::

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float

trả về spread tính bằng **POINTS** (số nguyên/thực điểm giá), ví dụ EURUSD spread
1.2 pip = 12 points (5-digit). Builder sẽ nhân với ``point`` để ra spread tuyệt đối.

Các mô hình có sẵn:
  * RealSpread     : dùng đúng spread thật từ data (ask - bid). Chính xác nhất.
  * FixedSpread    : spread cố định N points (giống tester thường).
  * RandomSpread   : spread ngẫu nhiên quanh 1 trung bình (mô phỏng nhiễu).
  * SessionSpread  : spread theo phiên Á/Âu/Mỹ (giãn lúc thanh khoản thấp).
  * NewsWidenSpread: bọc 1 model khác, giãn spread quanh giờ tin / rollover.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tdsclone.model import US_PER_SEC


class SpreadModel(ABC):
    """Giao diện trừu tượng: trả spread (points) cho 1 tick."""

    @abstractmethod
    def spread_points(self, ts_us: int, real_bid: float, real_ask: float,
                      point: float) -> float:
        """Spread mong muốn tại tick này, tính bằng points (>= 0)."""
        raise NotImplementedError

    # Tiện ích: ra spread tuyệt đối (đơn vị giá).
    def spread_price(self, ts_us: int, real_bid: float, real_ask: float,
                     point: float) -> float:
        return max(0.0, self.spread_points(ts_us, real_bid, real_ask, point)) * point

    @staticmethod
    def _utc(ts_us: int) -> datetime:
        return datetime.fromtimestamp(ts_us / US_PER_SEC, tz=timezone.utc)


# =============================================================================
#  Các mô hình cụ thể
# =============================================================================

@dataclass
class RealSpread(SpreadModel):
    """
    Dùng spread THẬT từ data: (ask - bid) / point.

    ``min_points``: chặn dưới (một số tick spread = 0 do data lỗi -> ép tối thiểu).
    ``multiplier`` : phóng đại spread thật (vd 1.5 để mô phỏng broker spread rộng hơn).
    """

    min_points: float = 0.0
    multiplier: float = 1.0

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float:
        raw = (real_ask - real_bid) / point
        return max(self.min_points, raw * self.multiplier)


@dataclass
class FixedSpread(SpreadModel):
    """Spread cố định N points — tái lập đúng hành vi tester mặc định."""

    points: float = 10.0

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float:
        return self.points


@dataclass
class RandomSpread(SpreadModel):
    """
    Spread ngẫu nhiên đều trong [min_points, max_points].

    ``seed`` để tái lập được (cùng seed -> cùng chuỗi spread).
    """

    min_points: float = 8.0
    max_points: float = 20.0
    seed: int | None = 42
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float:
        return self._rng.uniform(self.min_points, self.max_points)


@dataclass
class SessionSpread(SpreadModel):
    """
    Spread theo phiên giao dịch (giờ UTC):
        * Á (00:00-07:00)  : rộng nhất (thanh khoản thấp).
        * Âu (07:00-12:00) : trung bình.
        * Trùng Âu-Mỹ (12:00-16:00): hẹp nhất.
        * Mỹ (16:00-21:00) : trung bình.
        * Ngoài giờ (21:00-24:00): rộng.
    """

    asia: float = 18.0
    europe: float = 12.0
    overlap: float = 8.0
    us: float = 12.0
    offhours: float = 20.0

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float:
        h = self._utc(ts_us).hour
        if 0 <= h < 7:
            return self.asia
        if 7 <= h < 12:
            return self.europe
        if 12 <= h < 16:
            return self.overlap
        if 16 <= h < 21:
            return self.us
        return self.offhours


@dataclass
class NewsWidenSpread(SpreadModel):
    """
    Bọc 1 model nền và GIÃN spread quanh các thời điểm nhạy cảm:
      * Rollover 22:00-23:00 UTC (giờ chuyển ngày của nhiều broker) -> nhân hệ số.
      * (Tuỳ chọn) danh sách mốc tin tức -> giãn trong ±window giây.

    ``news_times_us``: list epoch micro-giây các mốc tin. ``window_sec``: bán kính.
    """

    base: SpreadModel = field(default_factory=lambda: RealSpread(min_points=6))
    rollover_multiplier: float = 4.0
    news_multiplier: float = 6.0
    news_times_us: list[int] = field(default_factory=list)
    window_sec: int = 120

    def spread_points(self, ts_us, real_bid, real_ask, point) -> float:
        sp = self.base.spread_points(ts_us, real_bid, real_ask, point)
        dt = self._utc(ts_us)

        # Rollover window.
        if dt.hour == 22:
            sp *= self.rollover_multiplier

        # News windows.
        if self.news_times_us:
            win_us = self.window_sec * US_PER_SEC
            for nt in self.news_times_us:
                if abs(ts_us - nt) <= win_us:
                    sp *= self.news_multiplier
                    break
        return sp


# Bảng tên -> factory để GUI/CLI dựng model từ string.
def build_model(name: str, **kwargs) -> SpreadModel:
    """Factory tiện cho GUI/CLI: build_model('real', min_points=6)."""
    name = name.lower()
    table = {
        "real": RealSpread,
        "fixed": FixedSpread,
        "random": RandomSpread,
        "session": SessionSpread,
        "news": NewsWidenSpread,
    }
    if name not in table:
        raise ValueError(f"Spread model không hợp lệ: {name!r}. "
                         f"Chọn: {', '.join(table)}")
    return table[name](**kwargs)
