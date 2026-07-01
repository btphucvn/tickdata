"""
Tick store theo symbol — LUU MOI THANG 1 FILE (scalable cho data nhieu nam).

Cau truc:
  data/ticks/{SYMBOL}/{YYYY-MM}.bin     # 1 file moi thang

Moi file: header magic 'TDST'(4) + version(4) + count(8) = 16 byte,
          tiep theo count x record(24): int64 time_ms, double bid, double ask (sorted).

Vi sao KHONG dung 1 file khong lo + merge:
  merge phai nap toan bo file vao RAM -> 8 nam crypto = hang GB -> NO RAM/cham.
  Luu moi thang 1 file: tai thang nao ghi thang do, build FXT thi STREAM qua tung
  thang -> RAM thap, resume tu nhien (thang co file = bo qua), giong cach TDS luu.
"""

import os
import struct
import datetime

MAGIC = b"TDST"
REC = 24
HDR = 16

STORE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ticks")


# ---------------------------------------------------------------------------
# Duong dan
# ---------------------------------------------------------------------------
def store_dir(symbol):
    d = os.path.join(STORE_DIR, symbol.upper())
    return d

def month_file(symbol, year, month):
    return os.path.join(store_dir(symbol), f"{year:04d}-{month:02d}.bin")

def _old_single_file(symbol):
    return os.path.join(STORE_DIR, f"{symbol.upper()}.bin")


# ---------------------------------------------------------------------------
# Migration: tach file cu {SYMBOL}.bin -> cac file thang
# ---------------------------------------------------------------------------
def _migrate_if_needed(symbol):
    old = _old_single_file(symbol)
    if not os.path.exists(old):
        return
    print(f"[migrate] Tach {symbol}.bin (cu) -> file theo thang...")
    # Doc tung record, gom theo thang, ghi file thang
    by_month = {}
    try:
        with open(old, "rb") as f:
            if f.read(4) != MAGIC:
                os.remove(old); return
            f.seek(HDR)
            while True:
                buf = f.read(REC)
                if len(buf) < REC:
                    break
                t_ms = struct.unpack_from("<q", buf, 0)[0]
                dt = datetime.datetime.utcfromtimestamp(t_ms / 1000)
                by_month.setdefault((dt.year, dt.month), bytearray()).extend(buf)
    except OSError:
        return
    os.makedirs(store_dir(symbol), exist_ok=True)
    for (y, m), data in by_month.items():
        n = len(data) // REC
        _write_month_raw(symbol, y, m, n, bytes(data))
    os.remove(old)
    print(f"[migrate] Xong: {len(by_month)} thang.")


def _write_month_raw(symbol, year, month, count, record_bytes):
    """Ghi file thang ATOMIC tu bytes record da chuan bi (da sort)."""
    os.makedirs(store_dir(symbol), exist_ok=True)
    path = month_file(symbol, year, month)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<Q", count))
        f.write(record_bytes)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Ghi / doc 1 thang
# ---------------------------------------------------------------------------
def save_month(symbol, year, month, ticks):
    """
    Ghi data 1 thang (GOP voi data thang da co, sort + dedupe). Atomic.
    Gop trong pham vi 1 thang -> r-e RAM (khac han merge ca store khong lo).
    ticks: list[(time_ms, bid, ask)].
    """
    if not ticks:
        return _count_in_file(month_file(symbol, year, month))
    # Gop voi data thang hien co (neu tai 1 phan thang / tai lai)
    by_time = {t[0]: t for t in load_month(symbol, year, month)}
    for t in ticks:
        by_time[t[0]] = t
    items = sorted(by_time.values(), key=lambda t: t[0])
    buf = bytearray()
    for t in items:
        buf += struct.pack("<qdd", t[0], t[1], t[2])
    _write_month_raw(symbol, year, month, len(items), bytes(buf))
    return len(items)


def has_month(symbol, year, month):
    return os.path.exists(month_file(symbol, year, month))


def load_month(symbol, year, month):
    """Doc 1 thang -> list[(time_ms,bid,ask)]."""
    path = month_file(symbol, year, month)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            if f.read(4) != MAGIC:
                return []
            f.seek(HDR)
            data = f.read()
    except OSError:
        return []
    out = []
    for off in range(0, len(data) - REC + 1, REC):
        out.append(struct.unpack_from("<qdd", data, off))
    return out


# ---------------------------------------------------------------------------
# Liet ke / thong ke
# ---------------------------------------------------------------------------
def _month_list(symbol):
    """List (year, month) co file, sap xep tang dan."""
    d = store_dir(symbol)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if fn.endswith(".bin") and len(fn) == 11:  # YYYY-MM.bin
            try:
                y, m = int(fn[:4]), int(fn[5:7])
                out.append((y, m))
            except ValueError:
                pass
    return sorted(out)


