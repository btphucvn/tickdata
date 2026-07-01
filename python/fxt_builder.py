"""
FXT Builder — tao file .fxt cho MT4 Strategy Tester tu tick data Dukascopy.

Format file .fxt (version 405, build 600+):
  Header: 728 bytes
  Records: 56 bytes each = barTime(i32), pad(i32), open(f64), high(f64),
           low(f64), close=bid(f64), volume(i64), tickTime(i32), flag(i32)
  flag=0: tick tong hop tu bar (MT4 tu sinh), flag=1: tick that (Dukascopy)

Reverse-engineered tu file XAUUSD240_0.fxt cua MT4 (build 1380).
"""

import struct
import os
import shutil
import datetime
import ctypes

# Windows file attributes (cho co che "actual tick file" cua MT4)
FILE_ATTRIBUTE_READONLY = 0x01
FILE_ATTRIBUTE_NORMAL   = 0x80

def _set_readonly(path, readonly=True):
    """
    Set/clear thuoc tinh READ-ONLY cua file.

    BI MAT TDS (RE tu terminal.exe, ham sub_F83C00/0xF84009): MT4 goi
    GetFileAttributesW(fxt) va kiem tra bit READ-ONLY. Neu FXT la READ-ONLY ->
    MT4 coi la "actual tick file" (tick that) -> dung real ticks -> 99.9%
    (thay vi tu sinh lai tu M1 -> 25%). Day la cach TDS dat modelling quality 99.9%.
    """
    try:
        attr = FILE_ATTRIBUTE_READONLY if readonly else FILE_ATTRIBUTE_NORMAL
        ctypes.windll.kernel32.SetFileAttributesW(ctypes.c_wchar_p(path), attr)
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Timezone shift (giong TDS): dich tick UTC -> gio server broker (GMTOffset+DST)
# TDS config: GMTOffset=2, DST=1 (US). Day la yeu to lam khop SO LENH voi TDS.
# ---------------------------------------------------------------------------
def _nth_sunday(year, month, n):
    """Epoch giay (UTC) cua Chu nhat thu n trong thang."""
    d = datetime.date(year, month, 1)
    # weekday: Mon=0..Sun=6
    first_sun = 1 + (6 - d.weekday()) % 7
    day = first_sun + (n - 1) * 7
    return int(datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc).timestamp())

def _us_dst_active(ts):
    """US DST: Chu nhat thu 2 thang 3 -> Chu nhat thu 1 thang 11."""
    y = datetime.datetime.utcfromtimestamp(ts).year
    start = _nth_sunday(y, 3, 2)    # 2nd Sun March
    end   = _nth_sunday(y, 11, 1)   # 1st Sun Nov
    return start <= ts < end

def _eu_dst_active(ts):
    """EU DST: Chu nhat cuoi thang 3 -> Chu nhat cuoi thang 10."""
    y = datetime.datetime.utcfromtimestamp(ts).year
    # Chu nhat cuoi = nth lon nhat
    import calendar
    def last_sun(mo):
        last = calendar.monthrange(y, mo)[1]
        d = datetime.date(y, mo, last)
        last_sun_day = last - ((d.weekday() - 6) % 7)
        return int(datetime.datetime(y, mo, last_sun_day, tzinfo=datetime.timezone.utc).timestamp())
    return last_sun(3) <= ts < last_sun(10)

def tz_shift_sec(ts, gmt_offset, dst):
    """Tra so giay can CONG vao tick UTC de ra gio broker. dst: 0=none,1=US,2=EU."""
    off = int(gmt_offset) * 3600
    if dst == 1 and _us_dst_active(ts):
        off += 3600
    elif dst == 2 and _eu_dst_active(ts):
        off += 3600
    return off

HEADER_SIZE  = 728
RECORD_SIZE  = 56   # struct '<iiddddqii'
RECORD_FMT   = '<iiddddqii'

from symbols_meta import resolve   # metadata thong nhat

