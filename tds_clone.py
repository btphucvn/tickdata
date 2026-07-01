"""
TDS Clone — all-in-one CLI

Dung phap:
  python tds_clone.py download --symbol EURUSD --from 2024-01-01 --to 2024-01-31
  python tds_clone.py build    --symbol EURUSD --from 2024-01-01 --to 2024-01-31
  python tds_clone.py run      --symbol EURUSD --from 2024-01-01 --to 2024-01-31

Lenh 'run' = download + build + deploy + bat spread service (neu can).
"""

import argparse
import datetime
import os
import sys

# Them thu muc python vao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))


DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'ticks')
FXT_DIR  = os.path.join(os.path.dirname(__file__), 'data', 'fxt')


def tick_bin_path(symbol, date_from, date_to):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f'{symbol}_{date_from}_{date_to}.bin')


def fxt_path(symbol, period):
    os.makedirs(FXT_DIR, exist_ok=True)
    return os.path.join(FXT_DIR, f'{symbol}{period}_0.fxt')


def _range_ms(date_from, date_to):
    """Doi 'YYYY-MM-DD' -> (from_ms, to_ms) UTC, to bao gom het ngay cuoi."""
    d_from = datetime.datetime.fromisoformat(date_from).replace(tzinfo=datetime.timezone.utc)
    d_to   = datetime.datetime.fromisoformat(date_to).replace(tzinfo=datetime.timezone.utc)
    d_to  += datetime.timedelta(days=1)   # bao gom het ngay cuoi
    return int(d_from.timestamp() * 1000), int(d_to.timestamp() * 1000)


def cmd_download(args):
    """
    Tai tick THEO TUNG THANG: luu sau moi thang (resume duoc), nghi giua cac thang
    de tranh rate-limit. Thang da co trong store se bo qua (--force de tai lai).
    Store: data/ticks/{SYMBOL}.bin
    """
    import time as _t
    import tick_store
    from download_ticks import (download_months, RateLimitedError,
                                _iter_months)

    sym = args.symbol.upper()
    d_from = datetime.date.fromisoformat(args.date_from)
    d_to   = datetime.date.fromisoformat(args.date_to)
    force  = getattr(args, 'force', False)

    existing = set() if force else set(tick_store.month_counts(sym).keys())
    source = getattr(args, 'source', 'auto')

    def save_month(y, m, ticks):
        if ticks:
            tick_store.save_month(sym, y, m, ticks)   # ghi file thang rieng

    months = _iter_months(d_from, d_to)
    print(f'[*] Tai {sym}: {len(months)} thang ({args.date_from}..{args.date_to}) '
          f'— server={source} — moi thang 1 file')
    try:
        for idx, (y, m, lo, hi) in enumerate(months):
            if (y, m) in existing:
                print(f'[skip] {y}-{m:02d}: da co')
                continue
            download_months(sym, lo, hi, save_month_cb=save_month,
                            source=source, max_workers=getattr(args, 'workers', 16),
                            pause_sec=0)
            if idx < len(months) - 1:
                _t.sleep(2.0)
    except RateLimitedError as e:
        print(f'\n[!] DUNG: {e}')
        print('    Cac thang xong da luu. Chay lai de tiep tuc.')
        return sym

    cov = tick_store.coverage(sym)
    print(f'[OK] Store {sym}: {cov[2] if cov else 0:,} ticks')
    return sym


