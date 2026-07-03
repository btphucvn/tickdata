"""
Dukascopy tick downloader — giống cách TDS download data.
URL format: https://datafeed.dukascopy.com/datafeed/{SYM}/{YEAR}/{MM}/{DD}/{HH}h_ticks.bi5
  - Month 0-indexed (Jan=00, Dec=11)
  - File: LZMA-compressed, moi record 20 bytes big-endian:
      uint32 ms_offset, uint32 ask_raw, uint32 bid_raw, float ask_vol, float bid_vol
  - Gia thuc = raw / 100000 (EURUSD 5-digit), hoac / 1000 (JPY pairs)

Usage:
  python download_ticks.py --symbol EURUSD --from 2024-01-01 --to 2024-01-31
  python download_ticks.py --symbol EURUSD --from 2024-01-01 --to 2024-01-31 --out ticks.bin
"""

import struct
import lzma
import datetime
import os
import sys
import argparse
import time
import threading
import random

from symbols_meta import resolve   # metadata thong nhat (code + divisor)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
TICK_RECORD = 20   # bytes per tick in decompressed bi5
# Header gia browser day du (hoc tu TDS — no lay UA Chrome moi tu eareview.net/tds/ua.php).
# UA Chrome hien tai giam rui ro bi chan vi "UA cu/bot".
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dukascopy.com/",
    "Connection": "keep-alive",
}

# Cache file .bi5 tho tren dia -> tai 1 lan duy nhat, re-run KHONG tai lai
# (day la cach chinh tranh bi Dukascopy chan vi re-download)
RAW_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")


# ---------------------------------------------------------------------------
# Rate limiter toan cuc (token bucket) — gioi han request/giay du co nhieu thread.
# Day la "phep lich su" giup KHONG bi Dukascopy chan.
# ---------------------------------------------------------------------------
class _RateLimiter:
    def __init__(self, rate_per_sec):
        self.interval = 1.0 / rate_per_sec
        self.lock = threading.Lock()
        self.next_t = time.time()

    def set_rate(self, rate_per_sec):
        with self.lock:
            self.interval = 1.0 / rate_per_sec

    def acquire(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next_t)
            self.next_t = t + self.interval
            wait = t - now
        if wait > 0:
            time.sleep(wait)

# Mac dinh ~18 req/s — nhanh nhung du an toan. Co the chinh qua set_rate.
_limiter = _RateLimiter(18)

# ---------------------------------------------------------------------------
# Phat hien bi chan: dem so loi lien tiep (503/timeout). Neu cao -> nghi (cooldown)
# va tu dong cham lai de tranh leo thang thanh ban cung.
# ---------------------------------------------------------------------------
_fail_lock = threading.Lock()
_consec_fail = 0
_cooldown_until = 0.0
_net_errors = 0          # so gio that bai do loi mang (khong phai 404) trong phien


class RateLimitedError(Exception):
    """Nem khi phat hien IP bi Dukascopy chan (qua nhieu request that bai)."""


def _reset_net_errors():
    global _net_errors
    with _fail_lock:
        _net_errors = 0

def _inc_net_error():
    global _net_errors
    with _fail_lock:
        _net_errors += 1
        return _net_errors

def _note_result(ok):
    """Cap nhat bo dem loi. Tra (consec_fail, can_cooldown)."""
    global _consec_fail
    with _fail_lock:
        if ok:
            _consec_fail = 0
            return 0, False
        _consec_fail += 1
        return _consec_fail, (_consec_fail in (15, 40, 80))