# SYMBOL_INFO giu lai cho code cu (vd orchestrator.py) — sinh tu symbols_meta.
def _symbol_info(sym):
    m = resolve(sym)
    return (m.digits, m.point, m.contract, m.name[:3], m.profit)

class _SymbolInfoCompat:
    def get(self, sym, default=None):
        try:
            return _symbol_info(sym)
        except Exception:
            return default
SYMBOL_INFO = _SymbolInfoCompat()

# Periods MT4 ho tro (ten hien thi -> gia tri minutes)
PERIODS = {
    'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
    'H1': 60, 'H4': 240, 'D1': 1440,
}


def _pack_header(symbol, period_min, from_ts, to_ts, bars, total_ticks,
                 server_name='default', model_quality=99.9,
                 swap_long=0.0, swap_short=0.0, swap_type=0):
    """
    Dong goi header FXT v405 sao cho MT4 build 600+ CHAP NHAN (khong sinh lai).

    Cac field & dieu kien duoc RE truc tiep tu sub_F83C00 trong terminal.exe:
      [0]   version       == 405
      [68]  description    == TEN SERVER (vd 'Dukascopy-demo-1') — MT4 so khop chuoi!
      [196] symbol         == symbol dang test
      [208] period, [212] model, [216] bars (>100)
      [232] modelQuality  : DOUBLE (khong phai int!) — set 99.9 -> hien 99.9%
      [240] baseCurrency  : byte != 0
      [252] spread (<=100000), [256] digits (<=8), [264] point (double)
      [280] int  > 0        (bat buoc)
      [352] int  0..6
      [356] int  1..500     (bat buoc)
      [400] double > 0      (bat buoc)
      [408] marginCurrency : byte != 0 (bat buoc)
    Neu BAT KY field nao sai -> MT4 sinh lai FXT tu M1 -> 25%. Khop het -> dung
    FXT tick that cua ta -> 99.9%.
    """
    meta = resolve(symbol)
    sym  = meta.name.split('.')[0].split('-')[0][:12]
    digits       = meta.digits
    point        = meta.point
    contract_size= meta.contract
    base_ccy     = meta.name[:3]
    profit_mode  = meta.profit

    buf = bytearray(HEADER_SIZE)

    def pi(off, v):  struct.pack_into('<i', buf, off, int(v))
    def pd(off, v):  struct.pack_into('<d', buf, off, float(v))
    def ps(off, n, v):
        b = (v.encode('ascii', 'replace') if isinstance(v, str) else v)[:n]
        buf[off:off+len(b)] = b

    pi(0, 405)                                              # version
    ps(4,  64, 'Copyright 2000-2026, MetaQuotes Ltd.')     # copyright
    ps(68, 128, server_name)                               # description = TEN SERVER (!)
    ps(196, 12, sym)                                        # symbol
    pi(208, period_min)                                     # period (minutes)
    pi(212, 0)                                              # model = 0 every-tick
    pi(216, bars)                                           # bars (>100)
    pi(220, from_ts)                                        # fromDate
    pi(224, to_ts)                                          # toDate
    pi(228, total_ticks)                                    # totalTicks
    pd(232, model_quality)                                 # modelQuality (DOUBLE, 99.9)
    ps(240, 12, base_ccy)                                   # baseCurrency (!=0)
    pi(252, 0)                                              # spread=0 (dynamic/hook)
    pi(256, digits)                                         # digits (<=8)
    pd(264, point)                                          # point (double)

    # --- Cac field "magic" MT4 yeu cau (gia tri lay tu FXT MT4 tu sinh) ---
    pi(272, 1)                                              # lotMin-ish
    pi(276, 100000)                                         # lotMax-ish
    pi(280, 1)                                              # >0 (BAT BUOC)
    pi(284, 0)
    pi(288, 1)
    pd(296, contract_size)                                  # contract size (double)
    # *** SUA LOI SIZING (2026-07): tick value = contract * point, KHONG phai contract. ***
    #   MODE_TICKVALUE = gia tri 1 tick (=1 point) / 1 lot (tien tai khoan). Voi symbol
    #   quote=USD: = contract_size * point. Truoc day dat = contract (thieu *point) ->
    #   MODE_TICKVALUE gap 1/point lan -> EA (LotsForRisk dung tv/ts) tinh lot NHO <point>
    #   lan. BTCUSD point=0.1 -> lot nho 10x -> tds.htm vs clone.htm lech profit hoan toan.
    #   tickSize@312 giu = 0: MT4 tu tinh MODE_TICKSIZE = point (da verify: FXT cu 312=0
    #   van cho MODE_TICKSIZE=0.1). Chi can sua tickValue -> tv/ts = contract (dung).
    pd(304, contract_size * point)                          # tick value (double)
    # LUU Y: KHONG dat 320 (profit_calc_mode) -> giu 0 nhu baseline (64 lenh khop TDS).
    # --- SWAP (RE terminal.exe 0xF84CDE-D0A: MT4 copy header nguyen ven vao model,
    #     roi doc model+0x148=swap_type, +0x150=swap_long, +0x158=swap_short,
    #     +0x160=rollover3days -> header offset 328/336/344/352) ---
    # *** SUA LOI SWAP (2026-07): PHAI bat swapEnable@324. RE 0xF84CBE: neu
    #     header[324] <= 0 -> MT4 set co swap = 0 -> KHONG tinh swap (bat ke swap_long).
    #     Truoc day khong set 324 (=0) -> swap tat -> lech TDS o cac lenh LONG.
    #     Set 1 -> swap bat (giong FXT cua TDS). ***
    pi(324, 1)                                             # swapEnable = 1 (BAT swap)
    pi(328, int(swap_type))                                # swap calc mode (0=points, tds SwapType)
    pd(336, float(swap_long))                              # swap_long  (double)
    pd(344, float(swap_short))                             # swap_short (double)
    pi(352, 3)                                             # swap_rollover3days = Wed (x3 swap) [0..6]
    pi(356, 100)                                            # 1..500 (BAT BUOC)
    pi(360, 1)
    pi(368, 30)
    pd(400, 1.0)                                            # >0 (BAT BUOC)
    ps(408, 12, base_ccy)                                   # marginCurrency (!=0)
    pi(440, 1002)
    pi(448, 1002)
    pi(484, 5)

    return bytes(buf)


