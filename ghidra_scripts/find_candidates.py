# find_candidates.py — Ghidra script (Jython 2.7)
# ============================================================================
#  Module 5C — khoanh vùng hàm ứng viên "trả giá trong tester" của terminal.exe
#  rồi SINH AOB signature -> signatures.json cho SigScanner (5C, C++).
#  TDS_CLONE_ARCHITECTURE.md — mục 5.3 (ii).
# ----------------------------------------------------------------------------
#  CHẠY: mở terminal.exe trong Ghidra (đã auto-analyze) -> Window > Script
#        Manager -> chạy script này. Output: signatures.json cạnh file chương trình.
#
#  QUY TRÌNH (mục 5.3):
#    1. Liệt kê hàm tham chiếu string liên quan: "Bid","Ask","spread","TestGenerator"...
#    2. Với mỗi ứng viên, trích N byte đầu hàm -> sinh AOB signature, MASK (??) các
#       byte là địa chỉ tương đối/relocation để signature bền qua nhiều build.
#    3. Xuất signatures.json.
#
#  ⚠️ Script CHỈ KHOANH VÙNG ứng viên. Việc xác định ĐÚNG hàm (hàm bắn mỗi tick)
#     là phán đoán RE của bạn — verify bằng x64dbg (đặt breakpoint, chạy backtest,
#     xem nó có bắn mỗi tick không). Không tool nào auto 100% (mục 5.3 "giới hạn").
# ============================================================================
# @category TDS-Clone
# @runtime Jython

import json

from ghidra.program.model.symbol import RefType
from ghidra.util.task import ConsoleTaskMonitor

# Từ khoá string để neo (anchor). Bổ sung theo bản MT4 của bạn.
ANCHOR_KEYWORDS = ["Bid", "Ask", "spread", "Spread", "TestGenerator",
                   "tester", "Tester", "quote", "Quote"]

SIG_LEN = 40   # số byte đầu hàm dùng để sinh signature (đủ đặc trưng)


def _program():
    return getCurrentProgram()  # noqa: F821 (Ghidra injects)


def find_string_anchored_functions():
    """Trả set các Function chứa/được tham chiếu bởi string anchor."""
    prog = _program()
    listing = prog.getListing()
    fm = prog.getFunctionManager()
    monitor = ConsoleTaskMonitor()
    found = {}

    # Duyệt mọi defined data, lọc string khớp keyword, lần xref tới hàm chứa nó.
    data_it = listing.getDefinedData(True)
    while data_it.hasNext():
        data = data_it.next()
        if not data.hasStringValue():
            continue
        sval = str(data.getValue())
        if not any(k in sval for k in ANCHOR_KEYWORDS):
            continue
        # Tìm các tham chiếu TỚI string này -> hàm chứa lệnh tham chiếu.
        refs = getReferencesTo(data.getAddress())  # noqa: F821
        for ref in refs:
            from_addr = ref.getFromAddress()
            fn = fm.getFunctionContaining(from_addr)
            if fn is not None:
                found[fn.getEntryPoint()] = (fn, sval)
    return found


def make_signature(func):
    """
    Sinh AOB signature từ N byte đầu hàm. MASK (??) các byte nằm trong operand là
    địa chỉ tương đối (call/jmp rel32, ద displacement) để bền qua relocation.
    Trả (pattern_str, n_bytes) hoặc (None, 0) nếu không đọc được.
    """
    prog = _program()
    mem = prog.getMemory()
    listing = prog.getListing()
    start = func.getEntryPoint()

    pattern = []
    addr = start
    read = 0
    while read < SIG_LEN:
        instr = listing.getInstructionAt(addr)
        if instr is None:
            break
        length = instr.getLength()
        # Đọc byte thô của lệnh.
        bts = bytearray(length)
        mem.getBytes(addr, bts)

        # Heuristic mask: nếu lệnh là call/jmp dùng địa chỉ tương đối, mask các
        # byte operand (thường 4 byte cuối của lệnh rel32). Đơn giản hoá: mask
        # mọi byte sau opcode đầu nếu mnemonic là CALL/JMP gần.
        mnem = instr.getMnemonicString().upper()
        is_rel = mnem in ("CALL", "JMP") and length >= 5

        for i in range(length):
            if read >= SIG_LEN:
                break
            if is_rel and i >= 1:
                pattern.append("??")          # operand địa chỉ tương đối -> wildcard
            else:
                pattern.append("%02X" % (bts[i] & 0xFF))
            read += 1
        addr = addr.add(length)

    if not pattern:
        return None, 0
    return " ".join(pattern), len(pattern)


def main():
    candidates = find_string_anchored_functions()
    out = []
    for entry, (fn, anchor_str) in candidates.items():
        pattern, n = make_signature(fn)
        if pattern is None:
            continue
        out.append({
            "name": fn.getName(),
            "entry": str(fn.getEntryPoint()),
            "anchor_string": anchor_str,
            "pattern": pattern,
            "bytes": n,
        })

    # Ghi signatures.json cạnh chương trình.
    prog = _program()
    exe_path = prog.getExecutablePath()
    import os
    out_path = os.path.join(os.path.dirname(exe_path), "signatures.json")
    with open(out_path, "w") as f:
        f.write("[\n")
        for i, rec in enumerate(out):
            line = json.dumps(rec)
            f.write("  " + line + ("," if i < len(out) - 1 else "") + "\n")
        f.write("]\n")

    print("Đã sinh %d ứng viên -> %s" % (len(out), out_path))
    print("BƯỚC TIẾP: verify hàm đúng bằng x64dbg (breakpoint, chạy backtest),")
    print("rồi dùng:  sigscan.exe --json signatures.json terminal.exe")


main()
