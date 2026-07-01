"""
Doc thong so symbol THAT tu broker (MT4 symbols.raw) — giong cach TDS lam.

TDS khong tu bia swap/contract; no dung dung thong so broker cap cho symbol. Clone
truoc day HARDCODE swap sai (BTCUSD LongSwap=2.09) -> backtest lech TDS hoan toan
o cac lenh LONG (swap qua nho ~0). Broker that: BTCUSD swap_long=-312.4 (points).

symbols.raw (MT4 build 600+): mang record 1936 byte. Offset (verify build 1473 Dukascopy):
  name        @0     (12s)
  swap_type   @1668  (int)   ; 0=points... (gia tri broker=2 nhung tester tinh theo points)
  swap_long   @1680  (double, points)
  swap_short  @1688  (double, points)
  rollover3d  @1696  (int)   ; ngay x3 swap (3=Wed)
  point       @1776  (double)

Neu offset sai o build khac -> ham tu VERIFY (name khop + point khop meta) va tra None.
"""

import os
import struct

REC_SIZE   = 1936
OFF_NAME   = 0
OFF_SWTYPE = 1668
OFF_SWLONG = 1680
OFF_SWSHORT= 1688
OFF_ROLL3  = 1696
OFF_POINT  = 1776


def find_symbols_raw():
    """Tra list duong dan symbols.raw cua cac terminal MT4 (uu tien server that)."""
    appdata = os.environ.get('APPDATA', '')
    root = os.path.join(appdata, 'MetaQuotes', 'Terminal')
    out = []
    if not os.path.isdir(root):
        return out
    special = {'default', 'deleted', 'downloads', 'mailbox', 'signals',
               'symbolsets', 'common'}
    for term in os.listdir(root):
        hroot = os.path.join(root, term, 'history')
        if not os.path.isdir(hroot):
            continue
        for srv in os.listdir(hroot):
            if srv.lower() in special:
                continue
            p = os.path.join(hroot, srv, 'symbols.raw')
            if os.path.isfile(p):
                out.append(p)
    # them ca default cuoi cung (fallback)
    for term in os.listdir(root):
        p = os.path.join(root, term, 'history', 'default', 'symbols.raw')
        if os.path.isfile(p):
            out.append(p)
    return out


def read_symbol(symbol, raw_path=None):
    """
    Doc thong so broker cho `symbol`. Tra dict hoac None neu khong tim thay/parse loi.
      { swap_long, swap_short, swap_type, rollover3day, point }
    swap_long/short: POINTS (dung cho FXT swap_type=0).
    """
    base = symbol.upper().split('.')[0].split('-')[0]
    paths = [raw_path] if raw_path else find_symbols_raw()
    for p in paths:
        try:
            b = open(p, 'rb').read()
        except Exception:
            continue
        if len(b) < REC_SIZE or len(b) % REC_SIZE != 0:
            continue
        n = len(b) // REC_SIZE
        for i in range(n):
            rec = b[i * REC_SIZE:(i + 1) * REC_SIZE]
            name = rec[:12].split(b'\x00')[0].decode('latin-1', 'ignore').upper()
            if name.split('.')[0] != base:
                continue
            point = struct.unpack_from('<d', rec, OFF_POINT)[0]
            # sanity: point phai la 10^-k trong khoang hop ly -> xac nhan layout dung
            if not (1e-8 < point < 1.0 + 1e-9):
                return None
            return dict(
                symbol=name,
                swap_long =struct.unpack_from('<d', rec, OFF_SWLONG)[0],
                swap_short=struct.unpack_from('<d', rec, OFF_SWSHORT)[0],
                swap_type =struct.unpack_from('<i', rec, OFF_SWTYPE)[0],
                rollover3day=struct.unpack_from('<i', rec, OFF_ROLL3)[0],
                point=point,
                source=p,
            )
    return None


if __name__ == '__main__':
    import sys
    for s in (sys.argv[1:] or ['BTCUSD', 'EURUSD', 'XAUUSD', 'ETHUSD']):
        info = read_symbol(s)
        print(f'{s:10s} -> {info}')
