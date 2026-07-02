"""
tick_store — SHIM. Kho tick gio la `daystore` (file nen mot-file-moi-NGAY .tkd +
catalog SQLite), clone KIEN TRUC cua Tick Data Suite. Xem daystore.py.

Giu nguyen ten module 'tick_store' de MOI consumer (fxt_builder, shm_writer,
hst_builder, fxt_virtual, tds_service, tds_gui, build_*_fxt) khong phai sua import.

Backup ban cu (.tkz per-thang): tick_store_tkz_backup.py
"""

from daystore import (  # noqa: F401
    # --- ghi ---
    append_day, save_month,
    # --- doc ---
    load_month, load_all, load_range, load_range_np, iter_all, iter_range,
    # --- metadata ---
    month_last_ms, month_counts, coverage, has_month, _month_list,
    # --- quan ly ---
    delete_symbol, list_symbols, store_dir, close_all,
    # --- native (bung scratch .bin) ---
    month_file, materialize_paths, clean_scratch,
    # --- tuong thich GUI cu (kho .tkd luon nen -> no-op) ---
    is_compressed, compress_month, compress_symbol,
    # --- migration + hang so ---
    migrate_all, MS_PER_DAY, REC,
)