def cmd_build(args):
    """
    Build FXT cho MT4 Strategy Tester.

    QUAN TRONG: chi build CHINH KHOANG TEST [from, to] (+ warm-up tuy chon), KHONG
    build toan bo store. Build toan bo store nhieu nam crypto -> FXT hang GB, copy
    that bai (het o dia) -> MT4 doc file rong -> loi "no history data".

    Build THANG vao thu muc MT4 tester/history (khong copy file lon).
    """
    import tick_store
    from fxt_builder import build_fxt, find_mt4_tester_dirs

    sym  = args.symbol.upper()

    cov = tick_store.coverage(sym)
    if not cov:
        print(f'[!] Chua co data {sym}. Chay: python tds_clone.py download --symbol {sym} ...')
        sys.exit(1)

    # Kiem tra khoang test nam trong store
    from_ms, to_ms = _range_ms(args.date_from, args.date_to)
    if cov[1] < from_ms or cov[0] > to_ms:
        print(f'[!] Store {sym} khong phu {args.date_from}..{args.date_to}')
        print(f'    Store: {datetime.datetime.utcfromtimestamp(cov[0]/1000).date()} '
              f'-> {datetime.datetime.utcfromtimestamp(cov[1]/1000).date()}')
        sys.exit(1)

    # Warm-up: lui ngay bat dau them N ngay (cho indicator co bar truoc test).
    warmup_days = int(getattr(args, 'warmup', 7) or 0)
    build_from_ms = max(cov[0], from_ms - warmup_days * 86400 * 1000)

    # Xac dinh duong dan dau ra: build THANG vao MT4 (khoi copy file lon)
    no_deploy = getattr(args, 'no_deploy', False)
    out = None
    if not no_deploy:
        dirs = find_mt4_tester_dirs()
        if dirs:
            fname = f'{sym}{args.period}_0.fxt'
            out = os.path.join(dirs[0], fname)
            # Xoa FXT cu/rong de tranh MT4 doc nham (bo read-only truoc khi xoa)
            if os.path.exists(out):
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(ctypes.c_wchar_p(out), 0x80)
                    os.remove(out)
                except OSError: pass
        else:
            print('[!] Khong tim thay thu muc MT4 tester/history -> build vao data/fxt')
    if out is None:
        out = fxt_path(sym, args.period)

    # Canh bao dung luong o dia (FXT tick-level rat lon)
    try:
        import shutil as _sh
        free_gb = _sh.disk_usage(os.path.dirname(out)).free / 1024**3
        if free_gb < 3:
            print(f'[!] CANH BAO: o dia chi con {free_gb:.1f}GB trong. '
                  f'FXT co the lon — neu het cho, build se loi.')
    except Exception:
        pass

    d_from = datetime.datetime.utcfromtimestamp(build_from_ms / 1000).date()
    d_to   = datetime.datetime.utcfromtimestamp(to_ms / 1000).date()

    # --- LOP 1: Build HST TRUOC (de MT4 luon co history -> KHONG "no history data").
    # Build HST truoc FXT -> FXT moi hon HST -> MT4 uu tien dung FXT tick-accurate cua ta.
    if not no_deploy:
        try:
            from hst_builder import build_hst, find_mt4_history_dirs, deploy_hst
            hdirs = find_mt4_history_dirs()
            if hdirs:
                server_dir = hdirs[0][1]
                hst_out = os.path.join(server_dir, f'{sym}{args.period}.hst')
                print(f'[*] Build HST M{args.period} {d_from} -> {d_to} -> {hst_out}')
                build_hst(tick_store.iter_range(sym, build_from_ms, to_ms),
                          sym, args.period, hst_out)
            else:
                print('[!] Khong tim thay history/<server> -> bo qua HST '
                      '(MT4 co the bao "no history data").')
        except (PermissionError, OSError) as e:
            print(f'[!] Khong ghi duoc HST (MT4 dang khoa?): {e}')
        except Exception as e:
            print(f'[!] Loi build HST: {e}')

    # --- LOP 2: Build FXT tick-accurate (MT4 dung lam du lieu test).
    print(f'[*] Build FXT M{args.period} khoang {d_from} -> {d_to} '
          f'(warm-up {warmup_days} ngay) -> {out}')
    # Doc timezone/DST tu settings (giong TDS GMTOffset/DST) -> bar align khop TDS
    gmt_offset, dst = 0, 0
    try:
        import settings_store
        st = settings_store.load(sym)
        gmt_offset, dst = int(st.gmt_offset), int(st.dst)
        if gmt_offset or dst:
            print(f'[*] Timezone: GMT+{gmt_offset} DST={dst} (giong TDS) -> dich tick sang gio broker')
    except Exception as e:
        print(f'[!] Khong doc duoc timezone settings: {e}')

    try:
        build_fxt(tick_store.iter_range(sym, build_from_ms, to_ms),
                  sym, args.period, out, gmt_offset=gmt_offset, dst=dst)
    except (PermissionError, OSError) as e:
        print(f'[!!!] Khong ghi duoc FXT: {e}')
        print('      Co the MT4 dang CHAY backtest (khoa file). '
              'Hay BAM STOP trong Strategy Tester roi thu lai.')
        sys.exit(1)

    # Verify FXT khong rong
    sz = os.path.getsize(out) if os.path.exists(out) else 0
    if sz < 1024:
        print(f'[!!!] FXT rong/loi ({sz} byte) — co the het o dia. Backtest se bao '
              f'"no history data". Hay giai phong o dia hoac chon khoang ngay ngan hon.')
        sys.exit(1)

    # --- VARIABLE SPREAD: ghi file spread that cho hook (giong TDS) ---
    if not no_deploy:
        try:
            from shm_writer import write_spread_file
            spread_path = os.path.join(os.path.dirname(__file__), 'data', 'active.spread')
            ss = write_spread_file(sym, spread_path, from_ms=build_from_ms, to_ms=to_ms)
            print(f'[OK] Spread file: {ss["count"]:,} records, avg {ss["avg"]:.0f}pts '
                  f'-> {spread_path}')
            print(f'     (Inject + tick "Use my tick data" -> hook ap variable spread that)')
        except Exception as e:
            print(f'[!] Khong ghi duoc spread file: {e}')

    if not no_deploy:
        print(f'[OK] Da build FXT+HST thang vao MT4: {out}  ({sz/1024/1024:.1f} MB)')
        print(f'     Tester "Use date" {args.date_from}..{args.date_to} -> Start.')

    return out


