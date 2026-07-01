"""
TDS Clone Service — vai tro giong TDSLoader + TDSService cua TDS that.

Chuc nang:
  1. WATCH tien trinh terminal.exe (MT4). Khi MT4 mo -> TU DONG inject tdshook.dll
     (goi injector.exe). Idempotent: moi PID chi inject 1 lan.
  2. GIU shared memory song cho symbol dang active (doc settings_store), refresh khi
     symbol/settings doi.
  3. Chay headless (--headless) hoac co tray icon (mac dinh, dung PySide6).

Chay:
  python tds_service.py                 # tray icon
  python tds_service.py --headless      # chay nen, in log ra console
  python tds_service.py --symbol XAUUSD --mode tick

Yeu cau: chay AS ADMINISTRATOR de inject duoc (OpenProcess PROCESS_ALL_ACCESS).
"""

import os
import sys
import time
import argparse
import threading
import subprocess

sys.path.insert(0, os.path.dirname(__file__))

import settings_store
import shm_writer

ROOT = os.path.dirname(os.path.dirname(__file__))

# Cac vi tri kha di cua injector.exe / tdshook.dll sau khi build CMake
INJECTOR_CANDIDATES = [
    os.path.join(ROOT, "native", "build", "injector", "Release", "injector.exe"),
    os.path.join(ROOT, "native", "build", "injector", "Debug", "injector.exe"),
    os.path.join(ROOT, "native", "build", "Release", "injector.exe"),
]
HOOK_CANDIDATES = [
    os.path.join(ROOT, "native", "build", "hook", "Release", "tdshook.dll"),
    os.path.join(ROOT, "native", "build", "hook", "Debug", "tdshook.dll"),
    os.path.join(ROOT, "native", "build", "Release", "tdshook.dll"),
]


def _first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def find_mt4_pids():
    """Tra list PID cua cac tien trinh terminal.exe dang chay."""
    import ctypes
    from ctypes import wintypes
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return []
    pids = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    if k32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            if entry.szExeFile.lower() == "terminal.exe":
                pids.append(entry.th32ProcessID)
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
    k32.CloseHandle(snap)
    return pids


# ---------------------------------------------------------------------------
# Service core
# ---------------------------------------------------------------------------
class TDSService:
    def __init__(self, log=print):
        self.log = log
        self.injected = set()          # PID da inject
        self.holder = None             # ShmHolder giu SHM song
        self.active_symbol = settings_store.get_state("active_symbol", "EURUSD")
        self.active_mode = settings_store.get_state("active_mode", "spread")
        self._stop = threading.Event()
        self._thread = None
        self.injector = _first_existing(INJECTOR_CANDIDATES)
        self.hook = _first_existing(HOOK_CANDIDATES)

    # ---- Shared memory ----
    # Neu store > nguong nay ma KHONG biet khoang test -> bo qua spread SHM
    # (tranh dung list hang chuc trieu record lam treo/het RAM).
    MAX_TICKS_NO_RANGE = 3_000_000

    def publish(self, symbol=None, mode=None):
        """Publish SHM cho symbol/mode. Chi build KHOANG TEST (active_from/to) neu co."""
        symbol = (symbol or self.active_symbol).upper()
        mode = mode or self.active_mode
        try:
            import tick_store
            # Khoang test do MT4 (qua 'prepare') ghi vao app_state, neu co
            fm = settings_store.get_state("active_from_ms")
            tm = settings_store.get_state("active_to_ms")
            from_ms = int(fm) if fm else None
            to_ms = int(tm) if tm else None

            if from_ms is None and mode == "spread":
                cov = tick_store.coverage(symbol)
                if cov and cov[2] > self.MAX_TICKS_NO_RANGE:
                    self.log(f"[SHM] {symbol}: store lon ({cov[2]:,} ticks) va chua biet "
                             f"khoang test -> CHUA publish spread. Trong MT4 tick "
                             f"'Use my tick data' de bao khoang test.")
                    # Van publish slippage params (nho)
                    if self.holder:
                        self.holder.close()
                    from shm_writer import ShmHolder, build_slippage, SHM_SLIPPAGE
                    self.holder = ShmHolder()
                    self.holder.publish(SHM_SLIPPAGE, build_slippage(symbol))
                    self.active_symbol, self.active_mode = symbol, mode
                    return True

            if self.holder:
                self.holder.close()
            rng = ""
            if from_ms:
                import datetime as _dt
                rng = (f" [{_dt.datetime.utcfromtimestamp(from_ms/1000).date()}"
                       f"..{_dt.datetime.utcfromtimestamp(to_ms/1000).date()}]")
            self.log(f"[SHM] Dang build {symbol} mode={mode}{rng}...")
            self.holder, info = shm_writer.publish_for_symbol(
                symbol, mode=mode, from_ms=from_ms, to_ms=to_ms)
            self.active_symbol, self.active_mode = symbol, mode
            settings_store.set_state("active_symbol", self.active_symbol)
            settings_store.set_state("active_mode", self.active_mode)
            self.log(f"[SHM] {symbol} mode={mode}: {info}")
            return True
        except Exception as e:
            self.log(f"[SHM][LOI] {symbol}: {e}")
            return False

    def publish_bg(self, **kw):
        """Publish trong thread nen (khong chan vong inject)."""
        threading.Thread(target=lambda: self.publish(**kw), daemon=True).start()

    # ---- Injection ----
    def inject(self, pid):
        if not self.injector or not self.hook:
            self.log("[INJECT][LOI] Chua build injector.exe/tdshook.dll. "
                     "Chay: cmake --build native\\build --config Release")
            return False
        try:
            r = subprocess.run([self.injector, "--pid", str(pid), self.hook],
                               capture_output=True, text=True, timeout=30)
            ok = r.returncode == 0
            self.log(f"[INJECT] PID {pid}: {'OK' if ok else 'LOI'} "
                     f"{(r.stdout or r.stderr).strip()[:200]}")
            return ok
        except Exception as e:
            self.log(f"[INJECT][LOI] PID {pid}: {e}")
            return False

    # ---- Watcher loop ----
    def _loop(self):
        self.log(f"[SVC] Bat dau. injector={self.injector}  hook={self.hook}")
        if not (self.injector and self.hook):
            self.log("[SVC][CANH BAO] Thieu binary native — chi giu SHM, KHONG auto-inject.")
        # Publish chay NEN -> KHONG chan vong inject (quan trong: store BTC rat lon)
        self.publish_bg()
        # Theo doi khoang test (active_from/to) de republish khi doi
        last_range = (settings_store.get_state("active_from_ms"),
                      settings_store.get_state("active_to_ms"))
        while not self._stop.is_set():
            pids = set(find_mt4_pids())
            # MT4 moi -> inject NGAY
            for pid in pids - self.injected:
                self.log(f"[SVC] Phat hien MT4 PID {pid} -> dang inject tdshook.dll...")
                if self.inject(pid):
                    self.injected.add(pid)
            # MT4 dong -> quen di (de lan sau mo lai inject lai)
            self.injected &= pids

            # MT4 (qua 'prepare') co the doi symbol/khoang -> republish SHM (nen).
            want_sym = settings_store.get_state("active_symbol", self.active_symbol)
            want_mode = settings_store.get_state("active_mode", self.active_mode)
            want_range = (settings_store.get_state("active_from_ms"),
                          settings_store.get_state("active_to_ms"))
            if (want_sym, want_mode) != (self.active_symbol, self.active_mode) \
                    or want_range != last_range:
                last_range = want_range
                self.log(f"[SVC] Active doi -> {want_sym} ({want_mode}), republish (nen)")
                self.publish_bg(symbol=want_sym, mode=want_mode)

            self._stop.wait(2.0)
        if self.holder:
            self.holder.close()
        self.log("[SVC] Dung.")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Tray UI (PySide6) — khong can pystray
