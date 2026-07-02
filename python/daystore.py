"""
Kho tick theo NGAY — clone KIEN TRUC cua Tick Data Suite (TDS).

TDS luu tick tick trong file mot-file-moi-NGAY `Dukascopy/{SYM}/{YYYY}/{MM-DD}.bfc`
(nen) + catalog SQLite `tds.db` (bang datainfo per-ngay). Native cua TDS doc thang
file ngay (windowed) -> chay duoc trong tester 32-bit du lich su nhieu nam.

Module nay bam sat KIEN TRUC do, nhung dung FORMAT AN TOAN cua rieng minh cho than
file (codec .bfc cua TDS bi khoa trong DLL protected — xem BFC_FORMAT_RE.md), nen
gia LUON BIT-EXACT:

  File ngay: data/ticks/{SYM}/{YYYY}/{MM-DD}.tkd
    Header 36B:
      "TKD1"(4) | u32 version=1 | i64 day_start_ms | i64 first_ms | i64 last_ms | u32 count
    Body: LZMA(count x <qdd>  i64 time_ms, f64 bid, f64 ask)   -> nen ~5x, bit-exact.

  Catalog: data/ticks/catalog.db (SQLite, kieu tds.db)
    datainfo(name TEXT, day INTEGER, first_ms INTEGER, last_ms INTEGER, cnt INTEGER,
             size INTEGER, PRIMARY KEY(name, day))
    -> FILE la nguon-su-that; catalog la CACHE tra cuu nhanh (metadata GUI), tu dung
       bo/rebuild khi lech.

Public API GIU Y HET tick_store cu (append_day, load_month, iter_all, iter_range,
load_range_np, month_last_ms, month_counts, coverage, has_month, delete_symbol,
list_symbols, materialize_paths, clean_scratch, ...) -> consumer khong phai sua.

Native (fxt_virtual.cpp) TAM THOI van doc .bin: materialize_paths bung ngay -> .bin
scratch (data/_materialized) cho native mmap. Giai doan sau native se doc .tkd truc tiep.
"""

from __future__ import annotations

import os
import lzma
import zlib
import glob
import struct
import sqlite3
import datetime
import threading

REC = 24
_RECS = struct.Struct("<qdd")             # i64 time_ms, f64 bid, f64 ask
MS_PER_DAY = 86_400_000
MAGIC = b"TKD1"
HDR = 36                                   # magic4+ver4+day8+first8+last8+cnt4
COMPRESS_PRESET = 1                        # (v1 cu) LZMA preset — chi con doc de re-encode
DEFLATE_LEVEL = 6                          # (v2) raw DEFLATE -> native doc bang puff.c
VER_LZMA = 1                               # than = LZMA (.xz)  — dinh dang cu
VER_DEFLATE = 2                            # than = RAW DEFLATE — native (miniz/puff) doc duoc


def _deflate(raw):
    """Nen RAW deflate (khong header zlib) — puff.c giai ma duoc."""
    co = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    return co.compress(raw) + co.flush()


def _inflate(body, raw_size):
    """Giai RAW deflate. raw_size = kich thuoc bung (count*24)."""
    return zlib.decompress(body, -15, raw_size)

_BASE = os.path.dirname(os.path.dirname(__file__))
STORE_DIR = os.path.join(_BASE, "data", "ticks")
SCRATCH_DIR = os.path.join(_BASE, "data", "_materialized")
CATALOG_DB = os.path.join(STORE_DIR, "catalog.db")


# ===========================================================================
#  Duong dan
# ===========================================================================
def store_dir(symbol):
    return os.path.join(STORE_DIR, symbol.upper())


def _day_to_date(day_index):
    """day_index (so ngay tu epoch UTC) -> datetime.date."""
    return datetime.datetime.utcfromtimestamp(day_index * 86400).date()


def _day_file_by_index(symbol, day_index):
    d = _day_to_date(day_index)
    return os.path.join(store_dir(symbol), f"{d.year:04d}", f"{d.month:02d}-{d.day:02d}.tkd")


def _day_index_of(ms):
    return ms // MS_PER_DAY


