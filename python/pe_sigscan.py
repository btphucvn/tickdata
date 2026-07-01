"""
pe_sigscan.py — thay thế Ghidra find_candidates.py
Phân tích terminal.exe trực tiếp (không cần Ghidra):
  1. Parse PE sections
  2. Tìm chuỗi anchor (Bid/Ask/spread/TestGenerator) trong .rdata/.data
  3. Tìm xref (lệnh push/mov imm32) trỏ tới address của chuỗi trong .text
  4. Tìm function entry point gần nhất (bằng prologue pattern)
  5. Xuất signatures.json (cùng format với find_candidates.py)
"""
import struct, json, re, sys
from pathlib import Path

# ── Cấu hình ────────────────────────────────────────────────────────────────
ANCHOR_KEYWORDS = [b"Bid", b"Ask", b"spread", b"Spread",
                   b"TestGenerator", b"tester", b"Tester", b"quote", b"Quote"]
SIG_LEN   = 32   # byte đầu hàm dùng làm signature
XREF_SCAN = True  # tìm xref imm32 trong .text

# ── Parse PE ────────────────────────────────────────────────────────────────
def parse_pe(data: bytes):
    dos_magic = struct.unpack_from("<H", data, 0)[0]
    assert dos_magic == 0x5A4D, "Không phải PE file"
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    assert struct.unpack_from("<I", data, pe_off)[0] == 0x4550, "Thiếu PE signature"
    machine = struct.unpack_from("<H", data, pe_off+4)[0]
    num_sec = struct.unpack_from("<H", data, pe_off+6)[0]
    opt_size= struct.unpack_from("<H", data, pe_off+20)[0]
    image_base = struct.unpack_from("<I", data, pe_off+52)[0]  # 32-bit

    sec_off = pe_off + 24 + opt_size
    sections = []
    for i in range(num_sec):
        o = sec_off + i*40
        name  = data[o:o+8].rstrip(b"\x00").decode("ascii", errors="replace")
        vsize = struct.unpack_from("<I", data, o+8)[0]
        rva   = struct.unpack_from("<I", data, o+12)[0]
        raw_s = struct.unpack_from("<I", data, o+16)[0]
        raw_o = struct.unpack_from("<I", data, o+20)[0]
        chars = struct.unpack_from("<I", data, o+36)[0]
        sections.append(dict(name=name, rva=rva, vsize=vsize,
                             raw_off=raw_o, raw_size=raw_s, chars=chars))
    return image_base, sections

def rva_to_raw(sections, rva):
    for s in sections:
        if s["rva"] <= rva < s["rva"] + s["vsize"]:
            return s["raw_off"] + (rva - s["rva"])
    return None

def raw_to_rva(sections, raw):
    for s in sections:
        if s["raw_off"] <= raw < s["raw_off"] + s["raw_size"]:
            return s["rva"] + (raw - s["raw_off"])
    return None

# ── Tìm strings ─────────────────────────────────────────────────────────────
def find_anchor_strings(data, image_base, sections):
    """Tìm địa chỉ VA của mọi chuỗi anchor trong các section data/rdata."""
    hits = {}  # va -> keyword
    data_secs = [s for s in sections
                 if any(s["name"].startswith(n) for n in (".rdata",".data",".text"))]
    for kw in ANCHOR_KEYWORDS:
        # Tìm cả UTF-8 và UTF-16LE
        for enc, pattern in [("utf8", kw), ("utf16", b"".join(bytes([b,0]) for b in kw))]:
            start = 0
            while True:
                idx = data.find(pattern, start)
                if idx < 0:
                    break
                rva = raw_to_rva(sections, idx)
                if rva is not None:
                    va = image_base + rva
                    if va not in hits:
                        hits[va] = kw.decode()
                start = idx + 1
    return hits  # {va: keyword_str}

# ── Tìm xref imm32 ──────────────────────────────────────────────────────────
def find_xrefs(data, image_base, sections, string_vas):
    """
    Scan .text cho mọi chuỗi 4-byte bằng một trong string_vas.
    Đây là heuristic cho push imm32 / mov reg,imm32 trỏ tới string.
    """
    text_secs = [s for s in sections if s["chars"] & 0x20]  # IMAGE_SCN_MEM_EXECUTE
    xref_map = {}  # raw_offset_in_text -> (va_of_string, keyword)
    va_list = list(string_vas.keys())

    for s in text_secs:
        seg = data[s["raw_off"]: s["raw_off"] + s["raw_size"]]
        for va, kw in string_vas.items():
            needle = struct.pack("<I", va)
            start = 0
            while True:
                idx = seg.find(needle, start)
                if idx < 0:
                    break
                raw = s["raw_off"] + idx
                xref_map[raw] = (va, kw)
                start = idx + 1
    return xref_map  # {raw_offset: (string_va, kw)}