# ---------------------------------------------------------------------------
def run_tray(svc: TDSService):
    from PySide6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu,
                                   QStyle, QInputDialog)
    from PySide6.QtGui import QAction

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon = app.style().standardIcon(QStyle.SP_ComputerIcon)
    tray = QSystemTrayIcon(icon)
    tray.setToolTip("TDS Clone Service")
    menu = QMenu()

    status = QAction("Service: dang chay"); status.setEnabled(False)
    menu.addAction(status)
    menu.addSeparator()

    def show_status():
        status.setText(f"Active: {svc.active_symbol} ({svc.active_mode}) | "
                       f"MT4 injected: {len(svc.injected)}")

    act_sym = QAction("Doi symbol active...")
    def change_symbol():
        sym, ok = QInputDialog.getText(None, "Symbol active",
                                       "Symbol:", text=svc.active_symbol)
        if ok and sym.strip():
            svc.publish(symbol=sym.strip().upper())
            show_status()
    act_sym.triggered.connect(change_symbol)
    menu.addAction(act_sym)

    act_republish = QAction("Publish lai SHM")
    act_republish.triggered.connect(lambda: (svc.publish(), show_status()))
    menu.addAction(act_republish)

    menu.addSeparator()
    act_quit = QAction("Thoat")
    def quit_all():
        svc.stop(); tray.hide(); app.quit()
    act_quit.triggered.connect(quit_all)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.aboutToShow = show_status
    menu.aboutToShow.connect(show_status)
    tray.show()
    tray.showMessage("TDS Clone", "Service dang chay — tu inject MT4 khi mo.",
                     QSystemTrayIcon.Information, 3000)

    svc.start()
    sys.exit(app.exec())


def main():
    ap = argparse.ArgumentParser(description="TDS Clone Service")
    ap.add_argument("--headless", action="store_true", help="Chay nen khong tray")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--mode", choices=["spread", "tick"], default=None)
    args = ap.parse_args()

    svc = TDSService()
    if args.symbol:
        svc.active_symbol = args.symbol.upper()
    if args.mode:
        svc.active_mode = args.mode

    if args.headless:
        svc.start()
        print("[SVC] Headless. Ctrl+C de thoat.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            svc.stop()
    else:
        try:
            run_tray(svc)
        except Exception as e:
            print(f"[!] Khong mo duoc tray ({e}) -> chay headless.")
            svc.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                svc.stop()


if __name__ == "__main__":
    main()
