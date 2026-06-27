"""
Module 6 — GUI / Orchestrator (PySide6).

Tái hiện trải nghiệm "Tick Data Manager" của TDS: quản lý symbol, tải data, build
FXT/HST, cấu hình spread model, theo dõi job. Xem :mod:`tdsclone.gui.app`.

PySide6 là phụ thuộc TUỲ CHỌN — cài bằng: ``pip install "tdsclone[gui]"``.
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:
    # Import trễ để gói lõi không phụ thuộc PySide6.
    from tdsclone.gui.app import main as _main
    return _main(argv)