# ===========================================================================
#  Catalog SQLite (cache) — file la nguon su that
# ===========================================================================
_cat_conns: dict[int, sqlite3.Connection] = {}
_cat_lock = threading.Lock()
_synced: set[str] = set()          # symbol da dong bo catalog trong phien nay
_migrated: set[str] = set()


def _catalog():
    """Ket noi catalog theo thread (sqlite conn khong cross-thread)."""
    tid = threading.get_ident()
    with _cat_lock:
        c = _cat_conns.get(tid)
        if c is not None:
            return c
    os.makedirs(STORE_DIR, exist_ok=True)
    c = sqlite3.connect(CATALOG_DB, timeout=30.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS datainfo(
                    name TEXT NOT NULL, day INTEGER NOT NULL,
                    first_ms INTEGER, last_ms INTEGER, cnt INTEGER, size INTEGER,
                    PRIMARY KEY(name, day))""")
    c.commit()
    with _cat_lock:
        _cat_conns[tid] = c
    return c


def close_all():
    with _cat_lock:
        for c in _cat_conns.values():
            try:
                c.close()
            except sqlite3.Error:
                pass
        _cat_conns.clear()
    _synced.clear()


def _read_header(path):
    """Doc header 1 file .tkd -> (day_index, first_ms, last_ms, count, size). None neu loi."""
    try:
        with open(path, "rb") as f:
            head = f.read(HDR)
        if len(head) < HDR or head[:4] != MAGIC:
            return None
        day_ms, first_ms, last_ms = struct.unpack_from("<qqq", head, 8)
        cnt = struct.unpack_from("<I", head, 32)[0]
        return (day_ms // MS_PER_DAY, first_ms, last_ms, cnt, os.path.getsize(path))
    except (OSError, struct.error):
        return None


def _rebuild_catalog(symbol):
    """Quet lai TAT CA file .tkd cua symbol -> ghi de catalog cho symbol do."""
    sym = symbol.upper()
    c = _catalog()
    rows = []
    for path in glob.glob(os.path.join(store_dir(sym), "*", "*.tkd")):
        h = _read_header(path)
        if h and h[3] > 0:
            rows.append((sym, h[0], h[1], h[2], h[3], h[4]))
    c.execute("DELETE FROM datainfo WHERE name=?", (sym,))
    if rows:
        c.executemany("INSERT OR REPLACE INTO datainfo VALUES(?,?,?,?,?,?)", rows)
    c.commit()


def _ensure(symbol):
    """Dam bao symbol da migrate (tu .tkz cu) + catalog dong bo voi file tren dia."""
    sym = symbol.upper()
    if sym not in _migrated:
        _migrate_old_format(sym)
        _migrated.add(sym)
    if sym in _synced:
        return
    # So file .tkd tren dia vs so dong catalog -> lech thi rebuild.
    n_files = len(glob.glob(os.path.join(store_dir(sym), "*", "*.tkd")))
    c = _catalog()
    n_cat = c.execute("SELECT COUNT(*) FROM datainfo WHERE name=?", (sym,)).fetchone()[0]
    if n_files != n_cat:
        _rebuild_catalog(sym)
    _synced.add(sym)


# ===========================================================================
#  Encode / decode 1 ngay
# ===========================================================================
def _pack_records(ticks):
    buf = bytearray()
    for t in ticks:
        buf += _RECS.pack(t[0], t[1], t[2])
    return bytes(buf)


def _unpack_records(raw):
    return [_RECS.unpack_from(raw, off) for off in range(0, len(raw) - REC + 1, REC)]


def _write_day(symbol, day_index, ticks):
    """Ghi 1 file ngay ATOMIC (ticks da sort, cung 1 ngay). Cap nhat catalog."""
    sym = symbol.upper()
    path = _day_file_by_index(sym, day_index)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = _pack_records(ticks)
    body = _deflate(raw)                    # v2: RAW DEFLATE -> native doc duoc
    day_ms = day_index * MS_PER_DAY
    first_ms, last_ms, cnt = ticks[0][0], ticks[-1][0], len(ticks)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", VER_DEFLATE))
        f.write(struct.pack("<qqq", day_ms, first_ms, last_ms))
        f.write(struct.pack("<I", cnt))
        f.write(body)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    c = _catalog()
    c.execute("INSERT OR REPLACE INTO datainfo VALUES(?,?,?,?,?,?)",
              (sym, day_index, first_ms, last_ms, cnt, os.path.getsize(path)))
    c.commit()


def _read_day_records(symbol, day_index):
    """Raw 24B record bytes cua 1 ngay. Doc theo VERSION (v1=LZMA cu, v2=deflate). b'' neu loi."""
    path = _day_file_by_index(symbol, day_index)
    try:
        with open(path, "rb") as f:
            head = f.read(HDR)
            if len(head) < HDR or head[:4] != MAGIC:
                return b""
            ver = struct.unpack_from("<I", head, 4)[0]
            cnt = struct.unpack_from("<I", head, 32)[0]
            body = f.read()
    except OSError:
        return b""
    try:
        if ver == VER_DEFLATE:
            return _inflate(body, cnt * REC)
        return lzma.decompress(body)          # v1 cu
    except (zlib.error, lzma.LZMAError):
        return b""


# ===========================================================================
#  Ghi
# ===========================================================================
def append_day(symbol, year, month, ticks):
    """
    Ghi ticks vao kho (gom theo NGAY UTC, GOP+dedupe voi ngay da co, ATOMIC).
    ticks: list[(time_ms, bid, ask)]. year/month giu de tuong thich (ngay thuc lay tu
    timestamp). Tra tong tick cua CAC NGAY vua dung.
    """
    if not ticks:
        return 0
    sym = symbol.upper()
    _ensure(sym)
    by_day: dict[int, list] = {}
    for t in ticks:
        by_day.setdefault(_day_index_of(t[0]), []).append(t)
    total = 0
    for day_index, day_ticks in by_day.items():
        existing = _read_day_records(sym, day_index)
        merged = {t[0]: t for t in _unpack_records(existing)} if existing else {}
        for t in day_ticks:
            merged[t[0]] = t           # tick moi ghi de khi trung ms
        items = sorted(merged.values(), key=lambda t: t[0])
        _write_day(sym, day_index, items)
        total += len(items)
    return total


def save_month(symbol, year, month, ticks):
    """Tuong thich store cu: ghi 'ca thang' (gom theo ngay). Tra tong tick thang."""
    append_day(symbol, year, month, ticks)
    return _month_count(symbol, year, month)


# ===========================================================================
#  Metadata (tu catalog — nhanh)
# ===========================================================================
def _month_day_range(year, month):
    d0 = int(datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    d1 = int(datetime.datetime(ny, nm, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    return d0 // MS_PER_DAY, d1 // MS_PER_DAY


def _month_count(symbol, year, month):
    sym = symbol.upper(); _ensure(sym)
    lo, hi = _month_day_range(year, month)
    r = _catalog().execute(
        "SELECT COALESCE(SUM(cnt),0) FROM datainfo WHERE name=? AND day>=? AND day<?",
        (sym, lo, hi)).fetchone()
    return int(r[0]) if r else 0


def month_last_ms(symbol, year, month):
    sym = symbol.upper(); _ensure(sym)
    lo, hi = _month_day_range(year, month)
    r = _catalog().execute(
        "SELECT MAX(last_ms) FROM datainfo WHERE name=? AND day>=? AND day<?",
        (sym, lo, hi)).fetchone()
    return r[0] if r and r[0] is not None else None


def has_month(symbol, year, month):
    return _month_count(symbol, year, month) > 0


def _month_list(symbol):
    sym = symbol.upper(); _ensure(sym)
    months = set()
    for (day,) in _catalog().execute("SELECT day FROM datainfo WHERE name=?", (sym,)):
        d = _day_to_date(day)
        months.add((d.year, d.month))
    return sorted(months)


def month_counts(symbol):
    sym = symbol.upper(); _ensure(sym)
    out: dict[tuple[int, int], int] = {}
    for day, cnt in _catalog().execute("SELECT day, cnt FROM datainfo WHERE name=?", (sym,)):
        d = _day_to_date(day)
        k = (d.year, d.month)
        out[k] = out.get(k, 0) + cnt
    return out


def coverage(symbol):
    sym = symbol.upper(); _ensure(sym)
    r = _catalog().execute(
        "SELECT MIN(first_ms), MAX(last_ms), COALESCE(SUM(cnt),0) FROM datainfo WHERE name=?",
        (sym,)).fetchone()
    if not r or r[2] == 0 or r[0] is None:
        return None
    return r[0], r[1], int(r[2])


# ===========================================================================
#  Doc
# ===========================================================================
def _days_in(symbol, day_lo, day_hi):
    """Danh sach day_index co data trong [day_lo, day_hi] (tang dan), tu catalog."""
    sym = symbol.upper(); _ensure(sym)
    return [row[0] for row in _catalog().execute(
        "SELECT day FROM datainfo WHERE name=? AND day>=? AND day<=? ORDER BY day",
        (sym, day_lo, day_hi))]


def load_month(symbol, year, month):
    lo, hi = _month_day_range(year, month)
    out = []
    for day in _days_in(symbol, lo, hi - 1):
        out.extend(_unpack_records(_read_day_records(symbol, day)))
    return out


def iter_all(symbol):
    sym = symbol.upper(); _ensure(sym)
    days = [row[0] for row in _catalog().execute(
        "SELECT day FROM datainfo WHERE name=? ORDER BY day", (sym,))]
    for day in days:
        for t in _unpack_records(_read_day_records(sym, day)):
            yield t


def iter_range(symbol, from_ms, to_ms):
    for day in _days_in(symbol, from_ms // MS_PER_DAY, to_ms // MS_PER_DAY):
        for t in _unpack_records(_read_day_records(symbol, day)):
            if t[0] < from_ms:
                continue
            if t[0] > to_ms:
                return
            yield t


def load_range(symbol, from_ms, to_ms):
    return list(iter_range(symbol, from_ms, to_ms))


def load_all(symbol):
    return list(iter_all(symbol))


def load_range_np(symbol, from_ms, to_ms):
    """numpy structured array (t,bid,ask) trong [from_ms, to_ms). None neu rong."""
    import numpy as np
    dt = np.dtype([('t', '<i8'), ('bid', '<f8'), ('ask', '<f8')])
    assert dt.itemsize == REC
    day_lo = from_ms // MS_PER_DAY
    day_hi = (to_ms - 1) // MS_PER_DAY if to_ms > 0 else -1
    arrs = []
    for day in _days_in(symbol, day_lo, day_hi):
        raw = _read_day_records(symbol, day)
        n = len(raw) // REC
        if n:
            arrs.append(np.frombuffer(raw, dtype=dt, count=n))
    if not arrs:
        return None
    a = arrs[0] if len(arrs) == 1 else np.concatenate(arrs)
    mask = (a['t'] >= from_ms) & (a['t'] < to_ms)
    return a[mask]


# ===========================================================================
#  Quan ly
# ===========================================================================
def delete_symbol(symbol):
    import shutil
    sym = symbol.upper()
    removed = False
    d = store_dir(sym)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True); removed = True
    try:
        _catalog().execute("DELETE FROM datainfo WHERE name=?", (sym,)); _catalog().commit()
    except sqlite3.Error:
        pass
    _synced.discard(sym); _migrated.discard(sym)
    clean_scratch(sym)
    return removed


def list_symbols():
    out = []
    if not os.path.isdir(STORE_DIR):
        return out
    for name in sorted(os.listdir(STORE_DIR)):
        d = os.path.join(STORE_DIR, name)
        if not os.path.isdir(d):
            continue
        sym = name.upper()
        size = 0
        for path in glob.glob(os.path.join(d, "*", "*.tkd")):
            try:
                size += os.path.getsize(path)
            except OSError:
                pass
        cov = coverage(sym)
        rec = dict(symbol=sym, size_mb=size / 1024 / 1024, path=d, compressed=1)
        if cov:
            rec.update(first_ms=cov[0], last_ms=cov[1], count=cov[2])
        else:
            rec.update(first_ms=0, last_ms=0, count=0)
        out.append(rec)
    return out


# ---- Tuong thich GUI cu (kho .tkd LUON nen -> cac ham nay thanh no-op) -----
def is_compressed(symbol, year=None, month=None):
    return True


def compress_month(symbol, year, month, preset=COMPRESS_PRESET):
    return (0, 0)


def compress_symbol(symbol, preset=COMPRESS_PRESET, progress=None):
    return (0, 0)


# ===========================================================================
#  Migration tu format cu (.tkz per-thang / .bin) -> .tkd per-ngay
# ===========================================================================
_OLD_MAGIC = b"TDST"
_OLD_MAGIC_Z = b"TKZ1"
_OLD_HDR = 16
_OLD_HDRZ = 32


def _read_old_month_file(path):
    """Doc 1 file thang format cu (.bin raw hoac .tkz nen) -> list[(t,bid,ask)]."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic == _OLD_MAGIC:            # .bin raw
                f.seek(_OLD_HDR); data = f.read()
            elif magic == _OLD_MAGIC_Z:        # .tkz nen
                f.seek(_OLD_HDRZ); data = lzma.decompress(f.read())
            else:
                return []
    except (OSError, lzma.LZMAError):
        return []
    return _unpack_records(data)


def symbols_needing_migration():
    """List symbol con file thang cu (.tkz/.bin) can chuyen sang .tkd."""
    out = []
    if not os.path.isdir(STORE_DIR):
        return out
    for name in sorted(os.listdir(STORE_DIR)):
        d = os.path.join(STORE_DIR, name)
        if not os.path.isdir(d):
            continue
        if any((fn.endswith(".tkz") or fn.endswith(".bin")) and len(fn) == 11
               for fn in os.listdir(d)):
            out.append(name.upper())
    return out


def migrate_all(progress=None):
    """
    Chuyen MOI symbol con format cu (.tkz/.bin per-thang) sang .tkd per-ngay.
    progress(done, total, symbol) — callback tuy chon. Tra so symbol da chuyen.
    """
    syms = symbols_needing_migration()
    for i, sym in enumerate(syms, 1):
        _migrate_old_format(sym)
        _migrated.add(sym)
        _synced.discard(sym)
        _rebuild_catalog(sym)
        if progress:
            progress(i, len(syms), sym)
    return len(syms)


def _day_version(path):
    try:
        with open(path, "rb") as f:
            head = f.read(HDR)
        if len(head) < HDR or head[:4] != MAGIC:
            return None
        return struct.unpack_from("<I", head, 4)[0]
    except OSError:
        return None


def _reencode_file(sym, path):
    """Doc 1 file .tkd v1 (LZMA) -> ghi lai v2 (raw deflate) cung cho. Tra True neu co doi."""
    try:
        with open(path, "rb") as f:
            head = f.read(HDR)
            body = f.read()
    except OSError:
        return False
    if len(head) < HDR or head[:4] != MAGIC:
        return False
    if struct.unpack_from("<I", head, 4)[0] == VER_DEFLATE:
        return False
    try:
        raw = lzma.decompress(body)
    except lzma.LZMAError:
        return False
    day_index = struct.unpack_from("<q", head, 8)[0] // MS_PER_DAY
    _write_day(sym, day_index, _unpack_records(raw))   # ghi v2, cap nhat catalog
    return True


def symbols_needing_reencode():
    """List symbol con file .tkd v1 (LZMA) can chuyen sang v2 (deflate) cho native."""
    out = []
    if not os.path.isdir(STORE_DIR):
        return out
    for name in sorted(os.listdir(STORE_DIR)):
        d = os.path.join(STORE_DIR, name)
        if os.path.isdir(d) and any(
                _day_version(p) == VER_LZMA for p in glob.glob(os.path.join(d, "*", "*.tkd"))):
            out.append(name.upper())
    return out


def reencode_all(progress=None):
    """
    Chuyen MOI file .tkd v1 (LZMA) -> v2 (raw deflate) — de native doc thang bang puff.c.
    Idempotent (file da v2 -> bo qua). progress(done, total, path). Tra so file da doi.
    """
    files = []
    if os.path.isdir(STORE_DIR):
        for name in sorted(os.listdir(STORE_DIR)):
            d = os.path.join(STORE_DIR, name)
            if os.path.isdir(d):
                for p in glob.glob(os.path.join(d, "*", "*.tkd")):
                    if _day_version(p) == VER_LZMA:
                        files.append((name.upper(), p))
    total = len(files)
    for i, (sym, path) in enumerate(files, 1):
        _reencode_file(sym, path)
        _synced.discard(sym)
        if progress:
            progress(i, total, path)
    for sym in {s for s, _ in files}:
        _rebuild_catalog(sym)
    return total


def _migrate_old_format(symbol):
    """
    Neu con file thang cu (.tkz/.bin) trong thu muc symbol -> chuyen sang .tkd per-ngay
    roi XOA file cu. Chay 1 lan/symbol.
    """
    sym = symbol.upper()
    d = store_dir(sym)
    if not os.path.isdir(d):
        return
    olds = [os.path.join(d, fn) for fn in os.listdir(d)
            if (fn.endswith(".tkz") or fn.endswith(".bin")) and len(fn) == 11]
    if not olds:
        return
    print(f"[migrate] {sym}: chuyen {len(olds)} file thang (cu) -> file ngay .tkd...")
    for path in sorted(olds):
        ticks = _read_old_month_file(path)
        if ticks:
            by_day: dict[int, list] = {}
            for t in ticks:
                by_day.setdefault(_day_index_of(t[0]), []).append(t)
            for day_index, day_ticks in by_day.items():
                day_ticks.sort(key=lambda t: t[0])
                _write_day(sym, day_index, day_ticks)
        try:
            os.remove(path)
        except OSError:
            pass
    print(f"[migrate] {sym}: xong.")


# ===========================================================================
#  Native: bung ngay -> .bin scratch cho fxt_virtual.cpp mmap (giai doan A)
# ===========================================================================
def _scratch_dir(symbol):
    return os.path.join(SCRATCH_DIR, symbol.upper())


def clean_scratch(symbol=None):
    import shutil
    target = _scratch_dir(symbol) if symbol else SCRATCH_DIR
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


def month_file(symbol, year, month):
    """Duong dan scratch .bin cua 1 thang (cho native mmap). Xem materialize_paths."""
    return os.path.join(_scratch_dir(symbol), f"{year:04d}-{month:02d}.bin")


def day_paths(symbol, from_ms, to_ms):
    """
    List path file .tkd cua cac ngay giao [from_ms, to_ms], theo THU TU thoi gian —
    cho native (fxt_virtual.cpp giai doan B) doc thang tung ngay (windowed), KHONG
    can scratch .bin. Chi tra ngay CO data.
    """
    sym = symbol.upper(); _ensure(sym)
    day_lo = from_ms // MS_PER_DAY
    day_hi = (to_ms - 1) // MS_PER_DAY if to_ms > 0 else -1
    return [_day_file_by_index(sym, day) for day in _days_in(sym, day_lo, day_hi)]


def materialize_paths(symbol, from_ms, to_ms):
    """
    Bung cac thang giao [from_ms, to_ms) ra .bin scratch (data/_materialized) cho native
    mmap. KHO .tkd giu nguyen -> backtest khong lam phinh kho, khong can nen lai.
    Tra list path .bin (theo thang) da tao.
    """
    sym = symbol.upper(); _ensure(sym)
    months = sorted({(d.year, d.month) for d in
                     (_day_to_date(day) for day in _days_in(sym, from_ms // MS_PER_DAY,
                                                            (to_ms - 1) // MS_PER_DAY if to_ms > 0 else -1))})
    paths = []
    for (y, m) in months:
        ticks = load_month(sym, y, m)
        if not ticks:
            continue
        want = len(ticks)
        p = month_file(sym, y, m)
        # tai dung neu da bung dung so record
        if os.path.exists(p) and (os.path.getsize(p) - _OLD_HDR) // REC == want:
            paths.append(p); continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        buf = bytearray()
        for t in ticks:
            buf += _RECS.pack(t[0], t[1], t[2])
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(_OLD_MAGIC)
            f.write(struct.pack("<I", 1))
            f.write(struct.pack("<Q", want))
            f.write(bytes(buf))
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
        paths.append(p)
    return paths
