"""
Tick store theo symbol — LUU MOI THANG 1 FILE (scalable cho data nhieu nam).

Cau truc:
  data/ticks/{SYMBOL}/{YYYY-MM}.bin     # thang NONG (raw, chua nen)
  data/ticks/{SYMBOL}/{YYYY-MM}.tkz     # thang LANH (da nen LZMA, luu tru)

MOI THANG chi ton tai 1 trong 2 dang (khong bao gio ca hai o trang thai on dinh):
  * .bin (raw)  = header 'TDST'(4)+u32 ver+u64 count = 16B, roi count x record 24B:
                  int64 time_ms, double bid, double ask (da sort).  <-- native mmap,
                  numpy frombuffer va append_day deu doc dang nay.
  * .tkz (nen)  = header 'TKZ1'(4)+u32 ver+u64 count+i64 first_ms+i64 last_ms = 32B,
                  roi LZMA(record 24B y het .bin). Bit-exact, chi nho hon ~5 lan.

Vi sao KHONG nen thang .bin dang chay:
  native (fxt_virtual.cpp) MMAP THANG .bin doc record tai offset co dinh -> phai
  raw. Nen la de LUU TRU thang nguoi khong dung; khi backtest/publish thi
  `materialize_month` bung .tkz -> .bin cho native. Day dung cach TDS lam:
  archive nen, bung ra working-set khi chay.

Vi sao KHONG dung 1 file khong lo + merge:
  merge phai nap toan bo file vao RAM -> 8 nam crypto = hang GB. Luu moi thang 1
  file: tai thang nao ghi thang do, build FXT thi STREAM qua tung thang -> RAM thap,
  resume tu nhien.
"""

import os
import struct
import lzma
import datetime

MAGIC = b"TDST"      # thang raw (.bin)
MAGIC_Z = b"TKZ1"    # thang nen (.tkz)
REC = 24
HDR = 16             # header .bin
HDRZ = 32            # header .tkz (MAGIC+ver+count+first_ms+last_ms)

# Muc nen LZMA cho archive. preset 1 = nhanh, ~5x nho hon. Cao hon = nho hon nhung
# cham hon (compress). Data tick nen rat tot nen 1 da du.
COMPRESS_PRESET = 1

STORE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ticks")

# Vung TAM cho backtest: thang nen duoc bung ra DAY (khong phai trong store) de native
# mmap. Store .tkz KHONG BAO GIO bi dong khi backtest -> KHONG can nen lai sau backtest.
# Tu don khi mo GUI. Day dung flow TDS: kho nen bat bien, working-set bung ra tam thoi.
SCRATCH_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "_materialized")


# ---------------------------------------------------------------------------
# Duong dan
# ---------------------------------------------------------------------------
def store_dir(symbol):
    return os.path.join(STORE_DIR, symbol.upper())

def month_file(symbol, year, month):
    """Duong dan thang dang RAW (.bin) — dang native/numpy doc. Luon tra .bin."""
    return os.path.join(store_dir(symbol), f"{year:04d}-{month:02d}.bin")

def month_file_z(symbol, year, month):
    """Duong dan thang dang NEN (.tkz)."""
    return os.path.join(store_dir(symbol), f"{year:04d}-{month:02d}.tkz")

def _old_single_file(symbol):
    return os.path.join(STORE_DIR, f"{symbol.upper()}.bin")


def _variant(symbol, year, month):
    """
    Tra ('raw', path.bin) neu thang o dang raw; ('z', path.tkz) neu dang nen; else
    (None, None). Neu ca hai ton tai (crash giua compress/materialize) -> uu tien
    RAW (la ban authoritative khi co mat).
    """
    raw = month_file(symbol, year, month)
    if os.path.exists(raw):
        return "raw", raw
    z = month_file_z(symbol, year, month)
    if os.path.exists(z):
        return "z", z
    return None, None


