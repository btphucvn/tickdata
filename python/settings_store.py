"""
Settings store per-symbol — mo phong dung interface ITickDataSettings cua TDS that.

Tham chieu: tds_decompiled/TDSManaged/TDSManaged.decompiled.cs (interface
ITickDataSettings, dong 9247-9345). Moi field o day map 1-1 voi TDS.

Luu vao data/settings.db (SQLite) — TACH RIENG khoi data/manifest.db (cua package
tdsclone) de khong xung dot. Service + GUI + orchestrator deu doc tu day.

Cong thuc spread cua TDS (xac nhan tu interface):
    spread_final_pts = clamp(real_spread_pts * SpreadMultiplier + SpreadAddition,
                             MinSpread, MaxSpread)

Khi UseVariableSpread = False -> dung spread co dinh = SpreadAddition (giong tester thuong).
"""

import os
import sqlite3
from dataclasses import dataclass, asdict, fields

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "settings.db")


# ---------------------------------------------------------------------------
# Dataclass: 1 ban ghi settings = toan bo ITickDataSettings cua TDS
# ---------------------------------------------------------------------------
@dataclass
class SymbolSettings:
    symbol: str = "EURUSD"

    # ---- Timezone (TDS: GmtOffset, Dst) ----
    # MAC DINH GMT+2 / DST=US (giong TDS: GMTOffset=2, DST=1) -> khop gio broker.
    gmt_offset: int = 2           # gio lech data so voi GMT (mac dinh +2 nhu TDS)
    dst: int = 1                  # 0=none, 1=US, 2=EU (mac dinh US nhu TDS)

    # ---- Variable spread (cong thuc loi cua TDS) ----
    use_variable_spread: bool = True
    spread_multiplier: float = 1.0    # nhan vao real spread
    spread_addition: float = 0.0      # cong them (points). Cung la spread co dinh khi tat variable
    min_spread: int = 0               # chan duoi (points), 0 = khong chan
    max_spread: int = 0               # chan tren (points), 0 = khong chan

    # ---- Slippage (TDS slippage model day du) ----
    slippage_enabled: bool = False
    reproducible_slippage: bool = True    # cung seed -> cung ket qua (de optimize)
    optimization_slippage: bool = False   # ap slippage ca khi optimize
    limit_order_slippage: bool = True
    stop_order_slippage: bool = True
    sl_order_slippage: bool = True
    tp_order_slippage: bool = False

    # Latency-based (do tre khop lenh sinh slippage)
    latency_based_slippage: bool = False
    min_market_slippage_delay: int = 0    # ms
    max_market_slippage_delay: int = 0
    min_pending_slippage_delay: int = 0
    max_pending_slippage_delay: int = 0

    # Dealer-style (giai khong-doi-xung favorable/unfavorable)
    dealer_style_slippage: bool = False
    max_favorable_slippage: int = 0       # points
    max_unfavorable_slippage: int = 0     # points

    # Chance tuy chinh
    use_custom_slippage_chance: bool = False
    custom_slippage_chance: float = 50.0      # %
    use_custom_favorable_chance: bool = False
    favorable_slippage_chance: float = 50.0   # %

    # Standard deviation (slippage phan phoi chuan)
    standard_deviation_slippage: bool = False
    slippage_mean: float = 0.0            # points
    slippage_stdev: float = 0.0           # points

    # ---- Override symbol properties (TDS Override*) ----
    override_base_currency: bool = False
    base_currency: str = "USD"
    override_digits: bool = False
    digits: int = 5
    override_min_lot: bool = False
    min_lot: float = 0.01
    override_max_lot: bool = False
    max_lot: float = 100.0
    override_lot_step: bool = False
    lot_step: float = 0.01
    override_stops_level: bool = False
    stops_level: int = 0

    # ---- Commission (TDS co tab rieng) ----
    commission_per_lot: float = 0.0       # tien / lot / chieu
    commission_currency: str = "USD"

    def spread_points_for(self, real_spread_pts: float) -> float:
        """Ap dung cong thuc spread cua TDS cho 1 tick.

        real_spread_pts = (ask - bid) / point cua tick that.
        Tra ve spread (points) sau khi ap mult/add/clamp.
        """
        if not self.use_variable_spread:
            # Spread co dinh = spread_addition (giong field 'spread' trong FXT header)
            return float(self.spread_addition)
        sp = real_spread_pts * self.spread_multiplier + self.spread_addition
        if self.min_spread > 0:
            sp = max(sp, float(self.min_spread))
        if self.max_spread > 0:
            sp = min(sp, float(self.max_spread))
        return max(0.0, sp)


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------
def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _ensure_table(con):
    cols = []
    for f in fields(SymbolSettings):
        if f.type == "str":
            sqltype = "TEXT"
        elif f.type == "bool" or f.type == "int":
            sqltype = "INTEGER"
        else:
            sqltype = "REAL"
        pk = " PRIMARY KEY" if f.name == "symbol" else ""
        cols.append(f"{f.name} {sqltype}{pk}")
    con.execute(f"CREATE TABLE IF NOT EXISTS symbol_settings ({', '.join(cols)})")
    # Bang trang thai toan cuc (active symbol cho service)
    con.execute("CREATE TABLE IF NOT EXISTS app_state "
                "(key TEXT PRIMARY KEY, value TEXT)")
    con.commit()


