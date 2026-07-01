"""
Va tai cho (in-place) tick value / tick size trong header FXT da build.

Ly do: cac FXT cu duoc build voi loi tickValue@304 = contract (thieu *point),
lam MODE_TICKVALUE gap 1/point lan -> EA tinh lot nho <point> lan (BTCUSD: 10x).
Xem fix goc trong fxt_builder.py (_pack_header). Script nay sua CAC FILE DA CO
ma khong can rebuild lai toan bo (chi ghi de 16 byte header @304 va @312).

Gia tri dung (symbol quote=USD):
  tickValue@304 = contract_size * point
  tickSize @312 = 0  (MT4 tu tinh MODE_TICKSIZE = point; da verify)
=> money_per_price_per_lot = tv/ts = contract_size (dung).

Giu nguyen thuoc tinh READ-ONLY (bi mat 99.9%): xoa RO de ghi, roi set lai.

Dung:
  python patch_fxt_tickvalue.py <file1.fxt> [file2.fxt ...]
  python patch_fxt_tickvalue.py            # tu tim FXT trong data/fxt + MT4 tester/history
"""

import os
import sys
import struct
import ctypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FILE_ATTRIBUTE_READONLY = 0x01
FILE_ATTRIBUTE_NORMAL   = 0x80

OFF_SYMBOL   = 196
OFF_DIGITS   = 256
OFF_POINT    = 264
OFF_CONTRACT = 296
OFF_TICKVAL  = 304
OFF_TICKSIZE = 312
OFF_SWENABLE = 324   # swap enable (int, PHAI = 1 de MT4 tinh swap)
OFF_SWTYPE   = 328   # swap calc mode (0 = points)
OFF_SWLONG   = 336   # swap long  (double, points)
OFF_SWSHORT  = 344   # swap short (double, points)


def _is_readonly(path):
    attr = ctypes.windll.kernel32.GetFileAttributesW(ctypes.c_wchar_p(path))
    return attr != -1 and bool(attr & FILE_ATTRIBUTE_READONLY)


def _set_readonly(path, ro):
    ctypes.windll.kernel32.SetFileAttributesW(
        ctypes.c_wchar_p(path),
        FILE_ATTRIBUTE_READONLY if ro else FILE_ATTRIBUTE_NORMAL)


def patch(path):
    with open(path, 'rb') as f:
        head = f.read(728)
    if len(head) < 728:
        print(f"[SKIP] {path}: khong phai FXT hop le (header < 728B)")
        return False

    symbol   = head[OFF_SYMBOL:OFF_SYMBOL+12].split(b'\x00')[0].decode('latin-1', 'ignore')
    digits   = struct.unpack_from('<i', head, OFF_DIGITS)[0]
    point    = struct.unpack_from('<d', head, OFF_POINT)[0]
    contract = struct.unpack_from('<d', head, OFF_CONTRACT)[0]
    old_tv   = struct.unpack_from('<d', head, OFF_TICKVAL)[0]
    old_ts   = struct.unpack_from('<d', head, OFF_TICKSIZE)[0]
    old_sl   = struct.unpack_from('<d', head, OFF_SWLONG)[0]
    old_ss   = struct.unpack_from('<d', head, OFF_SWSHORT)[0]
    old_sen  = struct.unpack_from('<i', head, OFF_SWENABLE)[0]

    if point <= 0:
        # fallback tu digits neu point rong
        point = 10.0 ** (-digits) if digits > 0 else 0.0
    if point <= 0 or contract <= 0:
        print(f"[SKIP] {path}: point/contract khong hop le (point={point} contract={contract})")
        return False

    new_tv = contract * point
    new_ts = 0.0   # giu 0 -> MT4 tu tinh MODE_TICKSIZE = point (bam baseline da chay dung)

    # --- Swap THAT tu broker symbols.raw (giong TDS) ---
    new_sl, new_ss = old_sl, old_ss
    try:
        import broker_symbols
        info = broker_symbols.read_symbol(symbol)
        if info is not None:
            new_sl = float(info['swap_long'])
            new_ss = float(info['swap_short'])
    except Exception as ex:
        print(f"[warn] {symbol}: khong doc duoc broker swap ({ex})")

    need_tv = abs(old_tv - new_tv) > 1e-12 or abs(old_ts - new_ts) > 1e-12
    need_sw = abs(old_sl - new_sl) > 1e-9 or abs(old_ss - new_ss) > 1e-9
    need_en = old_sen != 1   # PHAI bat swapEnable de MT4 tinh swap
    if not need_tv and not need_sw and not need_en:
        print(f"[OK ] {os.path.basename(path)} ({symbol}): da dung "
              f"(tv={old_tv} swapEn={old_sen} swapL={old_sl:.3f}) — bo qua")
        return False

    was_ro = _is_readonly(path)
    if was_ro:
        _set_readonly(path, False)
    try:
        with open(path, 'r+b') as f:
            f.seek(OFF_TICKVAL);  f.write(struct.pack('<d', new_tv))
            f.seek(OFF_TICKSIZE); f.write(struct.pack('<d', new_ts))
            f.seek(OFF_SWENABLE); f.write(struct.pack('<i', 1))       # BAT swap
            f.seek(OFF_SWTYPE);   f.write(struct.pack('<i', 0))       # points
            f.seek(OFF_SWLONG);   f.write(struct.pack('<d', new_sl))
            f.seek(OFF_SWSHORT);  f.write(struct.pack('<d', new_ss))
    finally:
        if was_ro:
            _set_readonly(path, True)   # giu lai bi mat READ-ONLY 99.9%

    eff_new_ts = new_ts if new_ts > 0 else point
    print(f"[FIX] {os.path.basename(path)} ({symbol}): "
          f"tickValue {old_tv:g}->{new_tv:g} (tv/ts={new_tv/eff_new_ts:.4g}=contract) | "
          f"swapEnable {old_sen}->1 | swapLong {old_sl:.3f}->{new_sl:.3f} "
          f"swapShort {old_ss:.3f}->{new_ss:.3f} pts | RO {'giu' if was_ro else 'khong'}")
    return True


def _auto_find():
    paths = []
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    fxt_dir = os.path.join(proj, 'data', 'fxt')
    if os.path.isdir(fxt_dir):
        paths += [os.path.join(fxt_dir, f) for f in os.listdir(fxt_dir)
                  if f.lower().endswith('.fxt')]
    appdata = os.environ.get('APPDATA', '')
    root = os.path.join(appdata, 'MetaQuotes', 'Terminal')
    if os.path.isdir(root):
        for term in os.listdir(root):
            th = os.path.join(root, term, 'tester', 'history')
            if os.path.isdir(th):
                paths += [os.path.join(th, f) for f in os.listdir(th)
                          if f.lower().endswith('.fxt')]
    return paths


if __name__ == '__main__':
    files = sys.argv[1:] or _auto_find()
    if not files:
        print("Khong tim thay file FXT nao. Truyen duong dan lam tham so.")
        sys.exit(1)
    print(f"[*] Kiem tra {len(files)} file FXT ...")
    n = 0
    for p in files:
        try:
            if patch(p):
                n += 1
        except Exception as e:
            print(f"[ERR] {p}: {e}")
    print(f"[DONE] Da va {n} file.")