# ── Tìm function entry gần nhất (prologue scan ngược) ───────────────────────
PROLOGS = [
    b"\x55\x8B\xEC",          # push ebp; mov ebp,esp  (phổ biến nhất MSVC x86)
    b"\x55\x89\xE5",          # push ebp; mov ebp,esp  (GCC)
    b"\x8B\xFF\x55\x8B\xEC",  # mov edi,edi; push ebp; mov ebp,esp (thêm debug stub)
]
MAX_BACK = 0x200  # quét ngược tối đa 512 byte

def find_function_entry(data, raw_xref):
    """Quét ngược từ raw_xref để tìm function prologue gần nhất."""
    lo = max(0, raw_xref - MAX_BACK)
    window = data[lo:raw_xref + 1]
    best = None
    for prolog in PROLOGS:
        idx = window.rfind(prolog)
        if idx >= 0:
            candidate = lo + idx
            if best is None or candidate > best:
                best = candidate
    return best  # raw offset của entry, hoặc None

# ── Sinh AOB signature ───────────────────────────────────────────────────────
def make_signature(data, entry_raw, sections, image_base):
    """Lấy SIG_LEN byte từ entry, mask các imm32 nằm trong vùng địa chỉ PE."""
    chunk = data[entry_raw: entry_raw + SIG_LEN]
    if len(chunk) < 8:
        return None
    # Scan từng offset: nếu 4 byte là VA hợp lệ → mask ??
    result = []
    i = 0
    while i < len(chunk):
        # Thử đọc imm32 tại i
        if i + 4 <= len(chunk):
            imm = struct.unpack_from("<I", chunk, i)[0]
            rva = imm - image_base
            if rva_to_raw(sections, rva) is not None and rva > 0x1000:
                result.append("?? ?? ?? ??")
                i += 4
                continue
        result.append("%02X" % chunk[i])
        i += 1
    return " ".join(result)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage: python pe_sigscan.py <terminal.exe> [out_signatures.json]")
        sys.exit(1)

    pe_path  = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else pe_path.parent / "signatures.json"
    data = pe_path.read_bytes()
    print(f"[+] Đọc {pe_path.name}: {len(data)//1024} KB")

    image_base, sections = parse_pe(data)
    print(f"[+] ImageBase=0x{image_base:08X}, {len(sections)} sections")
    for s in sections:
        print(f"    {s['name']:10s} RVA=0x{s['rva']:08X} raw=0x{s['raw_off']:08X} sz={s['raw_size']//1024}KB chars=0x{s['chars']:08X}")

    print("[+] Tìm anchor strings...")
    string_vas = find_anchor_strings(data, image_base, sections)
    print(f"    Tìm được {len(string_vas)} strings")
    for va, kw in list(string_vas.items())[:10]:
        print(f"    0x{va:08X} '{kw}'")
    if len(string_vas) > 10:
        print(f"    ... (và {len(string_vas)-10} nữa)")

    print("[+] Tìm xref trong .text...")
    xrefs = find_xrefs(data, image_base, sections, string_vas)
    print(f"    Tìm được {len(xrefs)} xref")

    print("[+] Tìm function entries + sinh signature...")
    seen_entries = set()
    results = []
    for raw_xref, (str_va, kw) in xrefs.items():
        entry_raw = find_function_entry(data, raw_xref)
        if entry_raw is None or entry_raw in seen_entries:
            continue
        seen_entries.add(entry_raw)

        entry_rva = raw_to_rva(sections, entry_raw)
        if entry_rva is None:
            continue

        sig = make_signature(data, entry_raw, sections, image_base)
        if sig is None:
            continue

        results.append({
            "name": f"sub_{entry_rva:08X}",
            "entry": f"0x{image_base + entry_rva:08X}",
            "entry_rva": f"0x{entry_rva:08X}",
            "anchor_string": kw,
            "pattern": sig,
            "bytes": SIG_LEN,
        })

    print(f"[+] Xuất {len(results)} ứng viên → {out_path}")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    print("\nBƯỚC TIẾP:")
    print("  1. Dùng signatures.json + x32dbg verify hàm đúng (đặt breakpoint, chạy backtest)")
    print("  2. Cập nhật TARGET_SIGNATURE trong native/hook/tdshook.cpp")
    print("  3. Build lại + inject")

if __name__ == "__main__":
    main()