def cmd_run(args):
    """Download (gop store) + build FXT + deploy. All-in-one."""
    cmd_download(args)
    cmd_build(args)

    deployed = not getattr(args, 'no_deploy', False)
    print()
    print('=' * 50)
    print(' TDS CLONE - SAN SANG')
    print('=' * 50)
    if deployed:
        print(f' FXT: da deploy vao MT4 tester/history')
    print(' Tick data: THAT (Dukascopy)')
    print()
    print(' Trong MT4 Strategy Tester:')
    print(f'   Symbol={args.symbol}  Period=M{args.period}  Model=Every Tick')
    print(f'   Date: {args.date_from} -> {args.date_to}  -> Start')
    print()


def cmd_restore(args):
    """
    Cho checkbox 'Use my tick data' khi BO TICK: go FXT/HST cua ta khoi MT4
    de MT4 quay ve dung data cua broker (modelling quality binh thuong).
    Phai bo read-only truoc khi xoa.
    """
    import ctypes
    from fxt_builder import find_mt4_tester_dirs
    from hst_builder import find_mt4_history_dirs
    sym = args.symbol.upper()
    removed = []
    # Xoa FXT (bo read-only truoc)
    for d in find_mt4_tester_dirs():
        p = os.path.join(d, f'{sym}{args.period}_0.fxt')
        if os.path.exists(p):
            try:
                ctypes.windll.kernel32.SetFileAttributesW(ctypes.c_wchar_p(p), 0x80)
                os.remove(p); removed.append(p)
            except OSError as e:
                print(f'[!] Khong xoa duoc {p}: {e}')
        # don dummy .regen neu co
        rp = p + '.regen'
        if os.path.exists(rp):
            try: os.remove(rp)
            except OSError: pass
    # Xoa HST cua ta (de MT4 tu dong bo lai tu broker)
    for _term, srv in find_mt4_history_dirs():
        p = os.path.join(srv, f'{sym}{args.period}.hst')
        if os.path.exists(p):
            try:
                ctypes.windll.kernel32.SetFileAttributesW(ctypes.c_wchar_p(p), 0x80)
                os.remove(p); removed.append(p)
            except OSError:
                pass
    if removed:
        print(f'[OK] Da go tick data cua ta cho {sym}. MT4 se dung data broker.')
        for r in removed:
            print(f'     - {r}')
    else:
        print(f'[*] Khong co FXT/HST cua ta cho {sym} de go.')