def _count_in_file(path):
    """So record theo KICH THUOC file (khong tin header)."""
    try:
        return max(0, (os.path.getsize(path) - HDR) // REC)
    except OSError:
        return 0


def month_counts(symbol):
    """Tra dict {(year, month): so_tick} — doc nhanh tu kich thuoc file."""
    _migrate_if_needed(symbol)
    counts = {}
    for (y, m) in _month_list(symbol):
        counts[(y, m)] = _count_in_file(month_file(symbol, y, m))
    return counts


def coverage(symbol):
    """Tra (first_ms, last_ms, total_count) hoac None neu rong."""
    _migrate_if_needed(symbol)
    months = _month_list(symbol)
    if not months:
        return None
    total = sum(_count_in_file(month_file(symbol, y, m)) for (y, m) in months)
    if total == 0:
        return None
    # first tick = record dau file thang dau co data
    first = last = None
    for (y, m) in months:
        path = month_file(symbol, y, m)
        n = _count_in_file(path)
        if n == 0:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(HDR)
                first = struct.unpack("<qdd", f.read(REC))[0]
            break
        except (OSError, struct.error):
            continue
    for (y, m) in reversed(months):
        path = month_file(symbol, y, m)
        n = _count_in_file(path)
        if n == 0:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(HDR + (n - 1) * REC)
                last = struct.unpack("<qdd", f.read(REC))[0]
            break
        except (OSError, struct.error):
            continue
    if first is None or last is None:
        return None
    return first, last, total


def list_symbols():
    """Liet ke symbol da tai. Tra list dict."""
    # migrate cac file cu truoc
    if os.path.isdir(STORE_DIR):
        for fn in os.listdir(STORE_DIR):
            if fn.endswith(".bin"):
                _migrate_if_needed(fn[:-4])

    out = []
    if not os.path.isdir(STORE_DIR):
        return out
    for sym in sorted(os.listdir(STORE_DIR)):
        d = os.path.join(STORE_DIR, sym)
        if not os.path.isdir(d):
            continue
        size = 0
        for (y, m) in _month_list(sym):
            try:
                size += os.path.getsize(month_file(sym, y, m))
            except OSError:
                pass
        cov = coverage(sym)
        if cov:
            out.append(dict(symbol=sym, first_ms=cov[0], last_ms=cov[1],
                            count=cov[2], size_mb=size/1024/1024, path=d))
        elif size or os.path.isdir(d):
            out.append(dict(symbol=sym, first_ms=0, last_ms=0,
                            count=0, size_mb=size/1024/1024, path=d))
    return out


def delete_symbol(symbol):
    """Xoa toan bo data 1 symbol."""
    import shutil
    _migrate_if_needed(symbol)
    d = store_dir(symbol)
    removed = False
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        removed = True
    old = _old_single_file(symbol)
    if os.path.exists(old):
        os.remove(old); removed = True
    return removed


# ---------------------------------------------------------------------------
# Stream / load theo range (cho build FXT) — RAM thap
# ---------------------------------------------------------------------------
def iter_all(symbol):
    """Generator: yield (time_ms,bid,ask) toan bo store theo thu tu thoi gian."""
    _migrate_if_needed(symbol)
    for (y, m) in _month_list(symbol):
        path = month_file(symbol, y, m)
        try:
            with open(path, "rb") as f:
                if f.read(4) != MAGIC:
                    continue
                f.seek(HDR)
                while True:
                    data = f.read(REC * 4096)   # doc theo lo
                    if not data:
                        break
                    for off in range(0, len(data) - REC + 1, REC):
                        yield struct.unpack_from("<qdd", data, off)
        except OSError:
            continue


def iter_range(symbol, from_ms, to_ms):
    """Generator: yield tick trong [from_ms, to_ms]."""
    for t in iter_all(symbol):
        if t[0] < from_ms:
            continue
        if t[0] > to_ms:
            break
        yield t


def load_range(symbol, from_ms, to_ms):
    """List tick trong [from_ms, to_ms] (cho range vua phai)."""
    return list(iter_range(symbol, from_ms, to_ms))


def load_all(symbol):
    """List toan bo (canh bao: ton RAM neu data lon — uu tien iter_all)."""
    return list(iter_all(symbol))


# Tuong thich nguoc: ham cu store_path(symbol) -> tra thu muc symbol
def store_path(symbol):
    return store_dir(symbol)