# ---------------------------------------------------------------------------
# Doc bytes record + metadata (dung chung, trong suot voi raw/nen)
# ---------------------------------------------------------------------------
def _records_bytes(symbol, year, month):
    """Raw 24-byte record bytes cua 1 thang (tu bung .tkz neu can). b'' neu rong."""
    kind, path = _variant(symbol, year, month)
    if kind is None:
        return b""
    try:
        with open(path, "rb") as f:
            if kind == "raw":
                if f.read(4) != MAGIC:
                    return b""
                f.seek(HDR)
                return f.read()
            # nen
            if f.read(4) != MAGIC_Z:
                return b""
            f.seek(HDRZ)
            comp = f.read()
    except OSError:
        return b""
    try:
        return lzma.decompress(comp)
    except lzma.LZMAError:
        return b""


def _month_meta(symbol, year, month):
    """
    (count, first_ms, last_ms) — RAM/IO ~0 (khong bung ca thang):
      * raw: count tu kich thuoc file + doc 2 record (dau/cuoi).
      * nen: doc thang tu header 32B.
    Tra (0, None, None) neu rong/loi.
    """
    kind, path = _variant(symbol, year, month)
    if kind is None:
        return (0, None, None)
    if kind == "z":
        try:
            with open(path, "rb") as f:
                head = f.read(HDRZ)
            if len(head) < HDRZ or head[:4] != MAGIC_Z:
                return (0, None, None)
            count, first_ms, last_ms = struct.unpack_from("<Qqq", head, 8)
            if count == 0:
                return (0, None, None)
            return (count, first_ms, last_ms)
        except OSError:
            return (0, None, None)
    # raw
    n = _count_in_file(path)
    if n <= 0:
        return (0, None, None)
    try:
        with open(path, "rb") as f:
            f.seek(HDR)
            first = struct.unpack("<qdd", f.read(REC))[0]
            f.seek(HDR + (n - 1) * REC)
            last = struct.unpack("<qdd", f.read(REC))[0]
        return (n, first, last)
    except (OSError, struct.error):
        return (n, None, None)


# ---------------------------------------------------------------------------
# Migration: tach file cu {SYMBOL}.bin -> cac file thang
# ---------------------------------------------------------------------------
def _migrate_if_needed(symbol):
    old = _old_single_file(symbol)
    if not os.path.exists(old):
        return
    print(f"[migrate] Tach {symbol}.bin (cu) -> file theo thang...")
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
    """Ghi thang dang RAW (.bin) ATOMIC. Neu thang dang o dang .tkz -> xoa .tkz cu."""
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
    # .bin la authoritative -> bo .tkz cu (neu con) de giu "1 dang/thang".
    z = month_file_z(symbol, year, month)
    if os.path.exists(z):
        try:
            os.remove(z)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Nen / bung 1 thang (archive) — GIU DU LIEU BIT-EXACT
# ---------------------------------------------------------------------------
def is_compressed(symbol, year, month):
    return _variant(symbol, year, month)[0] == "z"


def compress_month(symbol, year, month, preset=COMPRESS_PRESET):
    """
    Nen thang .bin -> .tkz (LZMA cua record 24B), roi xoa .bin. ATOMIC.
    Neu thang da nen / khong co -> no-op. Tra (raw_bytes, comp_bytes) da/se dung.
    """
    kind, _ = _variant(symbol, year, month)
    if kind != "raw":
        # da nen hoac khong co -> khong lam gi
        z = month_file_z(symbol, year, month)
        return (0, os.path.getsize(z)) if os.path.exists(z) else (0, 0)

    records = _records_bytes(symbol, year, month)   # raw 24B (tu .bin)
    n = len(records) // REC
    if n == 0:
        # thang rong -> chi xoa file raw
        try:
            os.remove(month_file(symbol, year, month))
        except OSError:
            pass
        return (0, 0)

    first_ms = struct.unpack_from("<q", records, 0)[0]
    last_ms = struct.unpack_from("<q", records, (n - 1) * REC)[0]
    comp = lzma.compress(records, preset=preset)

    zpath = month_file_z(symbol, year, month)
    tmp = zpath + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC_Z)
        f.write(struct.pack("<I", 1))                 # version
        f.write(struct.pack("<Qqq", n, first_ms, last_ms))
        f.write(comp)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, zpath)
    # Xoa .bin sau khi .tkz da ghi ben vung.
    try:
        os.remove(month_file(symbol, year, month))
    except OSError:
        pass
    return (len(records), HDRZ + len(comp))


