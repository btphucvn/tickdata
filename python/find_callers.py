"""find_callers.py — scan terminal.exe for all CALL xrefs into our 43 candidates.

No Ghidra needed. Works directly on the PE binary in O(n) time.
Outputs: callers.txt  (who calls each candidate)

Usage:
    python find_callers.py
"""

import struct, json, os, sys

BINARY  = r"C:\Program Files (x86)\Dukascopy MetaTrader 4\terminal.exe"
SIG_JSON = r"C:\Users\Phuc\Desktop\tickdata\ghidra_scripts\signatures.json"
OUT      = r"C:\Users\Phuc\Desktop\tickdata\python\callers.txt"
IMAGE_BASE = 0x00400000

# ── Load PE and map file offset <-> VA ────────────────────────────────────────
with open(BINARY, "rb") as f:
    pe_data = bytearray(f.read())

# Parse PE headers (minimal)
e_lfanew = struct.unpack_from("<I", pe_data, 0x3C)[0]
nt_off   = e_lfanew
# Optional Header starts at nt_off+4 (sig) + 20 (COFF) = nt_off+24
magic    = struct.unpack_from("<H", pe_data, nt_off + 24)[0]  # OptHdr.Magic
if magic == 0x10b:   # PE32
    img_base_off = nt_off + 24 + 28   # OptHdr+28 = ImageBase (PE32)
    sects_off    = nt_off + 24 + 224  # PE32 OptHdr is 224 bytes
elif magic == 0x20b: # PE32+
    img_base_off = nt_off + 24 + 24   # OptHdr+24 = ImageBase (PE32+, 8 bytes)
    sects_off    = nt_off + 24 + 240  # PE32+ OptHdr is 240 bytes
else:
    sys.exit("Unknown OptHdr magic: 0x%X" % magic)

declared_base = struct.unpack_from("<I", pe_data, img_base_off)[0]
print("Declared ImageBase: 0x%08X" % declared_base)

nsects = struct.unpack_from("<H", pe_data, nt_off + 6)[0]
sections = []
for i in range(nsects):
    s = sects_off + i * 40
    name      = pe_data[s:s+8].rstrip(b'\x00').decode('ascii', errors='replace')
    vsize     = struct.unpack_from("<I", pe_data, s + 8)[0]
    vaddr     = struct.unpack_from("<I", pe_data, s + 12)[0]   # RVA
    raw_size  = struct.unpack_from("<I", pe_data, s + 16)[0]
    raw_off   = struct.unpack_from("<I", pe_data, s + 20)[0]
    sections.append((name, vaddr, vsize, raw_off, raw_size))
    print("  Section %-8s  VA=0x%08X  RawOff=0x%06X  Size=0x%06X" % (
        name, declared_base + vaddr, raw_off, raw_size))

def va_to_file_off(va):
    rva = va - declared_base
    for name, sec_rva, sec_vsize, raw_off, raw_size in sections:
        if sec_rva <= rva < sec_rva + max(sec_vsize, raw_size):
            return raw_off + (rva - sec_rva)
    return None

def file_off_to_va(off):
    for name, sec_rva, sec_vsize, raw_off, raw_size in sections:
        if raw_off <= off < raw_off + raw_size:
            return declared_base + sec_rva + (off - raw_off)
    return None

# ── Load 43 candidate VAs ──────────────────────────────────────────────────────
with open(SIG_JSON) as f:
    sigs = json.load(f)

# Use 'entry' field — the actual Ghidra VA computed with ImageBase=0x00BB0C00.
# 'entry_rva' was wrong (subtracted 0x00400000 instead of 0x00BB0C00).
targets = {}
for s in sigs:
    va = int(s["entry"], 16)
    targets[va] = s["name"]
print("\n%d target functions loaded.\n" % len(targets))

# ── Scan entire .text section for CALL rel32 (E8 xx xx xx xx) ─────────────────
# A CALL to target_va from instruction at call_va:
#   opcode = E8, rel32 = target_va - (call_va + 5)
#   => target_va = call_va + 5 + rel32

callers = {va: [] for va in targets}

# Scan all code sections: .text + .cod0/.cod1/.cod2 (MT4 has multiple code segs)
CODE_NAMES = {'.text', 'CODE', '.cod0', '.cod1', '.cod2', '.code'}
text_sections = [(n, rva, vsz, ro, rs) for n, rva, vsz, ro, rs in sections
                 if n in CODE_NAMES or n.startswith('.cod')]
if not text_sections:
    text_sections = sections  # fallback: scan everything

for sec_name, sec_rva, sec_vsize, raw_off, raw_size in text_sections:
    scan_size = min(sec_vsize, raw_size)
    print("Scanning %s (%d KB)..." % (sec_name, scan_size // 1024))
    for i in range(scan_size - 4):
        if pe_data[raw_off + i] == 0xE8:   # CALL rel32
            rel32 = struct.unpack_from("<i", pe_data, raw_off + i + 1)[0]
            call_file_off = raw_off + i
            call_va = file_off_to_va(call_file_off)
            if call_va is None:
                continue
            target_va = call_va + 5 + rel32
            if target_va in targets:
                callers[target_va].append(call_va)

# ── Find parent function start for each caller ────────────────────────────────
def find_func_start(call_va):
    """Walk backward from call_va looking for common x86 prologues."""
    off = va_to_file_off(call_va)
    if off is None:
        return None
    for i in range(1, 0x2000):
        p = off - i
        if p < 0:
            break
        b = pe_data
        # PUSH EBP; MOV EBP, ESP  (55 8B EC)
        if b[p] == 0x55 and b[p+1] == 0x8B and b[p+2] == 0xEC:
            return file_off_to_va(p)
        # PUSH EBP; MOV EBP, ESP via SUB ESP or LEA  — some variants
        # Also: 55 89 E5 (GCC convention)
        if b[p] == 0x55 and b[p+1] == 0x89 and b[p+2] == 0xE5:
            return file_off_to_va(p)
    return None

# ── Report ─────────────────────────────────────────────────────────────────────
with open(OUT, "w") as f:
    f.write("Callers of the 43 candidate functions in terminal.exe\n")
    f.write("=" * 70 + "\n\n")
    total_callers = 0
    for va, name in sorted(targets.items()):
        clist = callers[va]
        f.write("%-40s @ 0x%08X  (%d callers)\n" % (name, va, len(clist)))
        for c in sorted(set(clist)):
            fstart = find_func_start(c)
            fstart_str = ("parent_func=0x%08X" % fstart) if fstart else "parent=?"
            f.write("    called from: 0x%08X  [%s]\n" % (c, fstart_str))
            total_callers += 1
        f.write("\n")
    f.write("Total caller-target pairs: %d\n" % total_callers)

print("Done. %d caller refs found -> %s" % (total_callers, OUT))
print("\nTop callers (addresses to investigate in Ghidra/decompiler):")
# Print candidates that have many unique callers
for va, name in sorted(targets.items(), key=lambda x: -len(set(callers[x[0]]))):
    uq = sorted(set(callers[va]))
    if uq:
        print("  %-35s : %d unique callers" % (name, len(uq)))
        for c in uq[:5]:
            print("    <- 0x%08X" % c)
        if len(uq) > 5:
            print("    ... (%d more)" % (len(uq) - 5))