def cmd_prepare(args):
    """
    Cho checkbox 'Use my tick data' goi: build+deploy FXT tu store da tai.
    Khong tu download (data phai tai truoc qua settings). Im lang, in gon.
    """
    import tick_store
    sym = args.symbol.upper()
    cov = tick_store.coverage(sym)
    if not cov:
        print(f'[X] Chua tai data {sym}!')
        print(f'    Bam "Tick data settings" -> Download truoc.')
        sys.exit(2)

    # Kiem tra range co nam trong store khong
    from_ms, to_ms = _range_ms(args.date_from, args.date_to)
    if cov[1] < from_ms or cov[0] > to_ms:
        print(f'[X] Store {sym} KHONG phu khoang {args.date_from}..{args.date_to}')
        print(f'    Store co: {datetime.datetime.utcfromtimestamp(cov[0]/1000).date()} '
              f'-> {datetime.datetime.utcfromtimestamp(cov[1]/1000).date()}')
        print(f'    Bam "Tick data settings" -> Download khoang nay truoc.')
        sys.exit(2)

    cmd_build(args)

    # Bao cho service biet symbol + KHOANG TEST -> service publish SHM dung range
    # (quan trong: store BTC hang chuc trieu tick -> chi build khoang test moi nhanh)
    try:
        import settings_store
        from_ms, to_ms = _range_ms(args.date_from, args.date_to)
        settings_store.set_state('active_symbol', sym)
        settings_store.set_state('active_mode', 'spread')
        settings_store.set_state('active_from_ms', from_ms)
        settings_store.set_state('active_to_ms', to_ms)
        print(f'[*] Da dat active={sym} khoang {args.date_from}..{args.date_to} cho service. '
              f'(Bat service de co variable spread/slippage trong suot.)')
    except Exception as e:
        print(f'[!] Khong set duoc active_symbol: {e}')

    print(f'[OK] San sang! Tick "Use my tick data" dang bat. Nhan Start.')


def cmd_list(args):
    """Liet ke cac symbol + ngay da tai."""
    import tick_store
    rows = tick_store.list_symbols()
    if not rows:
        print('(Chua tai data symbol nao)')
        return
    print(f'{"SYMBOL":<12}{"TU NGAY":<13}{"DEN NGAY":<13}{"TICKS":>14}  {"DUNG LUONG":>10}')
    print('-' * 66)
    total_mb = 0
    for r in rows:
        if r['count']:
            f = datetime.datetime.utcfromtimestamp(r['first_ms']/1000).strftime('%Y-%m-%d')
            t = datetime.datetime.utcfromtimestamp(r['last_ms']/1000).strftime('%Y-%m-%d')
        else:
            f = t = '-'
        print(f'{r["symbol"]:<12}{f:<13}{t:<13}{r["count"]:>14,}  {r["size_mb"]:>8.1f}MB')
        total_mb += r['size_mb']
    print('-' * 66)
    print(f'{len(rows)} symbol  |  tong {total_mb:.1f} MB')


def cmd_delete(args):
    """Xoa data da tai cua 1 symbol."""
    import tick_store
    sym = args.symbol.upper()
    cov = tick_store.coverage(sym)
    if tick_store.delete_symbol(sym):
        n = cov[2] if cov else 0
        print(f'[OK] Da xoa data {sym} ({n:,} ticks).')
    else:
        print(f'[!] Khong co data {sym} de xoa.')


def cmd_months(args):
    """In luoi cac thang da tai cho 1 symbol (hoac tat ca)."""
    import tick_store
    syms = ([args.symbol.upper()] if getattr(args, 'symbol', None)
            else [r['symbol'] for r in tick_store.list_symbols()])
    if not syms:
        print('(Chua tai data symbol nao)')
        return

    MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    for sym in syms:
        counts = tick_store.month_counts(sym)
        if not counts:
            print(f'\n{sym}: (chua co data)')
            continue
        years = sorted(set(y for (y, m) in counts))
        total = sum(counts.values())
        print(f'\n=== {sym} — da tai {total:,} ticks ===')
        print('Year  ' + ' '.join(f'{m:>5}' for m in MON))
        for y in years:
            cells = []
            for mo in range(1, 13):
                c = counts.get((y, mo), 0)
                if c == 0:
                    cells.append('    .')
                elif c >= 1000:
                    cells.append(f'{c//1000:>4}k')
                else:
                    cells.append(f'{c:>5}')
            print(f'{y}  ' + ' '.join(cells))
        # Liet ke gon thang da co
        done = sorted(counts.keys())
        print('Thang co data: ' + ', '.join(f'{y}-{m:02d}' for (y, m) in done))


