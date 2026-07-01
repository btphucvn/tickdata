"""
Dung lai M1 + H4 FXT KHOP TDS ve khoang ngay.

TDS bar dau = 2018.01.02 00:00 (gio broker) = 2018-01-01 22:00 UTC (GMT+2, thang 1
khong DST). Clone truoc day bat dau 2017-12-25 -> du ~1 tuan warmup -> vol-target
lech -> lot khac -> profit lech (M1 -452 vs TDS -476; H4 -387 vs -476).

Fix: build ca M1 lan H4 FXT bat dau dung 2018-01-01 22:00 UTC (first broker bar
= 2018.01.02 00:00, giong TDS). fxt_builder tu set tickvalue+swapEnable+swap+read-only.

KHONG sua fxt_builder (logic da dung) — chi chinh KHOANG NGAY.
"""
import os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tick_store, fxt_builder, settings_store

SYM = 'BTCUSD'
PERIODS = [1, 240]   # M1 va H4

def ms_utc(y, mo, d, h=0):
    return int(datetime.datetime(y, mo, d, h, tzinfo=datetime.timezone.utc).timestamp() * 1000)

def main():
    s = settings_store.load(SYM)
    gmt, dst = int(s.gmt_offset), int(s.dst)
    # First broker bar 2018.01.02 00:00 => UTC 2018-01-01 22:00 (GMT+2, no DST in Jan)
    from_ms = ms_utc(2018, 1, 1, 22)
    to_ms   = ms_utc(2019, 6, 1)
    print(f'[*] tz GMT{gmt:+d} DST={dst} | range UTC 2018-01-01 22:00 .. 2019-06-01 '
          f'(first broker bar = 2018.01.02 00:00, khop TDS)')
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for period in PERIODS:
        out = os.path.join(proj, 'data', 'fxt', f'{SYM}{period}_0.fxt')
        print(f'\n[*] Build {SYM} period={period} ...')
        t0 = time.time()
        ticks = tick_store.iter_range(SYM, from_ms, to_ms)
        fxt_builder.build_fxt(ticks, SYM, period, out, gmt_offset=gmt, dst=dst)
        print(f'[OK] build {period} trong {time.time()-t0:.0f}s')

        for tdir in fxt_builder.find_mt4_tester_dirs():
            dst_path = os.path.join(tdir, f'{SYM}{period}_0.fxt')
            try: fxt_builder._set_readonly(dst_path, False)
            except Exception: pass
            regen = dst_path + '.regen'
            if os.path.exists(regen):
                try: fxt_builder._set_readonly(regen, False); os.remove(regen)
                except Exception: pass
            fxt_builder.deploy_fxt(out, SYM, period, tdir)
            fxt_builder._set_readonly(dst_path, True)
            print(f'[OK] deploy + read-only: {dst_path}')

if __name__ == '__main__':
    main()
