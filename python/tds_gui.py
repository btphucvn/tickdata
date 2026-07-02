"""
TDS Clone — Tick Data Manager (GUI)
Dung CHUNG pipeline voi MT4 dialog + CLI: download_ticks, tick_store,
symbols_meta, fxt_builder, coverage_check.

Chay: python tds_gui.py   (hoac LAUNCH_GUI.bat)
"""

import sys
import os
import io
import time
import datetime
import contextlib
import threading

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt, QThread, Signal, QObject, QDate
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QComboBox, QDateEdit, QPushButton, QPlainTextEdit, QLabel,
    QTableWidget, QTableWidgetItem, QGroupBox, QProgressBar, QMessageBox,
    QHeaderView, QAbstractItemView, QSpinBox, QDoubleSpinBox, QCheckBox,
    QLineEdit, QScrollArea, QGridLayout,
)

import tick_store
import symbols_meta
import settings_store
from download_ticks import download_complete
from coverage_check import analyze_coverage, estimate_model_quality


# ---------------------------------------------------------------------------
# Worker chay tac vu nen, bat stdout -> log signal
# ---------------------------------------------------------------------------
class Worker(QObject):
    log      = Signal(str)
    done     = Signal(bool, str)
    progress = Signal(int, int)   # (done_hours, total_hours)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self._last_prog = 0.0

    def run(self):
        # Bat CA stdout LAN stderr -> log signal (de thay loi/cooldown/rate-limit).
        # Dong tien do dung '\r' cung duoc emit (throttle) de thay live "da tai bao nhieu".
        sig = self.log

        class _Emit(io.TextIOBase):
            # stdout/stderr bi redirect TOAN CUC -> nhieu luong (ThreadPool tai data)
            # cung goi write() -> PHAI khoa, neu khong 'while "\n" in buf' + split se
            # dua 2 luong vao race -> ValueError unpack -> CRASH giua chung khi tai.
            def __init__(s): s.buf = ""; s.last_cr = 0.0; s.lock = threading.Lock()
            def write(s, txt):
                lines = []
                seg_emit = None
                with s.lock:
                    s.buf += txt
                    # Tach theo \n: dong hoan chinh -> emit (giu lai vinh vien)
                    while "\n" in s.buf:
                        line, s.buf = s.buf.split("\n", 1)
                        line = line.split("\r")[-1]
                        if line.strip():
                            lines.append(line.rstrip())
                    # Phan con lai sau \r (dong tien do) -> throttle ~3 lan/giay
                    if "\r" in s.buf:
                        seg = s.buf.split("\r")[-1]
                        now = time.time()
                        if seg.strip() and now - s.last_cr >= 0.3:
                            s.last_cr = now
                            seg_emit = seg.rstrip()
                # emit NGOAI khoa (tranh giu lock khi qua Qt event loop)
                for ln in lines:
                    sig.emit(ln)
                if seg_emit is not None:
                    sig.emit(seg_emit)
                return len(txt)
            def flush(s): pass

        # Throttle progress bar + thinh thoang in 1 dong tien do dang text.
        def prog(d, t, n=0):
            now = time.time()
            if d >= t or now - self._last_prog >= 0.1:
                self._last_prog = now
                self.progress.emit(d, t)
            # Dong text tien do moi ~1.5s (so gio + so tick da tai)
            if n and (now - getattr(self, "_last_txt", 0.0) >= 1.5 or d >= t):
                self._last_txt = now
                pct = (d * 100 // t) if t else 0
                self.log.emit(f"   ... da tai {d}/{t} gio ({pct}%)  |  {n:,} ticks")

        emit = _Emit()
        try:
            with contextlib.redirect_stdout(emit), contextlib.redirect_stderr(emit):
                msg = self.fn(prog)
            self.done.emit(True, msg or "Hoan tat.")
        except Exception as e:
            import traceback
            self.log.emit("[LOI] " + str(e))
            self.log.emit(traceback.format_exc())
            self.done.emit(False, f"Loi: {e}")


# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TDS Clone — Tick Data Manager")
        self.resize(820, 600)
        self._thread = None
        self._worker = None
        # Widget cua lan chay _run hien tai (slot worker dung, xem _run).
        self._cur_btn = self._cur_bar = self._cur_log = None

        self._svc = None   # TDSService (tab Service)

        # Don vung tam backtest cu (thang nen duoc bung ra day, khong phai store).
        try:
            tick_store.clean_scratch()
        except Exception:
            pass

        tabs = QTabWidget()
        tabs.addTab(self._tab_download(), "1. Download")
        tabs.addTab(self._tab_manage(),   "2. Quan ly data")
        tabs.addTab(self._tab_settings(), "3. Settings")
        tabs.addTab(self._tab_service(),  "4. Service")
        tabs.addTab(self._tab_log(),      "5. Log")
        self.setCentralWidget(tabs)
        self._tabs = tabs
        tabs.currentChanged.connect(lambda i: self._refresh_table() if i == 1 else None)

        self._refresh_table()

        # Tu bat service auto-inject ngay khi mo (1 phat an ngay).
        try:
            self._svc_start()
            self._svc_log("[SVC] Tu khoi dong cung GUI. Mo MT4 -> tu inject.")
        except Exception as e:
            self.glog.appendPlainText(f"[SVC] Khong tu bat service: {e}")

    # ---- Tab 1: Download -------------------------------------------------
    def _tab_download(self):
        w = QWidget(); root = QVBoxLayout(w)
        box = QGroupBox("Tai tick data that tu Dukascopy (FX, vang, BTC, index... )")
        form = QFormLayout(box)

        self.dl_symbol = QComboBox(); self.dl_symbol.setEditable(True)
        self.dl_symbol.addItems(symbols_meta.known_symbols())
        self.dl_symbol.setCurrentText("EURUSD")
        form.addRow("Symbol:", self.dl_symbol)

        today = QDate.currentDate()
        self.dl_from = QDateEdit(today.addMonths(-1)); self.dl_from.setCalendarPopup(True)
        self.dl_from.setDisplayFormat("yyyy-MM-dd")
        self.dl_to = QDateEdit(today); self.dl_to.setCalendarPopup(True)
        self.dl_to.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Tu ngay (UTC):", self.dl_from)
        form.addRow("Den ngay (UTC):", self.dl_to)

        # Chon server download
        self.dl_source = QComboBox()
        self.dl_source.addItem("Tu dong (uu tien freeserv)", "auto")
        self.dl_source.addItem("freeserv.dukascopy.com (it bi chan)", "freeserv")
        self.dl_source.addItem("datafeed.dukascopy.com (bi5, nhanh hon)", "datafeed")
        form.addRow("Server:", self.dl_source)

        # So luong tai song song. 1 = tuan tu (1 ngay/lan) — freeserv it treo nhat.
        self.dl_workers = QSpinBox()
        self.dl_workers.setRange(1, 32)
        self.dl_workers.setValue(1)   # 1 = tuan tu, khong chia luong -> freeserv on dinh
        self.dl_workers.setToolTip("So ngay tai dong thoi. 1 = TUAN TU (khong chia luong). "
                                   "Nay freeserv da co timeout tu-khoi-phuc + keep-alive nen dat "
                                   "3-4 van an toan va nhanh hon 3-4 lan. Qua cao (>6) moi de bi chan.")
        form.addRow("So luong (1 = tuan tu):", self.dl_workers)

        # Tu nen sau moi thang (giong TDS: luu tru nen, bung khi backtest) -> dia nho ~5x.
        self.dl_autocompress = QCheckBox("Tu nen data sau moi thang (tiet kiem dia ~5x, giong TDS)")
        self.dl_autocompress.setChecked(True)
        self.dl_autocompress.setToolTip("Tai xong thang nao thi nen thang do ngay (LZMA, khong mat "
                                        "tick). Khi backtest/publish se tu bung ra .bin cho MT4. "
                                        "Tat neu muon giu file .bin thô (doc nhanh hon, ton dia hon).")
        form.addRow("", self.dl_autocompress)
        root.addWidget(box)

        self.dl_btn = QPushButton("Tai data (tu dong lap day gio trong)")
        self.dl_btn.clicked.connect(self._on_download)
        root.addWidget(self.dl_btn)

        self.dl_bar = QProgressBar(); self.dl_bar.setRange(0, 0); self.dl_bar.hide()
        root.addWidget(self.dl_bar)

        hint = QLabel("Tai xong la xong. Vao MT4 tick \"Use my tick data\" -> tu chay "
                      "(khong can build gi, giong TDS).")
        hint.setStyleSheet("color:#070; padding:4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.dl_log = QPlainTextEdit(); self.dl_log.setReadOnly(True)
        self.dl_log.setMaximumBlockCount(20000)   # gioi han dong, tranh phinh RAM
        self.dl_log.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        root.addWidget(self.dl_log, 1)
        return w

    def _on_download(self):
        sym = self.dl_symbol.currentText().strip().upper()
        d_from = self.dl_from.date().toPython()
        d_to   = self.dl_to.date().toPython()
        if d_to < d_from:
            QMessageBox.warning(self, "Loi", "Ngay 'den' phai sau 'tu'."); return

        meta = symbols_meta.resolve(sym)
        source = self.dl_source.currentData()
        source_label = self.dl_source.currentText()
        workers = self.dl_workers.value()
        autocompress = self.dl_autocompress.isChecked()   # doc TRUOC khi vao thread
        total_hours = ((d_to - d_from).days + 1) * 24
        self.dl_log.clear()
        self.dl_log.appendPlainText(f"Symbol {sym} -> Dukascopy code {meta.code}  "
                                    f"(/{meta.divisor}, digits={meta.digits})")
        self.dl_log.appendPlainText(f"Server: {source_label}")
        self.dl_log.appendPlainText(f"Tai {total_hours} gio ({(d_to-d_from).days+1} ngay)...")

        def task(progress_cb):
            import time as _t
            from download_ticks import download_months, _iter_months, RateLimitedError
            # Thang da HOAN TAT = moi thang co data TRU thang cao nhat (thang cao nhat
            # co the dang do neu lan truoc crash giua chung -> de resume theo ngay).
            _mcounts = {k for k, v in tick_store.month_counts(sym).items() if v > 0}
            _sorted = sorted(_mcounts)
            existing = set(_sorted[:-1]) if _sorted else set()

            def save_day(y, m, day_ticks):
                # Luu TUNG NGAY ngay lap tuc: RAM thap, tien do khong mat khi crash.
                tick_store.append_day(sym, y, m, day_ticks)

            def month_last_ms(y, m):
                return tick_store.month_last_ms(sym, y, m)

            def month_done(y, m, n):
                print(f"=== XONG {y}-{m:02d}: +{n:,} ticks moi (da luu tung ngay) ===")

            months = _iter_months(d_from, d_to)
            t_start = _t.time()
            print("=" * 56)
            print(f"BAT DAU TAI {sym}  ({meta.code})")
            print(f"  Khoang   : {d_from} -> {d_to}  ({(d_to-d_from).days+1} ngay, {total_hours} gio)")
            print(f"  Server   : {source_label}")
            print(f"  Luong    : {workers} song song  |  divisor /{meta.divisor}  digits {meta.digits}")
            print(f"  So thang : {len(months)}  (da co san: {len(existing & {(y,m) for y,m,_,_ in months})})")
            print("=" * 56)
            done_h = 0
            grand = 0
            skipped = 0
            try:
                for idx, (y, m, lo, hi) in enumerate(months):
                    if (y, m) in existing:
                        print(f"[bo qua] {y}-{m:02d}: da co trong store")
                        done_h += ((hi - lo).days + 1) * 24
                        skipped += 1
                        progress_cb(done_h, total_hours, grand)
                        continue
                    print(f"\n--- Thang {y}-{m:02d} ({lo} -> {hi}) [{idx+1}/{len(months)}] ---")
                    base = done_h
                    g, md = download_months(
                        sym, lo, hi, append_day_cb=save_day,
                        month_last_ms_cb=month_last_ms,
                        month_done_cb=month_done, source=source,
                        max_workers=workers,
                        progress_cb=lambda d, t, n, b=base, gg=grand:
                            progress_cb(b + d, total_hours, gg + n),
                        pause_sec=0)
                    grand += g
                    done_h += ((hi - lo).days + 1) * 24
                    # TU NEN thang vua xong (giong TDS: luon giu data o dang nen,
                    # bung lai khi backtest). Giu dia luon nho SUOT qua trinh tai.
                    if autocompress:
                        try:
                            b, a = tick_store.compress_month(sym, y, m)
                            if b:
                                print(f"   [nen] {y}-{m:02d}: {b/1024/1024:.0f} -> "
                                      f"{a/1024/1024:.1f} MB  (x{b/max(a,1):.1f} nho hon)")
                        except Exception as e:   # noqa: BLE001
                            print(f"   [nen][LOI] {y}-{m:02d}: {e}")
                    if idx < len(months) - 1:
                        _t.sleep(2.0)
            except RateLimitedError as e:
                print(f"\n[!!!] DUNG GIUA CHUNG: {e}")
                print("      Cac thang da xong van duoc luu. Chay lai de tai tiep.")
                return "Bi chan giua chung — cac thang xong da luu, chay lai de tiep tuc."

            dt = _t.time() - t_start
            cov = tick_store.coverage(sym)
            print("\n" + "=" * 56)
            print(f"HOAN TAT {sym} trong {dt:.0f}s")
            print(f"  Tai moi      : {grand:,} ticks  ({skipped} thang bo qua vi da co)")
            print(f"  Store hien co: {cov[2] if cov else 0:,} ticks")
            print("=" * 56)
            return f"{sym}: tai {grand:,} ticks moi, store co {cov[2] if cov else 0:,} ticks ({dt:.0f}s)"

        self._run(task, self.dl_btn, self.dl_bar, self.dl_log, total_hint=total_hours)

    # ---- Tab 2: Quan ly data (list + delete) -----------------------------
    def _tab_manage(self):
        w = QWidget(); root = QVBoxLayout(w)
        root.addWidget(QLabel("Cac symbol da tai (store dung chung voi MT4 + CLI):"))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Symbol", "Tick dau", "Tick cuoi", "Thang co data", "Ticks", "Dung luong"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        b_refresh = QPushButton("Lam moi"); b_refresh.clicked.connect(self._refresh_table)
        b_months  = QPushButton("Xem thang da tai"); b_months.clicked.connect(self._on_months)
        b_verify  = QPushButton("Kiem tra quality"); b_verify.clicked.connect(self._on_verify)
        self.mg_compress_btn = QPushButton("Nen (tiet kiem dia)")
        self.mg_compress_btn.setToolTip("Nen cac thang cua symbol da chon (LZMA, ~5x nho hon, "
                                        "khong mat tick nao). Tu bung lai khi backtest/publish.")
        self.mg_compress_btn.clicked.connect(self._on_compress)
        b_delete  = QPushButton("Xoa symbol da chon"); b_delete.clicked.connect(self._on_delete)
        b_delete.setStyleSheet("color:#b00;")
        row.addWidget(b_refresh); row.addWidget(b_months); row.addWidget(b_verify)
        row.addWidget(self.mg_compress_btn)
        row.addStretch(); row.addWidget(b_delete)
        root.addLayout(row)

        self.mg_bar = QProgressBar(); self.mg_bar.setRange(0, 0); self.mg_bar.hide()
        root.addWidget(self.mg_bar)

        # Bang luoi thang da tai cua symbol dang chon
        self.mg_months = QPlainTextEdit(); self.mg_months.setReadOnly(True)
        self.mg_months.setMaximumHeight(150)
        self.mg_months.setStyleSheet("font-family: Consolas, monospace;")
        root.addWidget(self.mg_months)

        self.mg_lbl = QLabel("")
        root.addWidget(self.mg_lbl)
        # Tu hien thang khi chon symbol khac
        self.table.itemSelectionChanged.connect(self._on_months)
        return w

    def _on_months(self):
        sym = self._selected_symbol_quiet()
        if not sym:
            return
        counts = tick_store.month_counts(sym)
        if not counts:
            self.mg_months.setPlainText(f"{sym}: chua co data")
            return
        MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
        years = sorted(set(y for (y, m) in counts))
        total = sum(counts.values())
        lines = [f"{sym} — da tai {total:,} ticks",
                 "Year  " + " ".join(f"{m:>5}" for m in MON)]
        for y in years:
            cells = []
            for mo in range(1, 13):
                c = counts.get((y, mo), 0)
                cells.append("    ." if c == 0 else
                             (f"{c//1000:>4}k" if c >= 1000 else f"{c:>5}"))
            lines.append(f"{y}  " + " ".join(cells))
        self.mg_months.setPlainText("\n".join(lines))

    def _selected_symbol_quiet(self):
        r = self.table.currentRow()
        if r < 0 or not self.table.item(r, 0):
            return None
        return self.table.item(r, 0).text()

    def _refresh_table(self):
        rows = tick_store.list_symbols()
        self.table.setRowCount(len(rows))
        total_mb = 0
        for i, r in enumerate(rows):
            sym = r["symbol"]
            if r["count"]:
                fd = datetime.datetime.utcfromtimestamp(r["first_ms"]/1000)
                td = datetime.datetime.utcfromtimestamp(r["last_ms"]/1000)
                f = fd.strftime("%Y-%m-%d")
                t = td.strftime("%Y-%m-%d")
                # So thang CO data vs so thang trong span (de lo data thua/thieu)
                have = len(tick_store.month_counts(sym))
                span = (td.year - fd.year) * 12 + (td.month - fd.month) + 1
                months_str = f"{have}/{span}"
                incomplete = have < span
            else:
                f = t = "-"; months_str = "0"; incomplete = False
            n_comp = r.get("compressed", 0)
            size_str = f"{r['size_mb']:.1f} MB"
            if n_comp:
                size_str += f"  (nen {n_comp})"
            vals = [sym, f, t, months_str, f"{r['count']:,}", size_str]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # Cot "Thang co data": do neu thieu thang -> canh bao data khong lien tuc
                if j == 3 and incomplete:
                    it.setForeground(Qt.red)
                    it.setToolTip(f"Data KHONG lien tuc: chi co {have}/{span} thang. "
                                  f"Con thieu {span-have} thang trong khoang nay.")
                self.table.setItem(i, j, it)
            total_mb += r["size_mb"]
        self.mg_lbl.setText(f"{len(rows)} symbol  |  tong {total_mb:.1f} MB  "
                            f"|  cot 'Thang co data' do = data bi thieu thang (khong lien tuc)")

    def _selected_symbol(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Chon", "Hay chon 1 symbol trong bang."); return None
        return self.table.item(r, 0).text()

    def _on_delete(self):
        sym = self._selected_symbol()
        if not sym: return
        if QMessageBox.question(self, "Xac nhan", f"Xoa toan bo data {sym}?") != QMessageBox.Yes:
            return
        tick_store.delete_symbol(sym)
        self._refresh_table()
        self.mg_lbl.setText(f"Da xoa {sym}.")

    def _on_compress(self):
        sym = self._selected_symbol()
        if not sym:
            return
        # So thang raw can nen (de biet co viec khong)
        raw_months = [(y, m) for (y, m) in tick_store._month_list(sym)
                      if not tick_store.is_compressed(sym, y, m)]
        if not raw_months:
            QMessageBox.information(self, "Nen", f"{sym}: tat ca thang da nen roi.")
            return

        def task(progress_cb):
            print(f"[nen] {sym}: nen {len(raw_months)} thang raw...")
            def prog(done, total, y, m):
                progress_cb(done, total)
                print(f"   [nen] {y}-{m:02d}  ({done}/{total})")
            before, after = tick_store.compress_symbol(sym, progress=prog)
            saved = (before - after) / 1024 / 1024
            print(f"[nen] {sym}: {before/1024/1024:.0f} -> {after/1024/1024:.0f} MB "
                  f"(tiet kiem {saved:.0f} MB, x{before/max(after,1):.1f})")
            return f"{sym}: nen xong, tiet kiem {saved:.0f} MB (x{before/max(after,1):.1f} nho hon)"

        self._run(task, self.mg_compress_btn, self.mg_bar, self.glog,
                  total_hint=len(raw_months))

    def _on_verify(self):
        sym = self._selected_symbol()
        if not sym: return
        cov = tick_store.coverage(sym)
        if not cov: return
        d_from = datetime.datetime.utcfromtimestamp(cov[0]/1000).date()
        d_to   = datetime.datetime.utcfromtimestamp(cov[1]/1000).date()
        c = analyze_coverage(tick_store.iter_all(sym), d_from, d_to)
        QMessageBox.information(self, f"Quality {sym}",
            f"Khoang: {d_from} -> {d_to}\n"
            f"Ticks: {c['ticks']:,}\n"
            f"Gio giao dich co data: {c['hours_with_data']}/{c['trading_hours']} "
            f"({c['coverage_pct']:.2f}%)\n"
            f"Model quality du kien: {estimate_model_quality(c['coverage_pct'])}")

    # ---- Tab 3: Settings per-symbol (giong TDS ITickDataSettings) --------
    def _tab_settings(self):
        outer = QWidget(); ov = QVBoxLayout(outer)

        top = QHBoxLayout()
        top.addWidget(QLabel("Symbol:"))
        self.st_symbol = QComboBox(); self.st_symbol.setEditable(True)
        self.st_symbol.addItems(symbols_meta.known_symbols())
        self.st_symbol.setCurrentText("EURUSD")
        self.st_symbol.currentTextChanged.connect(self._settings_load)
        top.addWidget(self.st_symbol, 1)
        b_load = QPushButton("Tai lai"); b_load.clicked.connect(self._settings_load)
        top.addWidget(b_load)
        ov.addLayout(top)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); form_root = QVBoxLayout(inner)
        self._st_widgets = {}   # field_name -> widget

        # --- Group: Timezone ---
        gz = QGroupBox("Mui gio (GMT/DST)  —  mac dinh GMT+2 / DST=US (giong TDS)")
        fz = QFormLayout(gz)
        fz.addRow("GMT offset (gio):", self._mk_spin("gmt_offset", -12, 14))
        dst = QComboBox(); dst.addItems(["Khong", "My (US)", "Chau Au (EU)"])
        self._st_widgets["dst"] = dst
        fz.addRow("DST:", dst)
        form_root.addWidget(gz)

        # --- Group: Variable Spread (cong thuc loi TDS) ---
        gs = QGroupBox("Variable Spread  —  clamp(real*mult + add, min, max)")
        fs = QFormLayout(gs)
        fs.addRow(self._mk_check("use_variable_spread", "Dung variable spread"))
        fs.addRow("Spread multiplier:", self._mk_dspin("spread_multiplier", 0, 100, 0.01, 2))
        fs.addRow("Spread addition (pts):", self._mk_dspin("spread_addition", 0, 1000, 0.1, 1))
        fs.addRow("Min spread (pts, 0=off):", self._mk_spin("min_spread", 0, 100000))
        fs.addRow("Max spread (pts, 0=off):", self._mk_spin("max_spread", 0, 100000))
        form_root.addWidget(gs)

        # --- Group: Slippage ---
        gl = QGroupBox("Slippage"); fl = QFormLayout(gl)
        fl.addRow(self._mk_check("slippage_enabled", "Bat slippage"))
        fl.addRow(self._mk_check("reproducible_slippage", "Reproducible (cung seed)"))
        fl.addRow(self._mk_check("latency_based_slippage", "Latency-based"))
        fl.addRow(self._mk_check("dealer_style_slippage", "Dealer-style"))
        fl.addRow(self._mk_check("standard_deviation_slippage", "Standard deviation"))
        fl.addRow("Max favorable (pts):", self._mk_spin("max_favorable_slippage", 0, 100000))
        fl.addRow("Max unfavorable (pts):", self._mk_spin("max_unfavorable_slippage", 0, 100000))
        fl.addRow("Min market delay (ms):", self._mk_spin("min_market_slippage_delay", 0, 100000))
        fl.addRow("Max market delay (ms):", self._mk_spin("max_market_slippage_delay", 0, 100000))
        fl.addRow("Slippage mean (pts):", self._mk_dspin("slippage_mean", 0, 10000, 0.1, 2))
        fl.addRow("Slippage stdev (pts):", self._mk_dspin("slippage_stdev", 0, 10000, 0.1, 2))
        fl.addRow(self._mk_check("use_custom_slippage_chance", "Custom slippage chance"))
        fl.addRow("Slippage chance (%):", self._mk_dspin("custom_slippage_chance", 0, 100, 1, 1))
        fl.addRow(self._mk_check("use_custom_favorable_chance", "Custom favorable chance"))
        fl.addRow("Favorable chance (%):", self._mk_dspin("favorable_slippage_chance", 0, 100, 1, 1))
        fl.addRow(self._mk_check("limit_order_slippage", "Ap cho Limit order"))
        fl.addRow(self._mk_check("stop_order_slippage", "Ap cho Stop order"))
        fl.addRow(self._mk_check("sl_order_slippage", "Ap cho SL"))
        fl.addRow(self._mk_check("tp_order_slippage", "Ap cho TP"))
        form_root.addWidget(gl)

        # --- Group: Overrides ---
        go = QGroupBox("Override symbol properties"); fo = QFormLayout(go)
        fo.addRow(self._mk_check("override_digits", "Override digits"))
        fo.addRow("Digits:", self._mk_spin("digits", 0, 10))
        fo.addRow(self._mk_check("override_min_lot", "Override min lot"))
        fo.addRow("Min lot:", self._mk_dspin("min_lot", 0, 10000, 0.01, 2))
        fo.addRow(self._mk_check("override_max_lot", "Override max lot"))
        fo.addRow("Max lot:", self._mk_dspin("max_lot", 0, 1e6, 1, 2))
        fo.addRow(self._mk_check("override_lot_step", "Override lot step"))
        fo.addRow("Lot step:", self._mk_dspin("lot_step", 0, 1000, 0.01, 2))
        fo.addRow(self._mk_check("override_stops_level", "Override stops level"))
        fo.addRow("Stops level (pts):", self._mk_spin("stops_level", 0, 100000))
        fo.addRow("Commission /lot:", self._mk_dspin("commission_per_lot", 0, 10000, 0.1, 2))
        form_root.addWidget(go)

        form_root.addStretch()
        scroll.setWidget(inner)
        ov.addWidget(scroll, 1)

        row = QHBoxLayout()
        b_save = QPushButton("Luu settings"); b_save.clicked.connect(self._settings_save)
        b_save.setStyleSheet("font-weight:bold;")
        self.st_status = QLabel("")
        row.addWidget(b_save); row.addWidget(self.st_status, 1)
        ov.addLayout(row)

        self._settings_load()
        return outer

    def _mk_spin(self, field, lo, hi):
        w = QSpinBox(); w.setRange(lo, hi); self._st_widgets[field] = w; return w

    def _mk_dspin(self, field, lo, hi, step, dec):
        w = QDoubleSpinBox(); w.setRange(lo, hi); w.setSingleStep(step)
        w.setDecimals(dec); self._st_widgets[field] = w; return w

    def _mk_check(self, field, text):
        w = QCheckBox(text); self._st_widgets[field] = w; return w

    def _settings_load(self):
        sym = self.st_symbol.currentText().strip().upper()
        if not sym:
            return
        s = settings_store.load(sym)
        for field, w in self._st_widgets.items():
            v = getattr(s, field)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QComboBox):   # dst
                w.setCurrentIndex(int(v))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(v))
            elif isinstance(w, QSpinBox):
                w.setValue(int(v))
        self.st_status.setText(f"Da tai settings {sym}.")

    def _settings_save(self):
        sym = self.st_symbol.currentText().strip().upper()
        s = settings_store.load(sym)
        s.symbol = sym
        for field, w in self._st_widgets.items():
            if isinstance(w, QCheckBox):
                setattr(s, field, w.isChecked())
            elif isinstance(w, QComboBox):
                setattr(s, field, w.currentIndex())
            elif isinstance(w, QDoubleSpinBox):
                setattr(s, field, w.value())
            elif isinstance(w, QSpinBox):
                setattr(s, field, w.value())
        settings_store.save(s)
        # Vi du minh hoa cong thuc
        ex = s.spread_points_for(8.0)
        self.st_status.setText(f"Da luu {sym}.  Vi du: real=8pts -> {ex:.1f}pts")
        self.glog.appendPlainText(f"[settings] Luu {sym}: var={s.use_variable_spread} "
                                  f"mult={s.spread_multiplier} add={s.spread_addition} "
                                  f"slippage={s.slippage_enabled}")

    # ---- Tab 4: Service (auto-inject + shared memory) --------------------
    def _tab_service(self):
        w = QWidget(); root = QVBoxLayout(w)

        box = QGroupBox("TDS Clone Service  —  tu inject MT4 + giu shared memory")
        f = QFormLayout(box)
        self.sv_symbol = QComboBox(); self.sv_symbol.setEditable(True)
        self.sv_symbol.addItems(symbols_meta.known_symbols())
        self.sv_symbol.setCurrentText(settings_store.get_state("active_symbol", "EURUSD"))
        f.addRow("Symbol active:", self.sv_symbol)
        self.sv_mode = QComboBox()
        self.sv_mode.addItem("Spread (deploy FXT + spread bien dong)", "spread")
        self.sv_mode.addItem("Tick (nhet bid+ask that, khong can FXT)", "tick")
        f.addRow("Che do:", self.sv_mode)
        root.addWidget(box)

        row = QHBoxLayout()
        self.sv_start = QPushButton("Bat service"); self.sv_start.clicked.connect(self._svc_start)
        self.sv_stop  = QPushButton("Tat service"); self.sv_stop.clicked.connect(self._svc_stop)
        self.sv_stop.setEnabled(False)
        self.sv_pub   = QPushButton("Publish lai SHM"); self.sv_pub.clicked.connect(self._svc_publish)
        row.addWidget(self.sv_start); row.addWidget(self.sv_stop); row.addWidget(self.sv_pub)
        root.addLayout(row)

        self.sv_status = QLabel("Service: TAT")
        self.sv_status.setStyleSheet("padding:4px;")
        root.addWidget(self.sv_status)

        hint = QLabel(
            "1. Bat service (NEN chay GUI as Administrator de inject duoc).\n"
            "2. Mo MT4 -> service tu inject tdshook.dll.\n"
            "3. Trong Strategy Tester tick 'Use my tick data' -> chay.\n"
            "Neu chua build native: vao native\\ chay 'cmake --build build --config Release'.")
        hint.setStyleSheet("color:#444; padding:4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.sv_log = QPlainTextEdit(); self.sv_log.setReadOnly(True)
        self.sv_log.setMaximumBlockCount(20000)
        root.addWidget(self.sv_log, 1)
        return w

    def _svc_log(self, msg):
        self.sv_log.appendPlainText(msg)
        self.glog.appendPlainText(msg)

    def _svc_start(self):
        if self._svc:
            return
        import tds_service
        self._svc = tds_service.TDSService(log=self._svc_log)
        self._svc.active_symbol = self.sv_symbol.currentText().strip().upper()
        self._svc.active_mode = self.sv_mode.currentData()
        if not (self._svc.injector and self._svc.hook):
            self._svc_log("[CANH BAO] Chua build native — service chi giu SHM, khong auto-inject.")
        self._svc.start()
        self.sv_start.setEnabled(False); self.sv_stop.setEnabled(True)
        self.sv_status.setText(f"Service: DANG CHAY  (active={self._svc.active_symbol})")

    def _svc_stop(self):
        if self._svc:
            self._svc.stop(); self._svc = None
        self.sv_start.setEnabled(True); self.sv_stop.setEnabled(False)
        self.sv_status.setText("Service: TAT")

    def _svc_publish(self):
        sym = self.sv_symbol.currentText().strip().upper()
        mode = self.sv_mode.currentData()
        if self._svc:
            self._svc.publish(symbol=sym, mode=mode)
        else:
            import shm_writer
            try:
                self._shm_holder = shm_writer.publish_for_symbol(sym, mode=mode)[0]
                self._svc_log(f"[SHM] Publish {sym} ({mode}) — khong qua service.")
            except Exception as e:
                self._svc_log(f"[SHM][LOI] {e}")
        self.sv_status.setText(f"Da publish {sym} ({mode}).")

    # ---- Tab 5: Log ------------------------------------------------------
    def _tab_log(self):
        w = QWidget(); root = QVBoxLayout(w)
        self.glog = QPlainTextEdit(); self.glog.setReadOnly(True)
        self.glog.setMaximumBlockCount(50000)
        root.addWidget(self.glog)
        return w

    # ---- Worker helper ---------------------------------------------------
    # QUAN TRONG: slot PHAI la METHOD cua MainWindow (QObject o main thread) — KHONG
    # duoc dung closure. QueuedConnection toi closure (khong co QObject chu) KHONG
    # marshal ve main thread -> slot chay tren worker thread -> cham widget tu non-GUI
    # thread -> ACCESS VIOLATION (crash). Dung bound method thi Qt marshal dung main
    # thread. Widget cua lan chay hien tai luu vao self._cur_* de slot dung.
    def _run(self, fn, btn, bar, log_widget, total_hint=0):
        if self._thread:
            return
        self._cur_btn = btn
        self._cur_bar = bar
        self._cur_log = log_widget
        btn.setEnabled(False)
        if total_hint > 0:
            bar.setRange(0, total_hint); bar.setValue(0)   # xac dinh ngay: thay 0/N
        else:
            bar.setRange(0, 0)   # vo dinh
        bar.show()
        self._thread = QThread()
        self._worker = Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._wk_log, Qt.QueuedConnection)
        self._worker.progress.connect(self._wk_progress, Qt.QueuedConnection)
        self._worker.done.connect(self._wk_done, Qt.QueuedConnection)
        self._thread.start()

    def _wk_log(self, line):
        if self._cur_log is not None:
            self._cur_log.appendPlainText(line)
        self.glog.appendPlainText(line)

    def _wk_progress(self, done, total):
        bar = self._cur_bar
        if bar is None:
            return
        if bar.maximum() != total:
            bar.setRange(0, total)
        bar.setValue(done)

    def _wk_done(self, ok, msg):
        btn = self._cur_btn; bar = self._cur_bar; log_widget = self._cur_log
        if btn is not None:
            btn.setEnabled(True)
        if bar is not None:
            bar.hide(); bar.reset()
        if log_widget is not None:
            log_widget.appendPlainText(("[OK] " if ok else "[X] ") + msg)
        if self._thread is not None:
            self._thread.quit(); self._thread.wait()
        self._thread = None; self._worker = None
        self._cur_btn = self._cur_bar = self._cur_log = None
        self._refresh_table()
        # Bao ro neu bi rate-limit
        if not ok and ("chan" in msg or "rate-limit" in msg.lower()):
            QMessageBox.warning(self, "IP bi chan tam thoi",
                "Dukascopy dang chan IP nay (do tai qua nhieu).\n\n"
                "Hay doi ~15-20 phut roi thu lai.\n"
                "Data da tai van duoc luu (cache), khong mat.")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