def cmd_verify(args):
    """Kiem tra do phu tick cua 1 symbol trong khoang -> uoc luong model quality."""
    import tick_store
    from coverage_check import analyze_coverage, estimate_model_quality
    sym = args.symbol.upper()
    cov0 = tick_store.coverage(sym)
    if not cov0:
        print(f'[!] Chua co data {sym}')
        return
    from_ms, to_ms = _range_ms(args.date_from, args.date_to)
    ticks = tick_store.load_range(sym, from_ms, to_ms)
    d_from = datetime.date.fromisoformat(args.date_from)
    d_to   = datetime.date.fromisoformat(args.date_to)
    cov = analyze_coverage(ticks, d_from, d_to)
    print(f'Symbol:    {sym}')
    print(f'Khoang:    {args.date_from} -> {args.date_to}')
    print(f'Ticks:     {cov["ticks"]:,}')
    print(f'Gio g.dich:{cov["hours_with_data"]}/{cov["trading_hours"]} '
          f'({cov["coverage_pct"]:.2f}%)')
    print(f'Model Q:   {estimate_model_quality(cov["coverage_pct"])}')
    if cov['missing_hours']:
        print(f'Gio trong: {len(cov["missing_hours"])} (phan lon nghi le)')
        for (y,m,d,h) in cov['missing_hours'][:10]:
            print(f'   {y}-{m:02d}-{d:02d} {h:02d}h')
        if len(cov['missing_hours']) > 10:
            print(f'   ... va {len(cov["missing_hours"])-10} gio nua')


def main():
    ap = argparse.ArgumentParser(description='TDS Clone - Dukascopy tick data suite')
    ap.add_argument('--version', action='version', version='TDS Clone 1.0')

    sub = ap.add_subparsers(dest='cmd', required=True)

    # --- download ---
    dl = sub.add_parser('download', help='Tai tick data tu Dukascopy')
    dl.add_argument('--symbol',  required=True)
    dl.add_argument('--from',    dest='date_from', required=True)
    dl.add_argument('--to',      dest='date_to',   required=True)
    dl.add_argument('--force',   action='store_true', help='Tai lai du da co')
    dl.add_argument('--source',  default='auto', choices=['auto', 'freeserv', 'datafeed'],
                    help='Server: auto (mac dinh), freeserv (it bi chan), datafeed (bi5)')
    dl.add_argument('--workers', type=int, default=16, help='So ngay tai song song (mac dinh 16)')

    # --- build ---
    bd = sub.add_parser('build', help='Build file .fxt cho MT4')
    bd.add_argument('--symbol',    required=True)
    bd.add_argument('--from',      dest='date_from', required=True)
    bd.add_argument('--to',        dest='date_to',   required=True)
    bd.add_argument('--period',    type=int, default=1, help='Period minutes (default=1)')
    bd.add_argument('--no-deploy', action='store_true', help='Khong copy vao MT4')

    # --- run (all-in-one) ---
    rn = sub.add_parser('run', help='Download + build + deploy (all-in-one)')
    rn.add_argument('--symbol',  required=True)
    rn.add_argument('--from',    dest='date_from', required=True)
    rn.add_argument('--to',      dest='date_to',   required=True)
    rn.add_argument('--period',  type=int, default=1)
    rn.add_argument('--point',   type=float, default=None,
                    help='Pip size (tu dong detect neu bo trong)')
    rn.add_argument('--force',   action='store_true')
    rn.add_argument('--no-deploy', action='store_true')

    # --- prepare (cho checkbox trong MT4 goi) ---
    pr = sub.add_parser('prepare', help='Build+deploy FXT tu store (cho checkbox)')
    pr.add_argument('--symbol',  required=True)
    pr.add_argument('--from',    dest='date_from', required=True)
    pr.add_argument('--to',      dest='date_to',   required=True)
    pr.add_argument('--period',  type=int, default=1)

    # --- restore (cho checkbox khi BO TICK: go FXT/HST cua ta) ---
    rs = sub.add_parser('restore', help='Go FXT/HST cua ta -> MT4 dung data broker')
    rs.add_argument('--symbol', required=True)
    rs.add_argument('--period', type=int, default=1)

    # --- list ---
    sub.add_parser('list', help='Liet ke symbol + ngay da tai')

    # --- months ---
    mo = sub.add_parser('months', help='In luoi thang da tai (cho 1 symbol hoac tat ca)')
    mo.add_argument('--symbol', default=None, help='Symbol (bo trong = tat ca)')

    # --- delete ---
    de = sub.add_parser('delete', help='Xoa data da tai cua 1 symbol')
    de.add_argument('--symbol', required=True)

    # --- verify ---
    ve = sub.add_parser('verify', help='Kiem tra do phu -> uoc luong model quality')
    ve.add_argument('--symbol',  required=True)
    ve.add_argument('--from',    dest='date_from', required=True)
    ve.add_argument('--to',      dest='date_to',   required=True)

    # --- info ---
    inf = sub.add_parser('info', help='Xem thong tin file tick/fxt')
    inf.add_argument('file', help='Duong dan file .bin hoac .fxt')

    args = ap.parse_args()

    if args.cmd == 'download':
        cmd_download(args)
    elif args.cmd == 'build':
        cmd_build(args)
    elif args.cmd == 'run':
        cmd_run(args)
    elif args.cmd == 'prepare':
        cmd_prepare(args)
    elif args.cmd == 'restore':
        cmd_restore(args)
    elif args.cmd == 'list':
        cmd_list(args)
    elif args.cmd == 'months':
        cmd_months(args)
    elif args.cmd == 'delete':
        cmd_delete(args)
    elif args.cmd == 'verify':
        cmd_verify(args)
    elif args.cmd == 'info':
        show_info(args.file)


