"""
Tai tick qua freeserv.dukascopy.com (dung thu vien dukascopy-python).

QUAN TRONG: freeserv.dukascopy.com la endpoint KHAC voi datafeed.dukascopy.com.
Khi datafeed bi chan (tarpit IP), freeserv VAN HOAT DONG -> day la nguon du phong
dang tin cay. Data y het (cung Dukascopy).

fetch() tra DataFrame co ca bidPrice + askPrice -> 1 lan goi du ca 2 gia.
"""

import datetime

# Timeout (giay) cho MOI request phan trang cua thu vien freeserv. Thu vien goc goi
# requests.get() KHONG co timeout -> 1 request ket la treo VO HAN. Tiem timeout vao ->
# qua han thi requests ne loi -> vong retry co san cua thu vien (_stream, max_retries=7)
# tu thu lai -> tu khoi phuc, KHONG treo. Nho vay chay song song nhe van an toan.
REQ_TIMEOUT = 25.0

_dk = None
_ins = None


class _ReqTimeoutShim:
    """
    Chuyen tiep moi thuoc tinh sang 'requests' that, nhung .get() duoc:
      1) THEM timeout  -> khong treo vo han.
      2) di qua 1 Session (keep-alive) -> tai dung ket noi TCP/TLS thay vi bat tay
         lai moi request -> NHANH hon (moi ngay ~12-15 request nen an nhieu).
    """
    def __init__(self, real):
        self._real = real
        self._sess = real.Session()
        # Pool rong de nhieu luong (neu chay song song) van keep-alive tot.
        try:
            adapter = real.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
            self._sess.mount("https://", adapter)
        except Exception:
            pass
    def __getattr__(self, name):
        return getattr(self._real, name)
    def get(self, *args, **kwargs):
        kwargs.setdefault("timeout", REQ_TIMEOUT)
        return self._sess.get(*args, **kwargs)


def _lazy():
    global _dk, _ins
    if _dk is None:
        import logging
        import requests
        import dukascopy_python
        import dukascopy_python.instruments as ins
        # Tiem timeout: thay 'requests' trong namespace thu vien bang shim (chi anh
        # huong thu vien, khong dong toi requests toan cuc).
        try:
            dukascopy_python.requests = _ReqTimeoutShim(requests)
        except Exception:
            pass
        # Tat log INFO on cua thu vien (dat SAU khi import de khong bi ghi de)
        lg = logging.getLogger("DUKASCRIPT")
        lg.setLevel(logging.ERROR)
        lg.propagate = False
        _dk = dukascopy_python
        _ins = ins
    return _dk, _ins


# Map symbol nguoi dung -> instrument freeserv (vd "EUR/USD")
# Index/oil co format rieng -> bang tra.
_INDEX_MAP = {
    "US500": "USA500.IDX/USD", "USA500IDXUSD": "USA500.IDX/USD",
    "US30":  "USA30.IDX/USD",  "USA30IDXUSD":  "USA30.IDX/USD",
    "US100": "USATEC.IDX/USD", "USTEC": "USATEC.IDX/USD", "USATECHIDXUSD": "USATEC.IDX/USD",
    "DE40":  "DEU.IDX/EUR", "DAX": "DEU.IDX/EUR", "GER40": "DEU.IDX/EUR",
    "UK100": "GBR.IDX/GBP", "FTSE": "GBR.IDX/GBP",
    "JP225": "JPN.IDX/JPY",
    "USOIL": "E_Light", "WTI": "E_Light", "LIGHTCMDUSD": "E_Light",
    "UKOIL": "E_Brent", "BRENT": "E_Brent", "BRENTCMDUSD": "E_Brent",
}


def symbol_to_instrument(symbol):
    """Doi symbol (EURUSD, XAUUSD, BTCUSD, US500...) -> instrument freeserv."""
    s = symbol.upper().split('.')[0].split('-')[0]
    if s in _INDEX_MAP:
        return _INDEX_MAP[s]
    # FX / metal / crypto 6 ky tu -> XXX/YYY
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    # da co dang co '/'
    if '/' in symbol:
        return symbol
    raise ValueError(f"Khong map duoc symbol '{symbol}' sang instrument freeserv. "
                     f"Them vao _INDEX_MAP trong freeserv_fetch.py")


def fetch_range(symbol, date_from, date_to, progress_cb=None):
    """
    Tai tick [date_from, date_to] qua freeserv. Tra list[(time_ms, bid, ask)] da sort.
    date_from/date_to: datetime.date hoac datetime.datetime (UTC).
    """
    dk, ins = _lazy()
    instrument = symbol_to_instrument(symbol)

    # Chuyen sang datetime UTC
    def to_dt(d, end=False):
        if isinstance(d, datetime.datetime):
            dt = d
        else:
            dt = datetime.datetime(d.year, d.month, d.day,
                                   23, 59, 59 if end else 0) if end else \
                 datetime.datetime(d.year, d.month, d.day)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt

    start = to_dt(date_from)
    end   = to_dt(date_to, end=True)

    df = dk.fetch(instrument, dk.INTERVAL_TICK, dk.OFFER_SIDE_BID, start, end)
    if df is None or len(df) == 0:
        return []

    # Vectorized (numpy) — nhanh hon iterrows() hang chuc lan
    times_ms = (df.index.view("int64") // 1_000_000)        # ns -> ms
    bids = df["bidPrice"].to_numpy()
    asks = df["askPrice"].to_numpy()
    ticks = [(int(t), float(b), float(a))
             for t, b, a in zip(times_ms.tolist(), bids.tolist(), asks.tolist())
             if b > 0 and a > 0]
    ticks.sort(key=lambda t: t[0])
    if progress_cb:
        progress_cb(1, 1, len(ticks))
    return ticks


def available():
    """Kiem tra thu vien dukascopy-python co cai khong."""
    try:
        _lazy()
        return True
    except ImportError:
        return False