def detect_server_name(fallback='default'):
    """Lay ten server MT4 (= ten thu muc history/<server>). Dung cho description FXT."""
    appdata = os.environ.get('APPDATA', '')
    root = os.path.join(appdata, 'MetaQuotes', 'Terminal')
    special = {'default', 'deleted', 'downloads', 'mailbox', 'signals', 'symbolsets', 'common'}
    if os.path.isdir(root):
        for term in os.listdir(root):
            hroot = os.path.join(root, term, 'history')
            if not os.path.isdir(hroot):
                continue
            for srv in os.listdir(hroot):
                if (os.path.isdir(os.path.join(hroot, srv))
                        and srv.lower() not in special):
                    return srv
    return fallback


# Swap per-symbol (long, short, type) — POINTS. type 0 = points (tester tinh
# money = swap_points * tick_value * lots).
#
# *** SUA LOI SWAP (2026-07) ***: gia tri cu (2.09/-6.76) SAI ~150x -> swap ~0 ->
# backtest lech TDS hoan toan o cac lenh LONG. Gia tri DUNG lay tu symbols.raw cua
# broker (giong cach TDS lam) qua broker_symbols.read_symbol(). Fallback duoi day
# la so THAT cua Dukascopy (symbols.raw): BTCUSD long=-312.43 points (~31$/ngay/lot).
SWAP_DEFAULTS = {
    'BTCUSD': (-312.4267272, 0.0, 0),
    'ETHUSD': (-8.37915528,  0.0, 0),
    'XAUUSD': (-558.5118928, 338.4165978, 0),
    'EURUSD': (-6.30731,     2.86468,     0),
}