def show_info(path):
    import struct
    ext = os.path.splitext(path)[1].lower()
    if ext == '.bin':
        with open(path, 'rb') as f:
            magic = f.read(4)
            if magic != b'TDST':
                print(f'[ERR] Khong phai file TDST bin')
                return
            version, = struct.unpack('<I', f.read(4))
            count,   = struct.unpack('<Q', f.read(8))
            t0, b0, a0 = struct.unpack('<qdd', f.read(24))
            f.seek(-24, 2)
            t1, b1, a1 = struct.unpack('<qdd', f.read(24))
        print(f'File:   {path}')
        print(f'Format: TDST v{version}')
        print(f'Ticks:  {count:,}')
        print(f'First:  {datetime.datetime.utcfromtimestamp(t0/1000)} UTC  bid={b0:.5f}')
        print(f'Last:   {datetime.datetime.utcfromtimestamp(t1/1000)} UTC  bid={b1:.5f}')
    elif ext == '.fxt':
        with open(path, 'rb') as f:
            raw = f.read(728)
        ver  = struct.unpack_from('<i', raw, 0)[0]
        sym  = raw[196:208].decode('ascii', errors='replace').rstrip('\x00')
        per  = struct.unpack_from('<i', raw, 208)[0]
        bars = struct.unpack_from('<i', raw, 216)[0]
        f_ts = struct.unpack_from('<i', raw, 220)[0]
        t_ts = struct.unpack_from('<i', raw, 224)[0]
        ntk  = struct.unpack_from('<i', raw, 228)[0]
        digs = struct.unpack_from('<i', raw, 256)[0]
        pnt  = struct.unpack_from('<d', raw, 264)[0]
        csz  = struct.unpack_from('<d', raw, 296)[0]
        fsize = os.path.getsize(path)
        nrec = (fsize - 728) // 56
        print(f'File:     {path}')
        print(f'Version:  {ver}')
        print(f'Symbol:   {sym}  Period=M{per}  Digits={digs}')
        print(f'Bars:     {bars:,}  Ticks={ntk:,}  Records={nrec:,}')
        print(f'From:     {datetime.datetime.utcfromtimestamp(f_ts)}')
        print(f'To:       {datetime.datetime.utcfromtimestamp(t_ts)}')
        print(f'Point:    {pnt}  LotSize={csz}')
        print(f'Size:     {fsize/1024/1024:.1f} MB')
    else:
        print(f'[!] Phai la .bin hoac .fxt')


if __name__ == '__main__':
    main()