def _maybe_cooldown(consec, trigger):
    """Neu nhieu loi lien tiep -> nghi va giam toc do (chong bi chan)."""
    global _cooldown_until
    if not trigger:
        # Neu dang trong cooldown, doi
        with _fail_lock:
            wait = _cooldown_until - time.time()
        if wait > 0:
            time.sleep(min(wait, 5))
        return
    # Trigger cooldown: giam rate + nghi
    secs = min(15 + consec, 60)
    new_rate = max(4, 18 - consec // 10)
    _limiter.set_rate(new_rate)
    with _fail_lock:
        _cooldown_until = time.time() + secs
    sys.stderr.write(f"\n[!] Nhieu loi lien tiep ({consec}) - co the bi rate-limit. "
                     f"Nghi {secs}s, giam con {new_rate} req/s...\n")
    sys.stderr.flush()
    time.sleep(secs)


def _cache_path(code, year, month, day, hour):
    return os.path.join(RAW_DIR, code, f"{year:04d}",
                        f"{month:02d}", f"{day:02d}", f"{hour:02d}h.bi5")

# ---------------------------------------------------------------------------
# HTTP session voi connection pooling + keep-alive (giong toc do TDS).
# Tai dung TCP connection thay vi mo moi cho moi request -> nhanh hon nhieu.
# Fallback ve urllib neu khong co 'requests'.
# ---------------------------------------------------------------------------
_session = None
_session_lock = threading.Lock()

def _get_session():
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        try:
            import requests
            from requests.adapters import HTTPAdapter
            s = requests.Session()
            s.headers.update(HEADERS)
            # pool lon de nhieu thread tai dung connection
            adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        except ImportError:
            _session = False   # khong co requests -> dung urllib
    return _session


def _http_get(url, timeout=10):
    """GET tra (status_code, bytes). status -1 = loi mang."""
    s = _get_session()
    if s:
        try:
            r = s.get(url, timeout=timeout)
            return r.status_code, (r.content if r.status_code == 200 else b"")
        except Exception:
            return -1, b""
    # Fallback urllib
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return -1, b""


def download_hour_ticks(symbol, year, month, day, hour, retries=3, use_cache=True):
    """
    Download + parse 1 gio tick. Tra list[(time_ms,bid,ask)] hoac [].
    - Cache .bi5 tren dia: tai 1 lan, re-run doc tu dia (khong dung mang -> khong bi chan).
    - Rate limiter + cooldown tu dong neu bi rate-limit.
    symbol: ten nguoi dung go (EURUSD, US500, BTCUSD) -> tu map sang code Dukascopy.
    """
    meta = resolve(symbol)
    cache = _cache_path(meta.code, year, month, day, hour)

    compressed = None
    # 1. Thu doc tu cache dia truoc
    if use_cache and os.path.exists(cache):
        try:
            with open(cache, "rb") as f:
                compressed = f.read()
        except OSError:
            compressed = None

    # 2. Khong co cache -> tai tu mang (qua rate limiter)
    if compressed is None:
        mm = f"{month - 1:02d}"   # Dukascopy month 0-indexed
        url = f"{BASE_URL}/{meta.code}/{year}/{mm}/{day:02d}/{hour:02d}h_ticks.bi5"
        for attempt in range(retries):
            _limiter.acquire()                      # ton trong rate toan cuc
            status, body = _http_get(url)
            if status == 200:
                compressed = body
                _note_result(True)
                # Luu cache (du body rong cung luu de khong tai lai)
                try:
                    os.makedirs(os.path.dirname(cache), exist_ok=True)
                    with open(cache, "wb") as f:
                        f.write(body)
                except OSError:
                    pass
                break
            if status == 404:
                _note_result(True)   # 404 la phan hoi hop le, khong phai bi chan
                # Cache marker rong cho data CU (>5 ngay) -> re-run khong request lai.
                # Data moi co the co tick sau, nen khong cache.
                age_days = (datetime.datetime.now(datetime.timezone.utc).date()
                            - datetime.date(year, month, day)).days
                if use_cache and age_days > 5:
                    try:
                        os.makedirs(os.path.dirname(cache), exist_ok=True)
                        open(cache, "wb").close()   # file 0 byte = "khong co data"
                    except OSError:
                        pass
                return []
            # 503/timeout -> dem loi, co the cooldown, roi backoff
            consec, trigger = _note_result(False)
            _maybe_cooldown(consec, trigger)
            if attempt == retries - 1:
                _inc_net_error()   # gio nay that bai do loi mang
                return []
            time.sleep(min(0.5 * (2 ** attempt), 8.0) + random.uniform(0, 0.5))

    if not compressed:
        return []

    try:
        raw = lzma.decompress(compressed)
    except lzma.LZMAError:
        return []

    divisor = meta.divisor
    base_ms = int(datetime.datetime(year, month, day, hour,
                                    tzinfo=datetime.timezone.utc).timestamp() * 1000)
    ticks = []
    for i in range(0, len(raw) - TICK_RECORD + 1, TICK_RECORD):
        ms_off, ask_raw, bid_raw, _av, _bv = struct.unpack_from(">IIIff", raw, i)
        bid = bid_raw / divisor
        ask = ask_raw / divisor
        if bid <= 0 or ask <= 0:
            continue
        if bid > ask:            # crossed-quote (feed loi) -> SWAP giong TDS
            bid, ask = ask, bid
        ticks.append((base_ms + ms_off, bid, ask))

    return ticks


def _all_hours(date_from, date_to):
    """Sinh list (y, m, d, h) cho moi gio trong khoang."""
    hours = []
    day = date_from
    while day <= date_to:
        for h in range(24):
            hours.append((day.year, day.month, day.day, h))
        day += datetime.timedelta(days=1)
    return hours


def _download_hours_parallel(symbol, hours, progress=True, max_workers=10,
                             progress_cb=None, log_days=False):
    """
    Tai SONG SONG nhieu gio bang ThreadPool (giong TDS). Tra list ticks (chua sort).
    progress_cb(done, total, ticks_so_far) — callback tien do (cho GUI).
    log_days: True -> in 1 dong moi khi xong 1 ngay (cho datafeed, vi 'hours' theo thu tu).
    """
    from concurrent.futures import ThreadPoolExecutor

    all_ticks = []
    total = len(hours)
    done = 0
    t0 = time.time()
    _reset_net_errors()

    # Theo doi tick theo ngay (hours theo thu tu chronological -> done//24 = chi so ngay)
    cur_day = None
    day_ticks = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # map giu thu tu nop -> ket qua tra theo thu tu hoan thanh
        for idx, ticks in enumerate(
                ex.map(lambda hms: download_hour_ticks(symbol, *hms), hours)):
            n = len(ticks) if ticks else 0
            if ticks:
                all_ticks.extend(ticks)
            done += 1

            # Log theo ngay (datafeed)
            if log_days:
                y, m, d, h = hours[idx]
                day = (y, m, d)
                if cur_day is None:
                    cur_day = day
                if day != cur_day:
                    print(f"  {cur_day[0]}-{cur_day[1]:02d}-{cur_day[2]:02d}: "
                          f"{day_ticks:,} ticks  | tong {len(all_ticks):,}")
                    cur_day = day
                    day_ticks = 0
                day_ticks += n

            # Phat hien IP bi chan SOM: sau 12 gio dau, neu >75% loi mang -> dung ngay
            # (tranh treo hang chuc phut khi bi chan)
            if done >= 12 and _net_errors >= done * 0.75:
                raise RateLimitedError(
                    f"IP bi Dukascopy chan tam thoi ({_net_errors}/{done} gio loi mang). "
                    f"Hay doi ~15-20 phut roi thu lai.")

            if progress and (done % 8 == 0 or done == total):
                pct = done * 100 // total
                bar = "#" * (pct // 5) + "." * (20 - pct // 5)
                rate = done / max(time.time() - t0, 0.01)
                sys.stdout.write(f"\r  [{bar}] {pct:3d}%  {done}/{total} gio  "
                                 f"{len(all_ticks):,} ticks  {rate:.0f} gio/s   ")
                sys.stdout.flush()
            if progress_cb:
                progress_cb(done, total, len(all_ticks))

    # Log ngay cuoi cung
    if log_days and cur_day is not None:
        print(f"  {cur_day[0]}-{cur_day[1]:02d}-{cur_day[2]:02d}: "
              f"{day_ticks:,} ticks  | tong {len(all_ticks):,}")

    if progress:
        print()
    if _net_errors:
        print(f"  [!] {_net_errors}/{total} gio loi mang (timeout/503) — "
              f"co the bi rate-limit hoac mat ket noi")
    return all_ticks


def download_range_ticks(symbol, date_from, date_to, progress=True,
                         max_workers=10, progress_cb=None):
    """
    Download tick trong [date_from, date_to] SONG SONG, tra list da sort.
    max_workers: so ket noi dong thoi (mac dinh 10, nhanh nho keep-alive).
    """
    hours = _all_hours(date_from, date_to)
    all_ticks = _download_hours_parallel(symbol, hours, progress,
                                         max_workers, progress_cb)
    all_ticks.sort(key=lambda t: t[0])
    return all_ticks


def download_complete(symbol, date_from, date_to, progress=True, max_refill=2,
                      max_workers=10, progress_cb=None):
    """
    Download DAY DU SONG SONG: tai toan bo, kiem tra gio giao dich bi trong,
    retry lap lai -> dat model quality 99%.

    max_workers: so ket noi dong thoi (mac dinh 10).
    Tra (ticks, coverage_report).
    """
    from coverage_check import analyze_coverage, estimate_model_quality

    print(f"[*] Download {symbol}: {date_from} -> {date_to}  ({max_workers} luong song song)")
    ticks = download_range_ticks(symbol, date_from, date_to, progress,
                                 max_workers, progress_cb)

    # Vong refill: retry cac gio giao dich con trong (cung song song)
    for round_no in range(max_refill):
        cov = analyze_coverage(ticks, date_from, date_to)
        missing = cov["missing_hours"]
        if not missing:
            break
        print(f"[*] Coverage {cov['coverage_pct']:.1f}% — lap day {len(missing)} gio trong "
              f"(vong {round_no+1})")
        extra = _download_hours_parallel(symbol, missing, progress=False,
                                         max_workers=max_workers)
        if not extra:
            break   # so gio con lai la holiday that -> dung
        ticks.extend(extra)
        ticks.sort(key=lambda t: t[0])

    cov = analyze_coverage(ticks, date_from, date_to)
    print(f"[*] Tong: {len(ticks):,} ticks  |  coverage gio giao dich: "
          f"{cov['coverage_pct']:.2f}%  ({cov['hours_with_data']}/{cov['trading_hours']})")
    print(f"[*] Model quality du kien: {estimate_model_quality(cov['coverage_pct'])}")
    if cov["missing_hours"]:
        print(f"    ({len(cov['missing_hours'])} gio con trong — phan lon la nghi le, binh thuong)")
    return ticks, cov


def _iter_months(date_from, date_to):
    """Sinh list (month_from_date, month_to_date) cho tung thang trong khoang."""
    out = []
    y, m = date_from.year, date_from.month
    while (y, m) <= (date_to.year, date_to.month):
        m_start = datetime.date(y, m, 1)
        # ngay cuoi thang
        if m == 12:
            nxt = datetime.date(y + 1, 1, 1)
        else:
            nxt = datetime.date(y, m + 1, 1)
        m_end = nxt - datetime.timedelta(days=1)
        # cat theo bien [date_from, date_to]
        lo = max(m_start, date_from)
        hi = min(m_end, date_to)
        out.append((y, m, lo, hi))
        y, m = (nxt.year, nxt.month)
    return out


_CRYPTO_BASES = ("BTC", "ETH", "LTC", "BCH", "XRP", "XMR", "DSH", "ETC", "ADA",
                 "DOT", "LNK", "SOL", "DOGE", "MATIC", "AVAX", "UNI", "AAVE")

def _is_crypto(symbol):
    """True neu symbol la crypto (giao dich 24/7, freeserv hay treo -> nen dung datafeed)."""
    base = symbol.upper().split('.')[0].split('-')[0].split('_')[0]
    return base.startswith(_CRYPTO_BASES)


def _fetch_month(symbol, lo, hi, source, max_workers, max_refill, progress_cb):
    """Tai 1 thang theo 'source'. Tra list ticks da sort."""
    from coverage_check import analyze_coverage

    use_freeserv = False
    if source == "freeserv":
        use_freeserv = True
    elif source == "auto":
        # UU TIEN freeserv cho MOI symbol (ke ca crypto): freeserv.dukascopy.com la
        # endpoint KHAC datafeed -> KHONG bi rate-limit/chan IP. Da verify BTCUSD chay
        # tot (~6s/ngay, data khop datafeed). Neu khong co thu vien -> fallback datafeed.
        try:
            import freeserv_fetch
            use_freeserv = freeserv_fetch.available()
            if use_freeserv:
                print(f"  [auto] {symbol} -> freeserv (khong bi chan IP)")
        except ImportError:
            use_freeserv = False

    if use_freeserv:
        # freeserv (dukascopy-python) — endpoint khac, KHONG bi chan nhu datafeed.
        # Tai NHIEU NGAY SONG SONG (ThreadPool) -> nhanh hon nhieu.
        import freeserv_fetch
        from concurrent.futures import ThreadPoolExecutor, as_completed

        days = []
        d = lo
        while d <= hi:
            days.append(d)
            d += datetime.timedelta(days=1)

        total_h = len(days) * 24
        results = {}
        done = 0
        n_err = 0
        n_empty = 0
        # So luong ngay tai song song. freeserv chiu duoc kha cao.
        nw = max(1, min(max_workers, len(days)))
        print(f"  [freeserv] tai {len(days)} ngay ({lo} -> {hi}), {nw} luong song song")
        with ThreadPoolExecutor(max_workers=nw) as ex:
            futs = {ex.submit(freeserv_fetch.fetch_range, symbol, dd, dd): dd
                    for dd in days}
            for fut in as_completed(futs):
                dd = futs[fut]
                try:
                    results[dd] = fut.result()
                except Exception as e:
                    results[dd] = []
                    n_err += 1
                    print(f"  [LOI] {dd}: {type(e).__name__}: {str(e)[:120]}")
                done += 1
                n = len(results[dd])
                got = sum(len(v) for v in results.values())
                if n == 0:
                    n_empty += 1
                    print(f"  {dd}: 0 ticks (nghi le/cuoi tuan?)  | tong {got:,}")
                else:
                    print(f"  {dd}: {n:,} ticks  | tong {got:,}")
                if progress_cb:
                    progress_cb(done * 24, total_h, got)
        if n_err:
            print(f"  [!] {n_err} ngay loi mang, {n_empty} ngay rong trong thang nay")

        ticks = []
        for dd in days:               # ghep lai dung thu tu thoi gian
            ticks.extend(results.get(dd, []))
        ticks.sort(key=lambda t: t[0])
        return ticks

    # datafeed bi5 (song song, qua cache + rate limiter)
    print(f"  [datafeed] tai {(hi-lo).days+1} ngay ({lo} -> {hi}), "
          f"{max_workers} luong song song")
    hours = _all_hours(lo, hi)
    ticks = _download_hours_parallel(symbol, hours, progress=False,
                                     max_workers=max_workers, progress_cb=progress_cb,
                                     log_days=True)
    for _ in range(max_refill):
        cov = analyze_coverage(ticks, lo, hi)
        if not cov["missing_hours"]:
            break
        extra = _download_hours_parallel(symbol, cov["missing_hours"],
                                         progress=False, max_workers=max_workers)
        if not extra:
            break
        ticks.extend(extra)
    ticks.sort(key=lambda t: t[0])
    return ticks


def _resolve_source(source):
    """Quy 'auto' ve nguon cu the 1 lan (khoi hoi lai moi ngay). Tra 'freeserv'|'datafeed'."""
    if source in ("freeserv", "datafeed"):
        return source
    try:
        import freeserv_fetch
        if freeserv_fetch.available():
            return "freeserv"
    except ImportError:
        pass
    return "datafeed"


def _fetch_one_day(symbol, y, m, d, resolved):
    """Tai + parse 1 NGAY tick -> list[(time_ms,bid,ask)] da sort. RAM ~1 ngay."""
    if resolved == "freeserv":
        import freeserv_fetch
        return freeserv_fetch.fetch_range(symbol, datetime.date(y, m, d),
                                          datetime.date(y, m, d))
    # datafeed: gom 24 gio (qua cache bi5 + rate limiter)
    ticks = []
    for h in range(24):
        ticks.extend(download_hour_ticks(symbol, y, m, d, h))
    ticks.sort(key=lambda t: t[0])
    return ticks


# freeserv.dukascopy.com thinh thoang bi throttle -> ham fetch() KHONG co timeout ->
# treo VO HAN. Dat tran/ngay: qua nguong nay coi nhu treo. Khi treo -> nghi roi THU LAI
# CHINH freeserv (khong dung datafeed). Van treo sau nhieu lan -> dung sach de resume.
_DAY_TIMEOUT = 150.0
_HANG_RETRIES = 3       # so lan thu lai 1 ngay khi freeserv treo
_HANG_COOLDOWN = 12.0   # giay nghi giua cac lan thu (de throttle giam)


def _fetch_day_timed(symbol, y, m, d, resolved, timeout):
    """
    Tai 1 ngay voi TIMEOUT (chay trong 1 luong rieng). Tra list ticks hoac nem
    concurrent.futures.TimeoutError neu qua gio. KHONG cho luong treo (shutdown wait=False).
    """
    import concurrent.futures as cf
    ex1 = cf.ThreadPoolExecutor(max_workers=1)
    fut = ex1.submit(_fetch_one_day, symbol, y, m, d, resolved)
    try:
        return fut.result(timeout=timeout)   # nem TimeoutError neu treo
    finally:
        ex1.shutdown(wait=False, cancel_futures=True)


def download_month_stream(symbol, y, m, lo, hi, source, max_workers,
                          append_day_cb, last_ms=None, progress_cb=None,
                          done_base=0, total_hours=0):
    """
    Tai 1 thang theo NGAY, LUU TUNG NGAY ngay lap tuc -> RAM thap + resume theo ngay.

    Khac han cach cu (gom ca thang vao RAM roi moi luu 1 lan): o day RAM chi giu
    ~max_workers ngay, va tien do duoc ghi xuong dia sau MOI ngay -> crash giua chung
    KHONG mat ca thang.

    - append_day_cb(y, m, day_ticks): luu 1 ngay (caller noi vao tick_store.append_day).
    - last_ms: timestamp cuoi da co trong store thang -> bo qua ngay da xong (resume),
      va loc tick <= last_ms de khong trung khi tai lai ngay ghi do dang.
    Tra tong tick MOI them trong thang.
    """
    import concurrent.futures as _cf
    from concurrent.futures import ThreadPoolExecutor

    resolved = _resolve_source(source)

    days = []
    d = lo
    while d <= hi:
        days.append(d)
        d += datetime.timedelta(days=1)

    # Resume: bo qua ngay da nam GON trong store (ket thuc <= last_ms).
    start_idx = 0
    if last_ms is not None:
        for i, dd in enumerate(days):
            day_end = int(datetime.datetime(dd.year, dd.month, dd.day, 23, 59, 59,
                          tzinfo=datetime.timezone.utc).timestamp() * 1000)
            if day_end <= last_ms:
                start_idx = i + 1
            else:
                break
    pending = days[start_idx:]
    if not pending:
        return 0

    added = 0
    cur_last = last_ms
    done_h = done_base + start_idx * 24
    nw = max(1, min(max_workers, len(pending)))

    mode = "tuan tu (1 ngay/lan)" if nw == 1 else f"{nw} luong song song"
    print(f"  [{resolved}] {symbol} thang {y}-{m:02d}: tai {len(pending)} ngay "
          f"({pending[0]} -> {pending[-1]}), {mode}"
          + (f"  [resume, bo qua {start_idx} ngay da co]" if start_idx else ""))

    def _get_day(dd, first_future=None):
        """
        Lay ticks 1 ngay. first_future: ket qua da submit (che do song song) hoac None
        (che do tuan tu -> tu chay co timeout). Treo -> nghi + thu lai CHINH freeserv;
        van treo sau _HANG_RETRIES lan -> raise RateLimitedError (khong dung datafeed).
        """
        try:
            if first_future is not None:
                return first_future.result(timeout=_DAY_TIMEOUT)
            return _fetch_day_timed(symbol, dd.year, dd.month, dd.day, resolved, _DAY_TIMEOUT)
        except _cf.TimeoutError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  [LOI] {dd}: {type(e).__name__}: {str(e)[:120]}")
            return []
        for attempt in range(1, _HANG_RETRIES + 1):
            print(f"  [TREO] {dd}: {resolved} qua {_DAY_TIMEOUT:.0f}s khong phan hoi "
                  f"-> nghi {_HANG_COOLDOWN:.0f}s roi thu lai ({attempt}/{_HANG_RETRIES})")
            time.sleep(_HANG_COOLDOWN)
            try:
                return _fetch_day_timed(symbol, dd.year, dd.month, dd.day, resolved, _DAY_TIMEOUT)
            except _cf.TimeoutError:
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  [LOI] {dd}: {type(e).__name__}: {str(e)[:120]}")
                return []
        raise RateLimitedError(
            f"Nguon '{resolved}' treo lien tuc o ngay {dd} (da thu {_HANG_RETRIES} lan). "
            f"Da luu toi truoc do — chay lai de tai tiep.")

    def _store(dd, day_ticks):
        nonlocal added, cur_last, done_h
        # Chi giu tick moi hon phan da luu -> append an toan, khong trung.
        if cur_last is not None and day_ticks:
            day_ticks = [t for t in day_ticks if t[0] > cur_last]
        if day_ticks:
            append_day_cb(dd.year, dd.month, day_ticks)   # ghi xuong dia NGAY
            added += len(day_ticks)
            cur_last = day_ticks[-1][0]
            print(f"  {dd}: {len(day_ticks):,} ticks  | thang +{added:,}")
        else:
            print(f"  {dd}: 0 ticks (nghi le/cuoi tuan?)")
        done_h += 24
        if progress_cb:
            progress_cb(done_h, total_hours, added)

    if nw == 1:
        # TUAN TU — tai tung ngay mot, KHONG chia luong song song.
        for dd in pending:
            _store(dd, _get_day(dd))
    else:
        # Song song theo LO, nhung APPEND dung THU TU ngay -> file luon sort tang.
        # KHONG dung 'with' (exit se shutdown wait=True -> treo theo luong freeserv);
        # tu shutdown(wait=False) o finally de bo luong treo.
        ex = ThreadPoolExecutor(max_workers=nw)
        try:
            i = 0
            while i < len(pending):
                batch = pending[i:i + nw]
                futs = [ex.submit(_fetch_one_day, symbol, dd.year, dd.month, dd.day, resolved)
                        for dd in batch]
                for dd, fut in zip(batch, futs):
                    _store(dd, _get_day(dd, first_future=fut))
                i += nw
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    return added


def download_months(symbol, date_from, date_to, append_day_cb=None,
                    month_last_ms_cb=None, month_done_cb=None,
                    progress_cb=None, pause_sec=2.0, max_workers=16,
                    source="auto", save_month_cb=None, max_refill=1):
    """
    Tai TUNG THANG. Uu tien luu TUNG NGAY (RAM thap + resume theo ngay).

    source: "auto" (freeserv neu co, else datafeed), "freeserv", "datafeed".
      - freeserv.dukascopy.com: endpoint chart API, KHONG bi chan khi datafeed bi tarpit.

    Che do streaming (khuyen dung): truyen
      - append_day_cb(year, month, day_ticks): luu 1 NGAY (noi vao tick_store.append_day).
      - month_last_ms_cb(year, month) -> int|None: timestamp cuoi da co trong thang (resume).
    Che do cu (tuong thich): truyen save_month_cb(year, month, ticks_ca_thang).

    - progress_cb(done_hours, total_hours, ticks): cho thanh bar.
    - month_done_cb(year, month, n_ticks_moi): bao xong 1 thang.

    Tra (tong_tick_moi, so_thang_xong).
    """
    months = _iter_months(date_from, date_to)
    total_hours = sum((hi - lo).days * 24 + 24 for (_, _, lo, hi) in months)
    done_hours = 0
    grand_total = 0
    months_done = 0

    for idx, (y, m, lo, hi) in enumerate(months):
        base = done_hours
        gt = grand_total

        if append_day_cb is not None:
            def cb(dh, th, nticks, _g=gt):
                if progress_cb:
                    progress_cb(dh, total_hours, _g + nticks)
            last_ms = month_last_ms_cb(y, m) if month_last_ms_cb else None
            added = download_month_stream(
                symbol, y, m, lo, hi, source, max_workers,
                append_day_cb=append_day_cb, last_ms=last_ms,
                progress_cb=cb, done_base=base, total_hours=total_hours)
            print(f"[OK] {y}-{m:02d}: +{added:,} ticks moi (luu tung ngay)")
        else:
            # Che do cu: gom ca thang roi luu 1 lan (giu tuong thich CLI).
            from coverage_check import analyze_coverage
            def cb(done, total, nticks, _b=base, _g=gt):
                if progress_cb:
                    progress_cb(_b + done, total_hours, _g + nticks)
            ticks = _fetch_month(symbol, lo, hi, source, max_workers, max_refill, cb)
            if save_month_cb:
                save_month_cb(y, m, ticks)
            added = len(ticks)
            cov = analyze_coverage(ticks, lo, hi)
            print(f"[OK] {y}-{m:02d}: {added:,} ticks  (coverage {cov['coverage_pct']:.0f}%)")

        grand_total += added
        months_done += 1
        done_hours += (hi - lo).days * 24 + 24
        if month_done_cb:
            month_done_cb(y, m, added)

        if pause_sec and idx < len(months) - 1:
            time.sleep(pause_sec)

    return grand_total, months_done


def download_range(symbol, date_from, date_to, out_path, progress=True):
    """
    Download toan bo tick trong khoang [date_from, date_to].
    Luu ra file nhi phan: header(16) + N x record(24).
    Record: int64 time_ms, double bid, double ask
    """
    print(f"[*] Download {symbol} tick data: {date_from} -> {date_to}")
    all_ticks = download_range_ticks(symbol, date_from, date_to, progress)
    print(f"[*] Tong cong: {len(all_ticks):,} ticks")

    # Ghi file nhi phan
    # Header: magic(4) + version(4) + count(8) = 16 bytes
    # Record: time_ms(int64) + bid(double) + ask(double) = 24 bytes
    MAGIC = b"TDST"
    count = len(all_ticks)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", 1))          # version
        f.write(struct.pack("<Q", count))      # count uint64
        for time_ms, bid, ask in all_ticks:
            f.write(struct.pack("<qdd", time_ms, bid, ask))

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[OK] Da luu {count:,} ticks vao: {out_path}  ({size_mb:.1f} MB)")
    return out_path


def load_ticks(path):
    """Doc file tick da luu. Tra list[(time_ms, bid, ask)]."""
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"TDST":
            raise ValueError("File khong phai dinh dang TDST")
        version, = struct.unpack("<I", f.read(4))
        count,   = struct.unpack("<Q", f.read(8))
        ticks = []
        for _ in range(count):
            time_ms, bid, ask = struct.unpack("<qdd", f.read(24))
            ticks.append((time_ms, bid, ask))
    return ticks


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dukascopy tick downloader")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--from",   dest="date_from", required=True,
                    help="YYYY-MM-DD")
    ap.add_argument("--to",     dest="date_to",   required=True,
                    help="YYYY-MM-DD")
    ap.add_argument("--out",    default="",
                    help="Output .bin path (mac dinh: ticks_{SYM}_{FROM}_{TO}.bin)")
    args = ap.parse_args()

    d_from = datetime.date.fromisoformat(args.date_from)
    d_to   = datetime.date.fromisoformat(args.date_to)

    out = args.out or f"ticks_{args.symbol}_{args.date_from}_{args.date_to}.bin"
    download_range(args.symbol, d_from, d_to, out)
