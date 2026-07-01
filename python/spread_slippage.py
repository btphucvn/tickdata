"""
Mo hinh Spread + Slippage giong TDS — phan "an tien" de ket qua backtest khop TDS.

SPREAD: ap cong thuc clamp(real*mult + add, min, max) (xem settings_store.SymbolSettings).

SLIPPAGE: TDS ap slippage luc KHOP LENH (khong phai luc sinh tick). Module nay:
  1. Cung cap mo hinh Python (de verify / cho duong #import EA).
  2. Dinh nghia BLOCK THAM SO slippage nhi phan de nhet vao shared memory, cho
     native order-hook tu tinh (dam bao deterministic + dong bo voi fill).

Cac che do slippage cua TDS (theo ITickDataSettings):
  - latency_based   : tre khop lenh min..max ms -> gia truot theo bien dong trong khoang do.
  - dealer_style    : truot khong-doi-xung, gioi han bang max_favorable/max_unfavorable.
  - standard_dev    : truot ~ Normal(mean, stdev) points.
  - custom_chance   : xac suat co slippage; favorable_chance: xac suat truot CO LOI.

Quy uoc dau: slippage points DUONG = bat loi (fill xau hon), AM = co loi.
"""

import math
import random
import struct

# Order type giong MT4
OP_BUY, OP_SELL = 0, 1
OP_BUYLIMIT, OP_SELLLIMIT = 2, 3
OP_BUYSTOP, OP_SELLSTOP = 4, 5

MAGIC_SLIP = 0x504C5354   # 'TSLP' -> block tham so slippage trong SHM


class SlippageModel:
    """Tinh slippage (points, co dau) cho 1 lenh, theo SymbolSettings."""

    def __init__(self, settings, seed: int = 12345):
        self.s = settings
        # reproducible -> RNG co seed co dinh; nguoc lai random that
        self.rng = random.Random(seed if settings.reproducible_slippage else None)

    def _order_uses_slippage(self, order_type: int) -> bool:
        s = self.s
        if order_type in (OP_BUY, OP_SELL):
            return True                      # market luon ap (neu enabled)
        if order_type in (OP_BUYLIMIT, OP_SELLLIMIT):
            return s.limit_order_slippage
        if order_type in (OP_BUYSTOP, OP_SELLSTOP):
            return s.stop_order_slippage
        return True

    def slippage_points(self, order_type: int, recent_volatility_pts: float = 0.0,
                        is_sl_tp: int = 0) -> float:
        """
        Tra slippage (points, co dau) cho lenh.
        recent_volatility_pts: bien dong gia gan day (points) — cho latency-based.
        is_sl_tp: 1=SL, 2=TP (de ap sl_order/tp_order_slippage).
        """
        s = self.s
        if not s.slippage_enabled:
            return 0.0
        if is_sl_tp == 1 and not s.sl_order_slippage:
            return 0.0
        if is_sl_tp == 2 and not s.tp_order_slippage:
            return 0.0
        if not self._order_uses_slippage(order_type):
            return 0.0

        # Xac suat co slippage
        chance = s.custom_slippage_chance if s.use_custom_slippage_chance else 100.0
        if self.rng.uniform(0, 100) > chance:
            return 0.0

        # --- Tinh do lon slippage theo che do ---
        if s.standard_deviation_slippage:
            mag = abs(self.rng.gauss(s.slippage_mean, max(1e-9, s.slippage_stdev)))
        elif s.latency_based_slippage:
            lo, hi = s.min_market_slippage_delay, max(s.min_market_slippage_delay,
                                                      s.max_market_slippage_delay)
            delay_ms = self.rng.uniform(lo, hi)
            # truot ~ bien dong * ty le thoi gian tre (mo hinh don gian, co the tinh chinh)
            mag = recent_volatility_pts * (delay_ms / 1000.0)
        elif s.dealer_style_slippage:
            mag = self.rng.uniform(0, max(s.max_favorable_slippage,
                                          s.max_unfavorable_slippage))
        else:
            # mac dinh: dong deu trong [0, max_unfavorable] hoac 1 default nho
            mx = s.max_unfavorable_slippage or 5
            mag = self.rng.uniform(0, mx)

        # --- Dau (favorable/unfavorable) ---
        fav_chance = (s.favorable_slippage_chance
                      if s.use_custom_favorable_chance else 50.0)
        favorable = self.rng.uniform(0, 100) < fav_chance
        sign = -1.0 if favorable else 1.0

        # Gioi han theo dealer-style
        if s.dealer_style_slippage:
            cap = s.max_favorable_slippage if favorable else s.max_unfavorable_slippage
            if cap > 0:
                mag = min(mag, cap)

        return sign * mag


# ---------------------------------------------------------------------------
# Block tham so slippage cho shared memory (cho native order-hook tu tinh)
# ---------------------------------------------------------------------------
def pack_slippage_params(settings, seed: int = 12345) -> bytes:
    """
    Dong goi tham so slippage thanh block nhi phan de native doc.

    Layout (little-endian, packed):
      uint32 magic = 'TSLP'
      uint32 enabled
      uint32 mode          # 0=default,1=latency,2=dealer,3=stddev
      uint32 seed
      uint32 flags         # bit0 limit, bit1 stop, bit2 sl, bit3 tp, bit4 reproducible
      float  custom_chance
      float  favorable_chance
      float  max_favorable
      float  max_unfavorable
      float  mean
      float  stdev
      float  min_delay_ms
      float  max_delay_ms
    """
    s = settings
    if s.standard_deviation_slippage:
        mode = 3
    elif s.dealer_style_slippage:
        mode = 2
    elif s.latency_based_slippage:
        mode = 1
    else:
        mode = 0
    flags = ((1 if s.limit_order_slippage else 0) << 0 |
             (1 if s.stop_order_slippage else 0) << 1 |
             (1 if s.sl_order_slippage else 0) << 2 |
             (1 if s.tp_order_slippage else 0) << 3 |
             (1 if s.reproducible_slippage else 0) << 4)
    chance = s.custom_slippage_chance if s.use_custom_slippage_chance else 100.0
    fav = s.favorable_slippage_chance if s.use_custom_favorable_chance else 50.0
    return struct.pack(
        "<IIIII8f",
        MAGIC_SLIP,
        1 if s.slippage_enabled else 0,
        mode, seed & 0xFFFFFFFF, flags,
        float(chance), float(fav),
        float(s.max_favorable_slippage), float(s.max_unfavorable_slippage),
        float(s.slippage_mean), float(s.slippage_stdev),
        float(s.min_market_slippage_delay), float(s.max_market_slippage_delay),
    )


if __name__ == "__main__":
    import settings_store
    s = settings_store.load("EURUSD")
    s.slippage_enabled = True
    s.standard_deviation_slippage = True
    s.slippage_mean = 2.0
    s.slippage_stdev = 1.5
    m = SlippageModel(s, seed=1)
    vals = [m.slippage_points(OP_BUY) for _ in range(8)]
    print("Slippage mau (points):", [round(v, 2) for v in vals])
    blk = pack_slippage_params(s)
    print(f"Slippage param block: {len(blk)} bytes, magic OK={blk[:4]==b'TSLP'}")