def _broker_swap(symbol):
    """Doc swap THAT tu broker symbols.raw (points). Tra (long, short) hoac None."""
    try:
        import broker_symbols
        info = broker_symbols.read_symbol(symbol)
        if info is not None:
            return float(info['swap_long']), float(info['swap_short'])
    except Exception:
        pass
    return None


def build_fxt(ticks, symbol, period_min, out_path, server_name=None,
              model_quality=99.9, gmt_offset=0, dst=0,
              swap_long=None, swap_short=None, swap_type=0):
    """
    Tao file .fxt tu danh sach tick.

    ticks: iterable cua (time_ms: int, bid: float, ask: float)
    period_min: 1, 5, 15, 60, 240, 1440
    out_path: duong dan file .fxt dau ra
    server_name: ten server (description field). Tu detect neu None — PHAI dung
                 thi MT4 moi chap nhan FXT (khong sinh lai).
    gmt_offset/dst: dich tick UTC -> gio broker (giong TDS GMTOffset/DST). dst:
                 0=none,1=US,2=EU. Lam khop SO LENH voi TDS.
    """
    if server_name is None:
        server_name = detect_server_name()
    # Swap: uu tien tham so -> broker symbols.raw (giong TDS) -> default per-symbol.
    if swap_long is None or swap_short is None:
        bs = _broker_swap(symbol)
        d  = SWAP_DEFAULTS.get(symbol.upper().split('.')[0], (0.0, 0.0, 0))
        if swap_long is None:
            swap_long = bs[0] if bs else d[0]
        if swap_short is None:
            swap_short = bs[1] if bs else d[1]
        swap_type = 0   # POINTS: tester tinh money = points * tick_value * lots
        print(f"[swap] {symbol}: long={swap_long:.4f} short={swap_short:.4f} pts "
              f"({'broker symbols.raw' if bs else 'default'})")
    point = resolve(symbol).point   # cho spread points tai offset 4
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)

    # Bo read-only cu (neu co) de ghi de duoc
    if os.path.exists(out_path):
        _set_readonly(out_path, False)

    period_sec = period_min * 60
    bar_time = 0
    bar_open = bar_high = bar_low = 0.0
    bar_vol  = 0
    num_bars = 0
    from_ts = to_ts = 0
    total_ticks = 0

    with open(out_path, 'wb') as f:
        f.write(b'\x00' * HEADER_SIZE)   # placeholder

        apply_tz = (gmt_offset != 0 or dst != 0)
        for time_ms, bid, ask in ticks:
            time_sec = int(time_ms) // 1000
            # Dich UTC -> gio broker (giong TDS) -> bar align khop TDS
            if apply_tz:
                time_sec += tz_shift_sec(time_sec, gmt_offset, dst)
            bt = (time_sec // period_sec) * period_sec

            if bt != bar_time:
                bar_time = bt
                bar_open = bid
                bar_high = bid
                bar_low  = bid
                bar_vol  = 0
                num_bars += 1

            bar_vol  += 1
            if bid > bar_high: bar_high = bid
            if bid < bar_low:  bar_low  = bid

            rec = struct.pack(RECORD_FMT,
                bar_time, 0,
                bar_open, bar_high, bar_low, bid,
                bar_vol,
                time_sec, 1)     # flag=1 = real tick
            f.write(rec)

            if from_ts == 0: from_ts = time_sec
            to_ts = time_sec
            total_ticks += 1

        # Ghi header voi thong tin chinh xac
        f.seek(0)
        f.write(_pack_header(symbol, period_min, from_ts, to_ts, num_bars,
                             total_ticks, server_name=server_name,
                             model_quality=model_quality,
                             swap_long=swap_long, swap_short=swap_short,
                             swap_type=swap_type))

    # *** BI MAT TDS: set FXT READ-ONLY -> MT4 coi la "actual tick file" -> 99.9% ***
    ro = _set_readonly(out_path, True)
    sz = os.path.getsize(out_path)
    print(f"[OK] FXT: {total_ticks:,} ticks, {num_bars:,} bars "
          f"({datetime.datetime.utcfromtimestamp(from_ts).date()} -> "
          f"{datetime.datetime.utcfromtimestamp(to_ts).date()})  "
          f"{sz/1024/1024:.1f} MB  -> {out_path}")
    print(f"[OK] Da set FXT READ-ONLY ({'thanh cong' if ro else 'LOI'}) "
          f"-> MT4 se dung real ticks (99.9%), khong sinh lai.")
    return out_path


def build_fxt_from_file(tick_bin_path, symbol, period_min, out_path):
    """Tao FXT tu file .bin da download (format cua download_ticks.py)."""
    import struct as _struct

    def _iter_ticks(path):
        with open(path, 'rb') as f:
            magic = f.read(4)
            if magic != b'TDST':
                raise ValueError(f'{path}: khong phai file TDST')
            _struct.unpack('<I', f.read(4))   # version
            count, = _struct.unpack('<Q', f.read(8))
            for _ in range(count):
                raw = f.read(24)
                if len(raw) < 24:
                    break
                time_ms, bid, ask = _struct.unpack('<qdd', raw)
                yield time_ms, bid, ask

    return build_fxt(_iter_ticks(tick_bin_path), symbol, period_min, out_path)


# ---------------------------------------------------------------------------
# Deployment — copy FXT vao thu muc MT4 tester/history
# ---------------------------------------------------------------------------

def find_mt4_tester_dirs():
    """Tim tat ca thu muc tester/history cua MT4 (co the nhieu terminal)."""
    appdata = os.environ.get('APPDATA', '')
    root = os.path.join(appdata, 'MetaQuotes', 'Terminal')
    if not os.path.isdir(root):
        return []
    dirs = []
    for entry in os.listdir(root):
        p = os.path.join(root, entry, 'tester', 'history')
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def deploy_fxt(src_fxt, symbol, period_min, tester_dir=None):
    """Copy file FXT vao thu muc MT4 tester/history."""
    if tester_dir is None:
        dirs = find_mt4_tester_dirs()
        if not dirs:
            raise RuntimeError('Khong tim thay thu muc MT4 tester/history')
        tester_dir = dirs[0]

    fname = f'{symbol.upper()}{period_min}_0.fxt'
    dst = os.path.join(tester_dir, fname)
    shutil.copy2(src_fxt, dst)
    print(f'[OK] Deploy: {dst}')
    return dst


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import argparse
    from download_ticks import load_ticks

    ap = argparse.ArgumentParser(description='FXT Builder cho MT4 Strategy Tester')
    ap.add_argument('--ticks',   required=True, help='File .bin tu download_ticks.py')
    ap.add_argument('--symbol',  required=True, help='Symbol, vi du EURUSD')
    ap.add_argument('--period',  type=int, default=1,
                    help='Period (minutes): 1, 5, 15, 60, 240... (default=1=M1)')
    ap.add_argument('--out',     default='', help='Output .fxt (mac dinh: {SYM}{P}_0.fxt)')
    ap.add_argument('--deploy',  action='store_true',
                    help='Sau khi build, tu dong copy vao thu muc MT4 tester/history')
    args = ap.parse_args()

    out = args.out or f'{args.symbol.upper()}{args.period}_0.fxt'

    print(f'[*] Doc tick: {args.ticks}')
    ticks = load_ticks(args.ticks)
    print(f'[*] {len(ticks):,} ticks -> build FXT {args.symbol} M{args.period}')

    fxt_path = build_fxt(ticks, args.symbol, args.period, out)

    if args.deploy:
        deploy_fxt(fxt_path, args.symbol, args.period)
        print('[*] MT4 da san sang: mo Strategy Tester, chon Every Tick, chon ngay trong pham vi.')