def load(symbol: str) -> SymbolSettings:
    """Doc settings 1 symbol. Neu chua co -> tra default (kem digits theo symbols_meta)."""
    sym = symbol.upper()
    con = _connect()
    _ensure_table(con)
    row = con.execute("SELECT * FROM symbol_settings WHERE symbol=?", (sym,)).fetchone()
    con.close()
    if row is None:
        return _default_for(sym)
    d = dict(row)
    # Ep kieu bool tu INTEGER
    kw = {}
    for f in fields(SymbolSettings):
        v = d.get(f.name)
        if f.type == "bool":
            kw[f.name] = bool(v)
        else:
            kw[f.name] = v
    return SymbolSettings(**kw)


def _default_for(sym: str) -> SymbolSettings:
    """Default thong minh: lay digits tu symbols_meta neu co."""
    s = SymbolSettings(symbol=sym)
    try:
        import symbols_meta
        m = symbols_meta.resolve(sym)
        s.digits = m.digits
        s.base_currency = "USD"
    except Exception:
        pass
    return s


def save(settings: SymbolSettings):
    """Luu (upsert) settings 1 symbol."""
    settings.symbol = settings.symbol.upper()
    con = _connect()
    _ensure_table(con)
    d = asdict(settings)
    # bool -> int
    for f in fields(SymbolSettings):
        if f.type == "bool":
            d[f.name] = 1 if d[f.name] else 0
    names = list(d.keys())
    placeholders = ", ".join("?" for _ in names)
    updates = ", ".join(f"{n}=excluded.{n}" for n in names if n != "symbol")
    con.execute(
        f"INSERT INTO symbol_settings ({', '.join(names)}) VALUES ({placeholders}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}",
        [d[n] for n in names])
    con.commit()
    con.close()


def list_configured() -> list:
    """Danh sach symbol da co settings rieng."""
    con = _connect()
    _ensure_table(con)
    rows = con.execute("SELECT symbol FROM symbol_settings ORDER BY symbol").fetchall()
    con.close()
    return [r["symbol"] for r in rows]


# ---------------------------------------------------------------------------
# App state (cho service): symbol/point dang active
# ---------------------------------------------------------------------------
def set_state(key: str, value: str):
    con = _connect()
    _ensure_table(con)
    con.execute("INSERT INTO app_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit()
    con.close()


def get_state(key: str, default=None):
    con = _connect()
    _ensure_table(con)
    row = con.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else default


if __name__ == "__main__":
    # Test nhanh
    s = load("EURUSD")
    print("Default EURUSD:", s.spread_points_for(8.0), "pts (real=8)")
    s.use_variable_spread = True
    s.spread_multiplier = 1.5
    s.spread_addition = 2.0
    s.max_spread = 30
    print("Sau mult=1.5 add=2 max=30, real=25:", s.spread_points_for(25.0))
    save(s)
    print("Reload:", load("EURUSD").spread_multiplier)
    print("Configured:", list_configured())
