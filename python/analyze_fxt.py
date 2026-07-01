import struct, datetime, sys
from collections import Counter

fxt = sys.argv[1] if len(sys.argv) > 1 else (
    r'C:\Users\Phuc\AppData\Roaming\MetaQuotes\Terminal'
    r'\ADB94438A57B9692806EA638441107AB\tester\history\XAUUSD240_0.fxt')
REC = 56
f = open(fxt, 'rb')
f.seek(728)

def rd(n):
    out = []
    for _ in range(n):
        b = f.read(REC)
        if len(b) < REC:
            break
        out.append(struct.unpack('<iiddddqii', b))
    return out

def fmt(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

recs = rd(30)
print('=== 30 RECORD DAU ===')
prev = None
for i, (bt, pad, o, h, l, c, v, tt, flag) in enumerate(recs):
    mark = '<- bar moi' if bt != prev else ''
    print(f'[{i:2}] bar={fmt(bt)[:16]} tick={fmt(tt)[5:]} '
          f'O={o:.3f} H={h:.3f} L={l:.3f} C={c:.3f} v={v} flag={flag} {mark}')
    prev = bt

f.seek(728)
recs2 = rd(20000)
flags = Counter(r[8] for r in recs2)
print(f'\nFlag distribution (20000 rec): {dict(flags)}')
bars = Counter(r[0] for r in recs2)
print('Ticks/bar (vai bar dau):')
for bt, cnt in list(bars.items())[:8]:
    print(f'  {fmt(bt)[:16]}: {cnt} ticks')
