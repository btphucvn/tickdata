"""
Symbol metadata thong nhat cho Dukascopy — divisor da VERIFY bang data that.

Moi symbol:
  code     : ma trong URL datafeed Dukascopy (vd "EURUSD", "USA500IDXUSD")
  divisor  : chia raw int trong bi5 -> gia that  (da kiem tra tung cai)
  digits   : so chu so thap phan MT4 (= log10(divisor) thong thuong)
  contract : contract size cho FXT header
  profit   : 0=forex, 1=CFD/kim loai/index/crypto

Divisor da verify (download that tu Dukascopy):
  FX 5-digit -> /100000   (EURUSD raw 110366 -> 1.10366)
  FX JPY     -> /1000
  XAU/XAG    -> /1000      (XAU raw 1733008 -> 1733.008)
  BTC/ETH/LTC-> /10        (BTC raw 454186 -> 45418.6)
  Index/Oil  -> /1000      (S&P raw 4745876 -> 4745.876, Oil 73218 -> 73.218)

Symbol nguoi dung go (vd "US500") -> map sang code Dukascopy ("USA500IDXUSD").
"""

# Helper: tao entry FX nhanh
def _fx(divisor=100000, digits=5):
    return dict(divisor=divisor, digits=digits, contract=100000.0, profit=0)

def _jpy():
    return dict(divisor=1000, digits=3, contract=100000.0, profit=0)

def _metal(contract):
    return dict(divisor=1000, digits=3, contract=contract, profit=1)

def _crypto(contract=1.0):
    return dict(divisor=10, digits=1, contract=contract, profit=1)

def _index(contract=1.0):
    return dict(divisor=1000, digits=3, contract=contract, profit=1)


# code Dukascopy -> metadata
SYMBOLS = {
    # ---- FX majors ----
    "EURUSD": _fx(), "GBPUSD": _fx(), "AUDUSD": _fx(), "NZDUSD": _fx(),
    "USDCAD": _fx(), "USDCHF": _fx(),
    "USDJPY": _jpy(),
    # ---- FX crosses ----
    "EURGBP": _fx(), "EURCHF": _fx(), "EURAUD": _fx(), "EURCAD": _fx(),
    "EURNZD": _fx(), "GBPAUD": _fx(), "GBPCAD": _fx(), "GBPCHF": _fx(),
    "GBPNZD": _fx(), "AUDCAD": _fx(), "AUDCHF": _fx(), "AUDNZD": _fx(),
    "CADCHF": _fx(), "NZDCAD": _fx(), "NZDCHF": _fx(),
    "EURJPY": _jpy(), "GBPJPY": _jpy(), "AUDJPY": _jpy(), "NZDJPY": _jpy(),
    "CADJPY": _jpy(), "CHFJPY": _jpy(),
    # ---- FX exotics ----
    "USDSGD": _fx(), "USDHKD": _fx(), "USDMXN": _fx(), "USDZAR": _fx(),
    "USDTRY": _fx(), "USDNOK": _fx(), "USDSEK": _fx(), "USDDKK": _fx(),
    "USDPLN": _fx(), "USDCNH": _fx(), "EURPLN": _fx(), "EURSEK": _fx(),
    "EURNOK": _fx(), "EURTRY": _fx(), "EURHUF": _jpy(), "USDHUF": _jpy(),
    # ---- Kim loai ----
    "XAUUSD": _metal(100.0), "XAGUSD": _metal(5000.0),
    "XAUEUR": _metal(100.0), "XAGEUR": _metal(5000.0),
    "XPTUSD": _metal(100.0), "XPDUSD": _metal(100.0),
    # ---- Crypto (divisor 10) ----
    "BTCUSD": _crypto(1.0), "ETHUSD": _crypto(1.0), "LTCUSD": _crypto(1.0),
    "BCHUSD": _crypto(1.0), "XRPUSD": dict(divisor=100000, digits=5, contract=1.0, profit=1),
    # ---- Chi so (divisor 1000) ----
    "USA30IDXUSD":   _index(1.0),   # Dow Jones
    "USA500IDXUSD":  _index(1.0),   # S&P 500
    "USATECHIDXUSD": _index(1.0),   # Nasdaq 100
    "DEUIDXEUR":     _index(1.0),   # DAX
    "GBRIDXGBP":     _index(1.0),   # FTSE 100
    "FRAIDXEUR":     _index(1.0),   # CAC 40
    "JPNIDXJPY":     _index(1.0),   # Nikkei 225
    "EUSIDXEUR":     _index(1.0),   # Euro Stoxx 50
    "AUSIDXAUD":     _index(1.0),   # ASX 200
    "HKGIDXHKD":     _index(1.0),   # Hang Seng
    # ---- Nang luong / hang hoa ----
    "LIGHTCMDUSD": _index(1000.0),  # WTI Crude Oil
    "BRENTCMDUSD": _index(1000.0),  # Brent Oil
    "GASCMDUSD":   _index(1000.0),  # Natural Gas
    "COPPERCMDUSD":_index(1000.0),  # Copper
}

