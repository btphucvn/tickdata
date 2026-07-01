"""Tai 1 gio bi5 cua 1 symbol, thu cac divisor de tim diung point factor."""
import urllib.request, lzma, struct, sys

sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSD"
# Thu vai gio/ngay khac nhau cho den khi co data
candidates = [
    (2024, 0, 2, 12), (2023, 5, 15, 14), (2022, 0, 5, 16),
    (2021, 10, 1, 10), (2020, 5, 1, 13),
]
HDR = {"User-Agent": "Mozilla/5.0"}

raw = None
for (y, m, d, h) in candidates:
    url = f"https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
    try:
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=15) as r:
            comp = r.read()
        if comp:
            raw = lzma.decompress(comp)
            if raw:
                print(f"[OK] Co data: {url}  ({len(raw)//20} ticks)")
                break
    except Exception as e:
        print(f"  skip {url}: {e}")

if not raw:
    print(f"[X] Khong tai duoc data cho {sym} (co the sai ten symbol)")
    sys.exit(1)

# Doc tick dau
ms, ask, bid, av, bv = struct.unpack_from(">IIIff", raw, 0)
print(f"\nRaw values: ask={ask}  bid={bid}  ask_vol={av:.4f}  bid_vol={bv:.4f}")
print("Thu cac divisor:")
for div in [1, 10, 100, 1000, 10000, 100000, 1000000]:
    print(f"  /{div:>8} -> bid={bid/div:>14.5f}  ask={ask/div:>14.5f}")
