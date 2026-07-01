"""Trich chuoi (ASCII + UTF-16LE) tu binary TDS, loc cac chuoi lien quan HTTP/download."""
import re, sys, os

TDS = r"C:\Program Files (x86)\eareview.net\Tick Data Suite"
TARGETS = ["TDSManaged.dll", "Tick Data Manager.exe", "tdslib.dll", "tdsstor64.dll",
           "TDSService.exe", "TDSSupport.exe"]

KEYWORDS = re.compile(
    r"user-?agent|mozilla|curl|accept|cookie|referer|authorization|x-|"
    r"datafeed|dukascopy|\.bi5|h_ticks|/\{0\}|gzip|deflate|keep-alive|"
    r"http/|https?://|wininet|winhttp|httpclient|webclient|\{0\}.{0,6}\{1\}",
    re.IGNORECASE)

def strings_from(data, min_len=5):
    out = set()
    # ASCII
    for m in re.finditer(rb"[\x20-\x7E]{%d,}" % min_len, data):
        out.add(m.group().decode("ascii", "ignore"))
    # UTF-16LE
    for m in re.finditer((rb"(?:[\x20-\x7E]\x00){%d,}" % min_len), data):
        out.add(m.group().decode("utf-16le", "ignore"))
    return out

for name in TARGETS:
    path = None
    for root, _, files in os.walk(TDS):
        if name in files:
            path = os.path.join(root, name); break
    if not path:
        continue
    data = open(path, "rb").read()
    hits = sorted(s.strip() for s in strings_from(data) if KEYWORDS.search(s))
    # Loc bo cert/crl noise
    hits = [h for h in hits if not re.search(r"globalsign|ocsp|\.crl|cacert|schannel", h, re.I)]
    if hits:
        print(f"\n===== {name} ({len(data)//1024} KB) — {len(hits)} chuoi =====")
        for h in hits[:60]:
            print("  " + h[:160])
