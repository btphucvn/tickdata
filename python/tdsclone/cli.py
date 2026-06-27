"""
Module 6 (headless) — CLI orchestrator.

Cho phép chạy toàn bộ pipeline mà KHÔNG cần GUI/PySide6 — tiện để test trên WSL
hoặc tự động hoá. Sau ``pip install -e .`` có lệnh ``tdsclone``; hoặc chạy trực
tiếp: ``python -m tdsclone.cli ...``.

Lệnh con:
    tdsclone download EURUSD 2024-01-02 2024-01-03
    tdsclone coverage EURUSD
    tdsclone build    EURUSD --period 1 --from 2024-01-02 --to 2024-01-03 --spread real
    tdsclone inspect-fxt out/EURUSD1_0.fxt
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from tdsclone import __version__
from tdsclone.pipeline import build_fxt, download_range, make_spread_model
from tdsclone.store.tickstore import TickStore


def _parse_date(s: str) -> datetime:
    """Nhận 'YYYY-MM-DD' hoặc 'YYYY-MM-DD HH:MM' (UTC)."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Ngày không hợp lệ: {s!r}")


def _progress_bar(done: int, total: int) -> None:
    pct = done * 100 // max(total, 1)
    bar = "#" * (pct // 4)
    sys.stderr.write(f"\r  [{bar:<25}] {pct:3d}% ({done}/{total})")
    sys.stderr.flush()
    if done >= total:
        sys.stderr.write("\n")


# =============================================================================
#  Handlers
# =============================================================================

def cmd_download(args) -> int:
    store = TickStore(args.data)
    print(f"Tải {args.symbol} {args.start:%Y-%m-%d} .. {args.end:%Y-%m-%d} ...")
    report = download_range(args.symbol, args.start, args.end, store,
                            cache_dir=args.cache, max_workers=args.workers,
                            progress=_progress_bar)
    print(report.summary())
    store.close()
    return 0


def cmd_coverage(args) -> int:
    store = TickStore(args.data)
    ranges = store.coverage(args.symbol)
    if not ranges:
        print(f"Chưa có dữ liệu cho {args.symbol}.")
    else:
        print(f"Coverage {args.symbol}:")
        for r in ranges:
            print(f"  {r}")
    store.close()
    return 0


def cmd_build(args) -> int:
    store = TickStore(args.data)
    model = make_spread_model(args.spread, **_spread_params(args))
    print(f"Build FXT {args.symbol} M{args.period} spread={args.spread} ...")
    result = build_fxt(args.symbol, args.period, args.start, args.end, store,
                       spread_model=model, out_dir=args.out,
                       also_hst=not args.no_hst,
                       also_spread_file=not args.no_spread_file)
    print(f"  FXT   : {result.fxt_path}  ({result.n_ticks:,} tick)")
    if result.hst_path:
        print(f"  HST   : {result.hst_path}")
    if result.spread_path:
        print(f"  Spread: {result.spread_path}")
    store.close()
    return 0


def cmd_inspect_fxt(args) -> int:
    from tdsclone.convert.fxt import dump_header, read_fxt_ticks

    hdr = dump_header(args.path)
    print("== FXT header ==")
    for k, v in hdr.items():
        print(f"  {k:14s}: {v}")
    ticks = read_fxt_ticks(args.path, limit=3)
    print("== 3 tick đầu (barTime, o, h, l, c, vol, tickTime, flag) ==")
    for t in ticks:
        print(f"  {t}")
    return 0


def _spread_params(args) -> dict:
    """Trích tham số spread tương ứng từ args."""
    if args.spread == "fixed":
        return {"points": args.points}
    if args.spread == "real":
        return {"min_points": args.min_points, "multiplier": args.multiplier}
    if args.spread == "random":
        return {"min_points": args.min_points, "max_points": args.points}
    return {}


# =============================================================================
#  Argparse
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tdsclone",
        description="TDS-Clone — download tick, build FXT/HST cho MT4.",
    )
    p.add_argument("--version", action="version", version=f"tdsclone {__version__}")
    p.add_argument("--data", default="data", help="thư mục Tick Store (mặc định: data)")
    p.add_argument("-v", "--verbose", action="store_true", help="log chi tiết")
    sub = p.add_subparsers(dest="command", required=True)

    # download
    d = sub.add_parser("download", help="tải tick Dukascopy")
    d.add_argument("symbol")
    d.add_argument("start", type=_parse_date)
    d.add_argument("end", type=_parse_date)
    d.add_argument("--cache", default="raw", help="thư mục cache .bi5")
    d.add_argument("--workers", type=int, default=8)
    d.set_defaults(func=cmd_download)

    # coverage
    c = sub.add_parser("coverage", help="xem khoảng đã có dữ liệu")
    c.add_argument("symbol")
    c.set_defaults(func=cmd_coverage)

    # build
    b = sub.add_parser("build", help="build .fxt (+.hst,.tdspread)")
    b.add_argument("symbol")
    b.add_argument("--period", type=int, default=1, help="timeframe phút")
    b.add_argument("--from", dest="start", type=_parse_date, required=True)
    b.add_argument("--to", dest="end", type=_parse_date, required=True)
    b.add_argument("--out", default="out", help="thư mục xuất")
    b.add_argument("--spread", default="real",
                   choices=["real", "fixed", "random", "session", "news"])
    b.add_argument("--points", type=float, default=12.0, help="spread fixed/max points")
    b.add_argument("--min-points", dest="min_points", type=float, default=0.0)
    b.add_argument("--multiplier", type=float, default=1.0)
    b.add_argument("--no-hst", action="store_true")
    b.add_argument("--no-spread-file", action="store_true")
    b.set_defaults(func=cmd_build)

    # inspect-fxt
    i = sub.add_parser("inspect-fxt", help="dump header + tick đầu của .fxt")
    i.add_argument("path", type=Path)
    i.set_defaults(func=cmd_inspect_fxt)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
