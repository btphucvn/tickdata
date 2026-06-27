"""
Module 2 — Tick Store (mục 2.3 / 2.4 kiến trúc).

Mục tiêu: lưu tick đã chuẩn hoá hiệu quả, query nhanh theo khoảng thời gian, và
biết được vùng dữ liệu đã có / còn thiếu (coverage & gaps).

LƯU TRỮ
-------
* Mỗi cặp ``(symbol, YYYY-MM)`` = 1 file dữ liệu (đúng tinh thần "mỗi symbol-tháng
  1 file Parquet" ở mục 2.3).
* Định dạng file:
    - Mặc định ``.tickbin`` — định dạng columnar nhị phân tự định nghĩa, CHỈ dùng
      standard library => chạy ngay không cần cài gì. (Xem :func:`_write_tickbin`.)
    - Nếu cài ``pyarrow`` và bật ``prefer_parquet=True`` => ghi ``.parquet`` chuẩn,
      đọc được bằng pandas/polars/DuckDB.
* Manifest **SQLite** (``manifest.db``) lập chỉ mục: symbol, khoảng thời gian,
  đường dẫn file, số tick, trạng thái, checksum — đúng mục 2.3.

API (mục 2.4): :meth:`ingest`, :meth:`query`, :meth:`coverage`, :meth:`gaps`.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
import zlib
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tdsclone.model import TickFrame, US_PER_SEC

logger = logging.getLogger(__name__)

# ---- Định dạng .tickbin -----------------------------------------------------
# Layout:  magic(8) | version(u32) | n(u32) | 5 khối cột nén-zlib có độ dài tiền tố
#   Khối cột: comp_len(u32) | raw_len(u32) | zlib(bytes của array)
# Các cột: ts(int64 'q'), bid/ask/bid_vol/ask_vol (float64 'd').
_TICKBIN_MAGIC = b"TDSTICK1"
_TICKBIN_VER = 1


def _write_tickbin(path: Path, tf: TickFrame) -> None:
    """Ghi TickFrame ra file .tickbin (columnar, nén zlib từng cột)."""

    def block(arr: array) -> bytes:
        raw = arr.tobytes()
        comp = zlib.compress(raw, level=6)
        return struct.pack("<II", len(comp), len(raw)) + comp

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_TICKBIN_MAGIC)
        fh.write(struct.pack("<II", _TICKBIN_VER, len(tf)))
        fh.write(block(tf.ts))
        fh.write(block(tf.bid))
        fh.write(block(tf.ask))
        fh.write(block(tf.bid_vol))
        fh.write(block(tf.ask_vol))


def _read_tickbin(path: Path) -> TickFrame:
    """Đọc file .tickbin -> TickFrame."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != _TICKBIN_MAGIC:
        raise ValueError(f"Không phải file .tickbin hợp lệ: {path}")
    off = 8
    ver, n = struct.unpack_from("<II", data, off)
    off += 8
    if ver != _TICKBIN_VER:
        raise ValueError(f"Phiên bản .tickbin không hỗ trợ: {ver}")

    def read_block(typecode: str) -> array:
        nonlocal off
        comp_len, raw_len = struct.unpack_from("<II", data, off)
        off += 8
        raw = zlib.decompress(data[off:off + comp_len])
        off += comp_len
        arr = array(typecode)
        arr.frombytes(raw)
        return arr

    tf = TickFrame()
    tf.ts = read_block("q")
    tf.bid = read_block("d")
    tf.ask = read_block("d")
    tf.bid_vol = read_block("d")
    tf.ask_vol = read_block("d")
    return tf


# ---- Parquet (tuỳ chọn, chỉ khi có pyarrow) --------------------------------

def _have_pyarrow() -> bool:
    import importlib.util
    return importlib.util.find_spec("pyarrow") is not None


def _write_parquet(path: Path, tf: TickFrame) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = tf.to_columns()
    table = pa.table(cols)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")


def _read_parquet(path: Path) -> TickFrame:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    d = table.to_pydict()
    tf = TickFrame()
    tf.ts = array("q", d["timestamp_utc"])
    tf.bid = array("d", d["bid"])
    tf.ask = array("d", d["ask"])
    tf.bid_vol = array("d", d["bid_volume"])
    tf.ask_vol = array("d", d["ask_volume"])
    return tf


# =============================================================================
#  Coverage
# =============================================================================

