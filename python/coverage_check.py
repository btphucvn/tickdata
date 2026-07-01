"""
Kiem tra do phu tick data — bao dam dat model quality 99% (giong TDS).

MT4 tinh model quality theo mat do tick: thieu gio giao dich nao -> tut duoi 99%.
Module nay phat hien gio giao dich BI TRONG (co the do loi tai 503) de retry.

Lich Forex (UTC): mo Chu Nhat 21:00 -> dong Thu Sau 21:00.
  - Thu 7: dong ca ngay
  - Chu Nhat < 21h: dong
  - Thu 6 >= 21h: dong
Cac gio con lai = gio giao dich -> phai co tick (tru holiday).
"""

import datetime


def is_trading_hour(dt_utc):
    """dt_utc: datetime UTC. Tra True neu la gio Forex mo cua."""
    wd = dt_utc.weekday()   # 0=Mon ... 5=Sat 6=Sun
    h = dt_utc.hour
    if wd == 5:                       # Thu 7
        return False
    if wd == 6 and h < 21:            # Chu Nhat sang
        return False
    if wd == 4 and h >= 21:           # Thu 6 toi
        return False
    return True


def expected_trading_hours(date_from, date_to):
    """Sinh list (year, month, day, hour) cua cac gio giao dich trong khoang."""
    out = []
    day = date_from
    while day <= date_to:
        for h in range(24):
            dt = datetime.datetime(day.year, day.month, day.day, h,
                                   tzinfo=datetime.timezone.utc)
            if is_trading_hour(dt):
                out.append((day.year, day.month, day.day, h))
        day += datetime.timedelta(days=1)
    return out


def analyze_coverage(ticks, date_from, date_to):
    """
    Phan tich do phu: tra dict {
       trading_hours, hours_with_data, missing_hours(list), coverage_pct, ticks
    }
    missing_hours = gio giao dich KHONG co tick (holiday that hoac gap can retry).
    """
    # Tap hop cac (y,m,d,h) co tick
    have = set()
    n_ticks = 0
    for time_ms, bid, ask in ticks:   # ho tro ca list lan generator (RAM thap)
        n_ticks += 1
        dt = datetime.datetime.utcfromtimestamp(time_ms / 1000)
        have.add((dt.year, dt.month, dt.day, dt.hour))

    expected = expected_trading_hours(date_from, date_to)
    missing = [e for e in expected if e not in have]
    n_exp = len(expected)
    n_have = n_exp - len(missing)
    pct = (n_have / n_exp * 100) if n_exp else 100.0

    return {
        "trading_hours":   n_exp,
        "hours_with_data": n_have,
        "missing_hours":   missing,
        "coverage_pct":    pct,
        "ticks":           n_ticks,
    }


def estimate_model_quality(coverage_pct):
    """
    Uoc luong model quality MT4 se hien.
    Coverage cao (tick day du gio giao dich) -> MT4 cho ~99.9%.
    Day la uoc luong; con so that do MT4 tu tinh khi backtest.
    """
    if coverage_pct >= 99.5:
        return "~99.9% (Every tick, real ticks day du)"
    if coverage_pct >= 98:
        return "~99% (gan day du, vai gio thieu)"
    if coverage_pct >= 90:
        return f"~{coverage_pct:.0f}% (co gap, nen retry)"
    return f"~{coverage_pct:.0f}% (THIEU NHIEU - can tai lai)"