def materialize_month(symbol, year, month):
    """
    Bung thang .tkz -> .bin (raw) cho native mmap / append. Neu da la .bin -> no-op.
    Xoa .tkz sau khi .bin ghi xong (giu "1 dang/thang"). Tra path .bin (hoac None).
    """
    kind, _ = _variant(symbol, year, month)
    if kind == "raw":
        return month_file(symbol, year, month)
    if kind is None:
        return None
    records = _records_bytes(symbol, year, month)     # bung tu .tkz
    n = len(records) // REC
    _write_month_raw(symbol, year, month, n, records)  # tu xoa .tkz (giong _write)
    # _write_month_raw xoa .tkz roi -> dam bao:
    return month_file(symbol, year, month)


def compress_symbol(symbol, preset=COMPRESS_PRESET, progress=None):
    """
    Nen TOAN BO thang raw cua 1 symbol -> tiet kiem dia. Tra (before_bytes, after_bytes).
    progress(done, total, y, m) — callback tuy chon.
    """
    _migrate_if_needed(symbol)
    months = [(y, m) for (y, m) in _month_list(symbol)
              if _variant(symbol, y, m)[0] == "raw"]
    before = after = 0
    total = len(months)
    for i, (y, m) in enumerate(months, 1):
        try:
            before += os.path.getsize(month_file(symbol, y, m))
        except OSError:
            pass
        compress_month(symbol, y, m, preset=preset)
        z = month_file_z(symbol, y, m)
        try:
            after += os.path.getsize(z)
        except OSError:
            pass
        if progress:
            progress(i, total, y, m)
    return before, after


# ---------------------------------------------------------------------------
# VUNG TAM (scratch) cho backtest — bung .tkz ra day, KHONG dong store
# ---------------------------------------------------------------------------
def _scratch_dir(symbol):
    return os.path.join(SCRATCH_DIR, symbol.upper())

def scratch_month_file(symbol, year, month):
    return os.path.join(_scratch_dir(symbol), f"{year:04d}-{month:02d}.bin")


def clean_scratch(symbol=None):
    """Xoa vung tam (tat ca, hoac 1 symbol). Best-effort (bo qua file dang bi MT4 khoa)."""
    import shutil
    target = _scratch_dir(symbol) if symbol else SCRATCH_DIR
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


def _ensure_readable_bin(symbol, year, month):
    """
    Tra path .bin DOC DUOC (raw) cho 1 thang, de native mmap:
      * store dang raw  -> tra thang trong store luon (da la .bin).
      * store dang nen  -> BUNG ra vung tam (data/_materialized), KHONG dong store .tkz.
        Tai dung file tam neu da bung dung so record (khoi bung lai).
    Tra None neu thang khong co.
    """
    kind, path = _variant(symbol, year, month)
    if kind == "raw":
        return path
    if kind is None:
        return None
    # nen -> vung tam
    sp = scratch_month_file(symbol, year, month)
    want = _month_meta(symbol, year, month)[0]
    if os.path.exists(sp) and _count_in_file(sp) == want:
        return sp                                   # da bung san, tai dung
    records = _records_bytes(symbol, year, month)   # bung tu .tkz (RAM)
    os.makedirs(_scratch_dir(symbol), exist_ok=True)
    tmp = sp + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<Q", len(records) // REC))
        f.write(records)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, sp)
    return sp