@dataclass
class CoverageRange:
    """Một khoảng thời gian đã có dữ liệu (UTC)."""

    start: datetime
    end: datetime
    ticks: int = 0

    def __repr__(self) -> str:  # cho dễ đọc trong GUI/log
        return (f"{self.start:%Y-%m-%d %H:%M}..{self.end:%Y-%m-%d %H:%M} "
                f"({self.ticks:,} ticks)")


# =============================================================================
#  TickStore
# =============================================================================

class TickStore:
    """
    Kho tick lưu theo (symbol, tháng) + manifest SQLite.

    Ví dụ::

        store = TickStore("data")
        store.ingest("EURUSD", tickframe)
        tf = store.query("EURUSD", start, end)
        print(store.coverage("EURUSD"))
        print(store.gaps("EURUSD", start, end))
    """

    def __init__(self, root: Path | str = "data", prefer_parquet: bool = False) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.prefer_parquet = prefer_parquet and _have_pyarrow()
        self._ext = ".parquet" if self.prefer_parquet else ".tickbin"
        self._db_path = self.root / "manifest.db"
        self._db = sqlite3.connect(self._db_path)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    # ----- manifest -----------------------------------------------------

    def _init_db(self) -> None:
        """Tạo bảng manifest nếu chưa có (mục 2.3)."""
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS manifest (
                symbol     TEXT NOT NULL,
                year_month TEXT NOT NULL,   -- 'YYYY-MM'
                path       TEXT NOT NULL,
                start_us   INTEGER NOT NULL, -- tick đầu (epoch micro)
                end_us     INTEGER NOT NULL, -- tick cuối
                n_ticks    INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'downloaded', -- downloaded/converted
                checksum   TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, year_month)
            );
            CREATE INDEX IF NOT EXISTS idx_symbol ON manifest(symbol);
            """
        )
        self._db.commit()

    def _file_path(self, symbol: str, ym: str) -> Path:
        return self.root / symbol.upper() / f"{symbol.upper()}_{ym}{self._ext}"

    # ----- ghi (ingest) -------------------------------------------------

    def ingest(self, symbol: str, ticks: TickFrame) -> None:
        """
        Nạp tick vào kho. Tự tách theo tháng UTC, MERGE với dữ liệu tháng đã có
        (khử trùng lặp theo timestamp), rồi cập nhật manifest.

        Idempotent: gọi lại với cùng data không tạo trùng.
        """
        if ticks.is_empty():
            logger.info("ingest(%s): frame rỗng, bỏ qua.", symbol)
            return

        ticks.sort()
        # Nhóm chỉ mục tick theo 'YYYY-MM' (theo UTC của timestamp).
        buckets: dict[str, TickFrame] = {}
        for i in range(len(ticks)):
            ts = ticks.ts[i]
            dt = datetime.fromtimestamp(ts / US_PER_SEC, tz=timezone.utc)
            ym = f"{dt.year:04d}-{dt.month:02d}"
            buf = buckets.setdefault(ym, TickFrame())
            buf.append(ts, ticks.bid[i], ticks.ask[i],
                       ticks.bid_vol[i], ticks.ask_vol[i])

        for ym, new_tf in buckets.items():
            self._ingest_month(symbol, ym, new_tf)

    def _ingest_month(self, symbol: str, ym: str, new_tf: TickFrame) -> None:
        path = self._file_path(symbol, ym)

        # Merge với file tháng cũ nếu có (union theo timestamp, ưu tiên data mới).
        if path.exists():
            existing = self._read_file(path)
            merged = self._merge_dedup(existing, new_tf)
        else:
            merged = new_tf
            merged.sort()

        self._write_file(path, merged)

        rng = merged.time_range()
        checksum = self._checksum(path)
        self._db.execute(
            """
            INSERT INTO manifest
                (symbol, year_month, path, start_us, end_us, n_ticks,
                 status, checksum, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, year_month) DO UPDATE SET
                path=excluded.path, start_us=excluded.start_us,
                end_us=excluded.end_us, n_ticks=excluded.n_ticks,
                checksum=excluded.checksum, updated_at=excluded.updated_at
            """,
            (
                symbol.upper(), ym, str(path), rng[0], rng[1], len(merged),
                "downloaded", checksum, datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._db.commit()
        logger.info("ingest(%s %s): %d tick -> %s", symbol, ym, len(merged), path.name)

    @staticmethod
    def _merge_dedup(a: TickFrame, b: TickFrame) -> TickFrame:
        """Hợp 2 frame, khử tick trùng timestamp (giữ bản trong b — data mới hơn)."""
        seen: dict[int, tuple[float, float, float, float]] = {}
        for fr in (a, b):  # b ghi sau -> override a
            for i in range(len(fr)):
                seen[fr.ts[i]] = (fr.bid[i], fr.ask[i], fr.bid_vol[i], fr.ask_vol[i])
        out = TickFrame()
        for ts in sorted(seen):
            bid, ask, bv, av = seen[ts]
            out.append(ts, bid, ask, bv, av)
        return out

    # ----- đọc / query --------------------------------------------------

    def _read_file(self, path: Path) -> TickFrame:
        if path.suffix == ".parquet":
            return _read_parquet(path)
        return _read_tickbin(path)

    def _write_file(self, path: Path, tf: TickFrame) -> None:
        if self.prefer_parquet:
            _write_parquet(path, tf)
        else:
            _write_tickbin(path, tf)

    @staticmethod
    def _checksum(path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()[:16]

    def query(self, symbol: str, start: datetime, end: datetime) -> TickFrame:
        """
        Trả mọi tick của ``symbol`` trong ``[start, end)`` (UTC), gộp qua các tháng.
        """
        start_us = int(start.timestamp() * US_PER_SEC)
        end_us = int(end.timestamp() * US_PER_SEC)
        rows = self._db.execute(
            """SELECT path FROM manifest
               WHERE symbol=? AND end_us>=? AND start_us<?
               ORDER BY year_month""",
            (symbol.upper(), start_us, end_us),
        ).fetchall()

        out = TickFrame()
        for row in rows:
            tf = self._read_file(Path(row["path"]))
            out.extend(tf.slice_time(start_us, end_us))
        out.sort()
        return out

    # ----- coverage & gaps ----------------------------------------------

    def coverage(self, symbol: str) -> list[CoverageRange]:
        """
        Liệt kê các khoảng đã có dữ liệu (gộp các tháng liền kề thành 1 khoảng).
        """
        rows = self._db.execute(
            """SELECT start_us, end_us, n_ticks FROM manifest
               WHERE symbol=? ORDER BY start_us""",
            (symbol.upper(),),
        ).fetchall()
        if not rows:
            return []

        ranges: list[CoverageRange] = []
        cur_start = rows[0]["start_us"]
        cur_end = rows[0]["end_us"]
        cur_ticks = rows[0]["n_ticks"]
        # Gộp nếu khoảng cách < 2 ngày (coi như liền mạch; cuối tuần là bình thường).
        GAP_LIMIT_US = 2 * 24 * 3600 * US_PER_SEC
        for r in rows[1:]:
            if r["start_us"] - cur_end <= GAP_LIMIT_US:
                cur_end = max(cur_end, r["end_us"])
                cur_ticks += r["n_ticks"]
            else:
                ranges.append(self._mk_range(cur_start, cur_end, cur_ticks))
                cur_start, cur_end, cur_ticks = r["start_us"], r["end_us"], r["n_ticks"]
        ranges.append(self._mk_range(cur_start, cur_end, cur_ticks))
        return ranges

    @staticmethod
    def _mk_range(start_us: int, end_us: int, ticks: int) -> CoverageRange:
        return CoverageRange(
            datetime.fromtimestamp(start_us / US_PER_SEC, tz=timezone.utc),
            datetime.fromtimestamp(end_us / US_PER_SEC, tz=timezone.utc),
            ticks,
        )

    def gaps(self, symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """
        Khoảng còn THIẾU trong ``[start, end)`` dựa trên coverage. Bỏ qua cuối tuần
        không được làm tự động ở đây (đơn giản hoá) — chỉ trả lỗ hổng thô.
        """
        cov = self.coverage(symbol)
        result: list[tuple[datetime, datetime]] = []
        cursor = start
        for c in cov:
            if c.end <= start or c.start >= end:
                continue
            if c.start > cursor:
                result.append((cursor, min(c.start, end)))
            cursor = max(cursor, c.end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
        return result

    # ----- vòng đời -----------------------------------------------------

    def symbols(self) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT symbol FROM manifest ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def set_status(self, symbol: str, ym: str, status: str) -> None:
        self._db.execute(
            "UPDATE manifest SET status=? WHERE symbol=? AND year_month=?",
            (status, symbol.upper(), ym),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "TickStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