# Alias: ten nguoi dung quen go -> code Dukascopy
ALIASES = {
    "US30": "USA30IDXUSD", "DJ30": "USA30IDXUSD", "DOW": "USA30IDXUSD",
    "US500": "USA500IDXUSD", "SPX500": "USA500IDXUSD", "SP500": "USA500IDXUSD",
    "US100": "USATECHIDXUSD", "USTEC": "USATECHIDXUSD", "NAS100": "USATECHIDXUSD",
    "NASDAQ": "USATECHIDXUSD",
    "DE40": "DEUIDXEUR", "DAX": "DEUIDXEUR", "GER40": "DEUIDXEUR", "DE30": "DEUIDXEUR",
    "UK100": "GBRIDXGBP", "FTSE": "GBRIDXGBP",
    "FR40": "FRAIDXEUR", "CAC40": "FRAIDXEUR",
    "JP225": "JPNIDXJPY", "NIKKEI": "JPNIDXJPY",
    "EU50": "EUSIDXEUR", "STOXX50": "EUSIDXEUR",
    "AUS200": "AUSIDXAUD", "HK50": "HKGIDXHKD",
    "USOIL": "LIGHTCMDUSD", "WTI": "LIGHTCMDUSD", "CRUDE": "LIGHTCMDUSD",
    "UKOIL": "BRENTCMDUSD", "BRENT": "BRENTCMDUSD",
    "NATGAS": "GASCMDUSD", "NGAS": "GASCMDUSD", "COPPER": "COPPERCMDUSD",
}


class SymbolMeta:
    def __init__(self, name, code, divisor, digits, contract, profit):
        self.name     = name       # ten hien thi / luu store (theo nguoi dung go)
        self.code     = code       # code download Dukascopy
        self.divisor  = divisor
        self.digits   = digits
        self.contract = contract
        self.profit   = profit

    @property
    def point(self):
        return 10.0 ** (-self.digits)

    def __repr__(self):
        return (f"SymbolMeta({self.name} code={self.code} /{self.divisor} "
                f"digits={self.digits} point={self.point})")


def resolve(symbol):
    """
    Tra SymbolMeta cho symbol nguoi dung go.
    Thu tu: alias -> bang SYMBOLS -> heuristic (suy doan).
    """
    key = symbol.upper().strip()
    # Bo hau to broker (vd EURUSD.m, XAUUSD-ECN)
    base = key.split('.')[0].split('-')[0].split('_')[0]

    # 1. Alias?
    code = ALIASES.get(base, base)

    # 2. Co trong bang?
    if code in SYMBOLS:
        m = SYMBOLS[code]
        return SymbolMeta(key, code, m["divisor"], m["digits"],
                          m["contract"], m["profit"])

    # 3. Heuristic cho symbol la (van download duoc, chi can doan divisor)
    if "JPY" in base:
        return SymbolMeta(key, code, 1000, 3, 100000.0, 0)
    if base.startswith(("XAU", "XAG", "XPT", "XPD")):
        return SymbolMeta(key, code, 1000, 3, 100.0, 1)
    if base.endswith("USD") and len(base) == 6 and base[:3] in (
            "BTC", "ETH", "LTC", "BCH", "XMR", "DSH", "ETC"):
        return SymbolMeta(key, code, 10, 1, 1.0, 1)
    if "IDX" in base or "CMD" in base:
        return SymbolMeta(key, code, 1000, 3, 1.0, 1)
    # Mac dinh: FX 5-digit
    return SymbolMeta(key, code, 100000, 5, 100000.0, 0)


def known_symbols():
    """Danh sach ten quen thuoc cho dropdown (alias + code chinh)."""
    names = set(ALIASES.keys())
    # Them cac code FX/metal/crypto (de dung truc tiep)
    for c in SYMBOLS:
        if "IDX" not in c and "CMD" not in c:
            names.add(c)
    return sorted(names)
