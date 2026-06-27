"""
Module 6 — Giao diện desktop PySide6 (Tick Data Manager clone).

Cấu trúc cửa sổ chính (QMainWindow) gồm các TAB:
    1. "Download"  : form chọn symbol + khoảng ngày -> tải Dukascopy vào TickStore,
                     có thanh tiến trình và log.
    2. "Build FXT" : form chọn symbol/period/khoảng + cấu hình SpreadModel -> build
                     .fxt/.hst/.tdspread.
    3. "Coverage"  : bảng hiển thị symbol nào đã có data, khoảng nào.
    4. "Log"       : log toàn cục.

Công việc nặng (download/build) chạy trong QThread (worker) để KHÔNG đơ giao diện;
worker phát signal progress/log/done về main thread.

PySide6 là phụ thuộc tuỳ chọn: ``pip install "tdsclone[gui]"``. Trên WSL không có
display thì GUI không mở được — dùng :mod:`tdsclone.cli` thay thế.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- Import PySide6 với thông báo lỗi thân thiện nếu thiếu --------------------
try:
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox,
        QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
        QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
        QSpinBox, QStatusBar, QTableWidget, QTableWidgetItem, QTabWidget,
        QVBoxLayout, QWidget,
    )
    from PySide6.QtCore import QDate
except ImportError as exc:  # pragma: no cover - chỉ chạy khi thiếu PySide6
    raise SystemExit(
        "Thiếu PySide6. Cài bằng:  pip install \"tdsclone[gui]\"\n"
        f"(chi tiết: {exc})"
    )

from tdsclone.pipeline import build_fxt, download_range, make_spread_model
from tdsclone.store.tickstore import TickStore
from tdsclone.symbols import known_symbols


# =============================================================================
#  Worker — chạy tác vụ nặng trong thread riêng
# =============================================================================

class Worker(QObject):
    """
    Bọc một hàm tác vụ chạy nền. Phát signal:
        progress(done, total) — cập nhật thanh tiến trình.
        log(text)             — dòng log.
        finished(ok, message) — xong (ok=True/False).
    """

    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            # Truyền callback progress/log vào hàm tác vụ nếu nó nhận.
            msg = self._fn(*self._args,
                           progress=self.progress.emit,
                           log=self.log.emit,
                           **self._kwargs)
            self.finished.emit(True, msg or "Hoàn tất.")
        except Exception as exc:  # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.finished.emit(False, f"Lỗi: {exc}")


# =============================================================================
#  Cửa sổ chính
# =============================================================================

class MainWindow(QMainWindow):
    """Cửa sổ chính của Tick Data Manager clone."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TDS-Clone — Tick Data Manager")
        self.resize(880, 640)

        # Tick Store dùng chung cho mọi tab (đường dẫn mặc định "data").
        self._data_dir = Path("data")
        self.store = TickStore(self._data_dir)

        self._thread: QThread | None = None
        self._worker: Worker | None = None

        # --- Tabs ---
        tabs = QTabWidget()
        tabs.addTab(self._build_download_tab(), "1. Download")
        tabs.addTab(self._build_convert_tab(), "2. Build FXT")
        tabs.addTab(self._build_coverage_tab(), "3. Coverage")
        tabs.addTab(self._build_log_tab(), "4. Log")
        self.setCentralWidget(tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(f"Tick Store: {self._data_dir.resolve()}")

    # ----- Tab 1: Download ---------------------------------------------

    def _build_download_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)

        form_box = QGroupBox("Tải tick-data (Dukascopy)")
        form = QFormLayout(form_box)

        self.dl_symbol = QComboBox()
        self.dl_symbol.setEditable(True)              # cho gõ symbol lạ
        self.dl_symbol.addItems(known_symbols())
        form.addRow("Symbol:", self.dl_symbol)

        # Mặc định: 7 ngày gần nhất.
        today = QDate.currentDate()
        self.dl_from = QDateEdit(today.addDays(-7))
        self.dl_from.setCalendarPopup(True)
        self.dl_to = QDateEdit(today)
        self.dl_to.setCalendarPopup(True)
        form.addRow("Từ ngày (UTC):", self.dl_from)
        form.addRow("Đến ngày (UTC):", self.dl_to)

        self.dl_workers = QSpinBox()
        self.dl_workers.setRange(1, 32)
        self.dl_workers.setValue(8)
        form.addRow("Số luồng tải:", self.dl_workers)

        root.addWidget(form_box)

        self.dl_button = QPushButton("⬇  Tải về & nạp vào Tick Store")
        self.dl_button.clicked.connect(self._on_download)
        root.addWidget(self.dl_button)

        self.dl_progress = QProgressBar()
        root.addWidget(self.dl_progress)

        self.dl_log = QPlainTextEdit()
        self.dl_log.setReadOnly(True)
        root.addWidget(self.dl_log, 1)
        return w

    def _on_download(self) -> None:
        symbol = self.dl_symbol.currentText().strip().upper()
        start = self._qdate_to_dt(self.dl_from.date())
        end = self._qdate_to_dt(self.dl_to.date())
        if end <= start:
            self._warn("Ngày 'đến' phải sau ngày 'từ'.")
            return
        workers = self.dl_workers.value()

        def task(progress, log):
            log(f"Bắt đầu tải {symbol} {start:%Y-%m-%d}..{end:%Y-%m-%d}")
            report = download_range(symbol, start, end, self.store,
                                    max_workers=workers, progress=progress)
            log(report.summary())
            return report.summary()

        self._run_task(task, button=self.dl_button, progress=self.dl_progress,
                       log_widget=self.dl_log,
                       on_done=lambda ok, msg: self._refresh_coverage())

    # ----- Tab 2: Build FXT --------------------------------------------

    def _build_convert_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)

        box = QGroupBox("Build FXT / HST cho MT4 Strategy Tester")
        form = QFormLayout(box)

        self.cv_symbol = QComboBox()
        self.cv_symbol.setEditable(True)
        self.cv_symbol.addItems(known_symbols())
        form.addRow("Symbol:", self.cv_symbol)

        self.cv_period = QComboBox()
        for p, label in [(1, "M1"), (5, "M5"), (15, "M15"), (30, "M30"),
                         (60, "H1"), (240, "H4"), (1440, "D1")]:
            self.cv_period.addItem(label, p)
        form.addRow("Timeframe:", self.cv_period)

        today = QDate.currentDate()
        self.cv_from = QDateEdit(today.addDays(-7))
        self.cv_from.setCalendarPopup(True)
        self.cv_to = QDateEdit(today)
        self.cv_to.setCalendarPopup(True)
        form.addRow("Từ ngày (UTC):", self.cv_from)
        form.addRow("Đến ngày (UTC):", self.cv_to)

        # --- Cấu hình Spread Model ---
        self.cv_spread = QComboBox()
        self.cv_spread.addItems(["real", "fixed", "random", "session", "news"])
        self.cv_spread.currentTextChanged.connect(self._on_spread_changed)
        form.addRow("Spread model:", self.cv_spread)

        # Tham số động (hiện/ẩn theo model).
        self.cv_points = QDoubleSpinBox()
        self.cv_points.setRange(0, 1000)
        self.cv_points.setValue(12.0)
        self.cv_points_label = QLabel("Points (fixed/max):")
        form.addRow(self.cv_points_label, self.cv_points)

        self.cv_min_points = QDoubleSpinBox()
        self.cv_min_points.setRange(0, 1000)
        self.cv_min_points.setValue(0.0)
        self.cv_min_label = QLabel("Min points:")
        form.addRow(self.cv_min_label, self.cv_min_points)

        self.cv_multiplier = QDoubleSpinBox()
        self.cv_multiplier.setRange(0.1, 100)
        self.cv_multiplier.setSingleStep(0.1)
        self.cv_multiplier.setValue(1.0)
        self.cv_mult_label = QLabel("Multiplier:")
        form.addRow(self.cv_mult_label, self.cv_multiplier)

        # --- Output ---
        out_row = QHBoxLayout()
        self.cv_out = QLineEdit("out")
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse_out)
        out_row.addWidget(self.cv_out)
        out_row.addWidget(browse)
        out_widget = QWidget()
        out_widget.setLayout(out_row)
        form.addRow("Thư mục xuất:", out_widget)

        self.cv_hst = QCheckBox("Sinh kèm .hst (chart)")
        self.cv_hst.setChecked(True)
        self.cv_spread_file = QCheckBox("Sinh kèm .tdspread (cho EA DLL / C2)")
        self.cv_spread_file.setChecked(True)
        form.addRow("", self.cv_hst)
        form.addRow("", self.cv_spread_file)

        root.addWidget(box)

        self.cv_button = QPushButton("⚙  Build FXT")
        self.cv_button.clicked.connect(self._on_build)
        root.addWidget(self.cv_button)

        self.cv_log = QPlainTextEdit()
        self.cv_log.setReadOnly(True)
        root.addWidget(self.cv_log, 1)

        self._on_spread_changed(self.cv_spread.currentText())  # set hiển thị ban đầu
        return w

    def _on_spread_changed(self, kind: str) -> None:
        """Hiện/ẩn tham số theo loại spread model đang chọn."""
        is_real = kind == "real"
        is_fixed = kind == "fixed"
        is_random = kind == "random"
        # points: fixed (cố định) hoặc random (max)
        self._set_row_visible(self.cv_points_label, self.cv_points, is_fixed or is_random)
        # min_points: real hoặc random
        self._set_row_visible(self.cv_min_label, self.cv_min_points, is_real or is_random)
        # multiplier: chỉ real
        self._set_row_visible(self.cv_mult_label, self.cv_multiplier, is_real)

    @staticmethod
    def _set_row_visible(label: QWidget, field: QWidget, visible: bool) -> None:
        label.setVisible(visible)
        field.setVisible(visible)

    def _on_build(self) -> None:
        symbol = self.cv_symbol.currentText().strip().upper()
        period = self.cv_period.currentData()
        start = self._qdate_to_dt(self.cv_from.date())
        end = self._qdate_to_dt(self.cv_to.date())
        if end <= start:
            self._warn("Ngày 'đến' phải sau ngày 'từ'.")
            return

        kind = self.cv_spread.currentText()
        params = {
            "points": self.cv_points.value(),
            "min_points": self.cv_min_points.value(),
            "max_points": self.cv_points.value(),
            "multiplier": self.cv_multiplier.value(),
        }
        out_dir = self.cv_out.text().strip() or "out"
        also_hst = self.cv_hst.isChecked()
        also_sf = self.cv_spread_file.isChecked()

        def task(progress, log):
            log(f"Build {symbol} M{period} spread={kind} ...")
            model = make_spread_model(kind, **params)
            result = build_fxt(symbol, period, start, end, self.store,
                               spread_model=model, out_dir=out_dir,
                               also_hst=also_hst, also_spread_file=also_sf)
            log(f"FXT: {result.fxt_path} ({result.n_ticks:,} tick)")
            if result.hst_path:
                log(f"HST: {result.hst_path}")
            if result.spread_path:
                log(f"Spread: {result.spread_path}")
            return f"Đã build {result.fxt_path.name}"

        self._run_task(task, button=self.cv_button, progress=None,
                       log_widget=self.cv_log)

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất", self.cv_out.text())
        if d:
            self.cv_out.setText(d)

    # ----- Tab 3: Coverage ---------------------------------------------

    def _build_coverage_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)

        refresh = QPushButton("🔄  Làm mới coverage")
        refresh.clicked.connect(self._refresh_coverage)
        root.addWidget(refresh)

        self.cov_table = QTableWidget(0, 4)
        self.cov_table.setHorizontalHeaderLabels(["Symbol", "Từ", "Đến", "Số tick"])
        self.cov_table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.cov_table, 1)
        self._refresh_coverage()
        return w

    def _refresh_coverage(self) -> None:
        self.cov_table.setRowCount(0)
        for symbol in self.store.symbols():
            for r in self.store.coverage(symbol):
                row = self.cov_table.rowCount()
                self.cov_table.insertRow(row)
                self.cov_table.setItem(row, 0, QTableWidgetItem(symbol))
                self.cov_table.setItem(row, 1, QTableWidgetItem(f"{r.start:%Y-%m-%d %H:%M}"))
                self.cov_table.setItem(row, 2, QTableWidgetItem(f"{r.end:%Y-%m-%d %H:%M}"))
                self.cov_table.setItem(row, 3, QTableWidgetItem(f"{r.ticks:,}"))

    # ----- Tab 4: Log ---------------------------------------------------

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        self.global_log = QPlainTextEdit()
        self.global_log.setReadOnly(True)
        root.addWidget(QLabel("Log toàn cục:"))
        root.addWidget(self.global_log, 1)
        return w

    # ----- Hạ tầng chạy task nền ----------------------------------------

    def _run_task(self, task_fn, *, button: QPushButton, progress: QProgressBar | None,
                  log_widget: QPlainTextEdit, on_done=None) -> None:
        """
        Chạy ``task_fn(progress, log)`` trong QThread, khoá nút trong lúc chạy,
        nối signal về cập nhật progress/log/trạng thái.
        """
        if self._thread is not None:
            self._warn("Đang có tác vụ chạy, vui lòng đợi.")
            return

        button.setEnabled(False)
        if progress:
            progress.setRange(0, 0)  # indeterminate cho tới khi có total

        self._thread = QThread()
        self._worker = Worker(task_fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        def on_progress(done: int, total: int) -> None:
            if progress:
                progress.setRange(0, total)
                progress.setValue(done)

        def on_log(text: str) -> None:
            log_widget.appendPlainText(text)
            self.global_log.appendPlainText(text)

        def on_finished(ok: bool, msg: str) -> None:
            on_log(msg)
            self.statusBar().showMessage(msg, 8000)
            button.setEnabled(True)
            if progress:
                progress.setRange(0, 1)
                progress.setValue(1 if ok else 0)
            # Dọn thread.
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None
            if on_done:
                on_done(ok, msg)
            if not ok:
                self._warn(msg)

        self._worker.progress.connect(on_progress)
        self._worker.log.connect(on_log)
        self._worker.finished.connect(on_finished)
        self._thread.start()

    # ----- tiện ích -----------------------------------------------------

    @staticmethod
    def _qdate_to_dt(qd: "QDate") -> datetime:
        return datetime(qd.year(), qd.month(), qd.day(), tzinfo=timezone.utc)

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "TDS-Clone", text)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.store.close()
        super().closeEvent(event)


def main(argv=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
