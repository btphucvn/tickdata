"""
Dung H4 FXT (BTCUSD240_0.fxt) DUNG cach — khop M1 FXT (da khop TDS).

Van de: clone chi deploy M1 FXT. Khi test H4, MT4 khong co H4 FXT hop le -> tu
sinh lai (guard redirect nhung file bi truncate 0 byte) -> H4 bar LECH GRID
(UTC 22:01 thay vi broker 00:00) -> tin hieu khac -> 77 lenh thay vi 67.

TDS: H4 == M1 y het (-476.46) vi H4 FXT full-tick, align broker giong M1.

Fix: build H4 FXT full-tick, cung tz (GMT+2/DST US) + range nhu M1 -> H4 bar
align broker -> khop TDS. build_fxt tu set swapEnable + tick value + swap broker
+ read-only (bi mat 99.9%).
"""
import os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tick_store, fxt_builder, settings_store

SYM = 'BTCUSD'
PERIOD = 240   # H4

def ms(y, mo, d):
    return int(datetime.datetime(y, mo, d, tzinfo=datetime.timezone.utc).timestamp() * 1000)

def main():
    s = settings_store.load(SYM)
    gmt, dst = int(s.gmt_offset), int(s.dst)
    print(f'[*] tz: GMT{gmt:+d} DST={dst} (giong M1 FXT da khop TDS)')

    # Range = khop M1 FXT: 2017-12-25 .. 2019-06-01 (UTC). Bao warmup vol-target.
    from_ms, to_ms = ms(2017, 12, 25), ms(2019, 6, 1)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(proj, 'data', 'fxt', f'{SYM}{PERIOD}_0.fxt')

    print(f'[*] Build H4 FXT {SYM} range {datetime.date(2017,12,25)}..{datetime.date(2019,6,1)} ...')
    t0 = time.time()
    ticks = tick_store.iter_range(SYM, from_ms, to_ms)
    fxt_builder.build_fxt(ticks, SYM, PERIOD, out, gmt_offset=gmt, dst=dst)
    print(f'[OK] build xong trong {time.time()-t0:.0f}s')

    # Deploy vao MT4 tester/history (xoa read-only cua file cu/0-byte truoc)
    for tdir in fxt_builder.find_mt4_tester_dirs():
        dst_path = os.path.join(tdir, f'{SYM}{PERIOD}_0.fxt')
        try:
            fxt_builder._set_readonly(dst_path, False)
        except Exception:
            pass
        # xoa file .regen 0-byte neu co (tranh MT4 doc nham)
        regen = dst_path + '.regen'
        if os.path.exists(regen):
            try: fxt_builder._set_readonly(regen, False); os.remove(regen)
            except Exception: pass
        fxt_builder.deploy_fxt(out, SYM, PERIOD, tdir)
        fxt_builder._set_readonly(dst_path, True)   # read-only lai -> MT4 dung real ticks
        print(f'[OK] deploy + read-only: {dst_path}')

if __name__ == '__main__':
    main()