def materialize_paths(symbol, from_ms, to_ms):
    """
    Tra list path .bin DOC DUOC cho cac thang giao [from_ms, to_ms) — cho native mmap.
    Thang nen duoc bung ra VUNG TAM; store .tkz GIU NGUYEN (backtest khong lam phinh
    store -> khong can nen lai). Path co the o store (thang raw) hoac vung tam (thang nen).
    """
    _migrate_if_needed(symbol)
    out = []
    for (y, m) in _month_list(symbol):
        m0 = int(datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        m1 = int(datetime.datetime(ny, nm, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        if m1 <= from_ms or m0 >= to_ms:
            continue
        p = _ensure_readable_bin(symbol, y, m)
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Ghi / doc 1 thang
# ---------------------------------------------------------------------------
def save_month(symbol, year, month, ticks):
    """
    Ghi data 1 thang (GOP voi data thang da co, sort + dedupe). Atomic. Ghi dang RAW.
    ticks: list[(time_ms, bid, ask)].
    """
    if not ticks:
        return _month_meta(symbol, year, month)[0]
    by_time = {t[0]: t for t in load_month(symbol, year, month)}
    for t in ticks:
        by_time[t[0]] = t
    items = sorted(by_time.values(), key=lambda t: t[0])
    buf = bytearray()
    for t in items:
        buf += struct.pack("<qdd", t[0], t[1], t[2])
    _write_month_raw(symbol, year, month, len(items), bytes(buf))
    return len(items)


def month_last_ms(symbol, year, month):
    """Timestamp (ms) cua tick CUOI (raw hay nen deu O(1)). None neu rong."""
    return _month_meta(symbol, year, month)[2]


def append_day(symbol, year, month, ticks):
    """
    APPEND ticks vao CUOI thang .bin — RAM thap, KHONG doc lai ca file, KHONG dedupe.

    Neu thang dang o dang NEN (.tkz) -> tu bung ve .bin truoc (materialize) roi append
    (append can raw). Gia dinh: tick MOI HON toan bo data hien co (caller loc t[0] >
    month_last_ms). Chong crash: cat file ve boi so REC truoc khi ghi.

    ticks: list[(time_ms, bid, ask)] da sort tang. Tra tong record sau khi append.
    """
    path = month_file(symbol, year, month)
    if not ticks:
        return _month_meta(symbol, year, month)[0]

    # Thang dang nen -> bung ve raw truoc khi append.
    if _variant(symbol, year, month)[0] == "z":
        materialize_month(symbol, year, month)

    os.makedirs(store_dir(symbol), exist_ok=True)

    buf = bytearray()
    for t in ticks:
        buf += struct.pack("<qdd", t[0], t[1], t[2])

    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(MAGIC)
            f.write(struct.pack("<I", 1))
            f.write(struct.pack("<Q", 0))

    size = os.path.getsize(path)
    aligned = HDR + max(0, (size - HDR) // REC) * REC
    with open(path, "r+b") as f:
        if aligned != size:
            f.truncate(aligned)
        f.seek(0, os.SEEK_END)
        f.write(buf)
        f.flush(); os.fsync(f.fileno())
        total = (f.tell() - HDR) // REC
        f.seek(8)
        f.write(struct.pack("<Q", total))
        f.flush(); os.fsync(f.fileno())
    return total


def has_month(symbol, year, month):
    return _variant(symbol, year, month)[0] is not None


def load_month(symbol, year, month):
    """Doc 1 thang -> list[(time_ms,bid,ask)] (tu bung .tkz neu can)."""
    data = _records_bytes(symbol, year, month)
    out = []
    for off in range(0, len(data) - REC + 1, REC):
        out.append(struct.unpack_from("<qdd", data, off))
    return out


# ---------------------------------------------------------------------------
# Liet ke / thong ke
# ---------------------------------------------------------------------------
def _month_list(symbol):
    """List (year, month) co data (raw HOAC nen), sap xep tang dan, khong trung."""
    d = store_dir(symbol)
    if not os.path.isdir(d):
        return []
    out = set()
    for fn in os.listdir(d):
        if len(fn) == 11 and (fn.endswith(".bin") or fn.endswith(".tkz")):
            try:
                out.add((int(fn[:4]), int(fn[5:7])))
            except ValueError:
                pass
    return sorted(out)


def _count_in_file(path):
    """So record theo KICH THUOC file .bin raw (khong dung cho .tkz)."""
    try:
        return max(0, (os.path.getsize(path) - HDR) // REC)
    except OSError:
        return 0


def month_counts(symbol):
    """Tra dict {(year, month): so_tick} — O(1)/thang (khong bung .tkz)."""
    _migrate_if_needed(symbol)
    counts = {}
    for (y, m) in _month_list(symbol):
        counts[(y, m)] = _month_meta(symbol, y, m)[0]
    return counts


def coverage(symbol):
    """Tra (first_ms, last_ms, total_count) hoac None neu rong."""
    _migrate_if_needed(symbol)
    months = _month_list(symbol)
    if not months:
        return None
    total = 0
    first = last = None
    for (y, m) in months:
        cnt, fms, lms = _month_meta(symbol, y, m)
        if cnt == 0:
            continue
        total += cnt
        if first is None and fms is not None:
            first = fms
        if lms is not None:
            last = lms
    if total == 0 or first is None or last is None:
        return None
    return first, last, total


def list_symbols():
    """Liet ke symbol da tai. Tra list dict (co ca dung luong raw+nen)."""
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
        n_comp = 0
        for (y, m) in _month_list(sym):
            kind, path = _variant(sym, y, m)
            if kind == "z":
                n_comp += 1
            try:
                size += os.path.getsize(path)
            except OSError:
                pass
        cov = coverage(sym)
        base = dict(symbol=sym, size_mb=size / 1024 / 1024, path=d, compressed=n_comp)
        if cov:
            base.update(first_ms=cov[0], last_ms=cov[1], count=cov[2])
            out.append(base)
        elif size or os.path.isdir(d):
            base.update(first_ms=0, last_ms=0, count=0)
            out.append(base)
    return out


def delete_symbol(symbol):
    """Xoa toan bo data 1 symbol (ca .bin lan .tkz)."""
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
        kind, path = _variant(symbol, y, m)
        if kind == "raw":
            # STREAM theo lo -> RAM thap.
            try:
                with open(path, "rb") as f:
                    if f.read(4) != MAGIC:
                        continue
                    f.seek(HDR)
                    while True:
                        data = f.read(REC * 4096)
                        if not data:
                            break
                        for off in range(0, len(data) - REC + 1, REC):
                            yield struct.unpack_from("<qdd", data, off)
            except OSError:
                continue
        elif kind == "z":
            # Nen: bung ca thang (1 thang ~ vai tram MB) roi yield.
            data = _records_bytes(symbol, y, m)
            for off in range(0, len(data) - REC + 1, REC):
                yield struct.unpack_from("<qdd", data, off)


def iter_range(symbol, from_ms, to_ms):
    """Generator: yield tick trong [from_ms, to_ms]."""
    for t in iter_all(symbol):
        if t[0] < from_ms:
            continue
        if t[0] > to_ms:
            break
        yield t


def load_range_np(symbol, from_ms, to_ms):
    """
    Doc NHANH tick trong [from_ms, to_ms) -> numpy structured array (t,bid,ask).
    Chi doc thang giao khoang (raw doc thang, nen tu bung). Tra None neu rong.
    """
    import numpy as np
    _migrate_if_needed(symbol)
    dt = np.dtype([('t', '<i8'), ('bid', '<f8'), ('ask', '<f8')])
    assert dt.itemsize == REC
    arrs = []
    for (y, m) in _month_list(symbol):
        m0 = int(datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        m1 = int(datetime.datetime(ny, nm, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        if m1 <= from_ms or m0 >= to_ms:
            continue
        data = _records_bytes(symbol, y, m)
        n = len(data) // REC
        if n:
            arrs.append(np.frombuffer(data, dtype=dt, count=n))
    if not arrs:
        return None
    a = arrs[0] if len(arrs) == 1 else np.concatenate(arrs)
    mask = (a['t'] >= from_ms) & (a['t'] < to_ms)
    return a[mask]


def load_range(symbol, from_ms, to_ms):
    """List tick trong [from_ms, to_ms] (cho range vua phai)."""
    return list(iter_range(symbol, from_ms, to_ms))


def load_all(symbol):
    """List toan bo (canh bao: ton RAM neu data lon — uu tien iter_all)."""
    return list(iter_all(symbol))


# Tuong thich nguoc: ham cu store_path(symbol) -> tra thu muc symbol
def store_path(symbol):
    return store_dir(symbol)
