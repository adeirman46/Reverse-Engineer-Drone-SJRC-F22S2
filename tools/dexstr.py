"""Dump strings from all classes*.dex in an APK that match a regex. Usage: dexstr.py <apk> <regex>"""
import re, struct, sys, zipfile

def uleb(d, p):
    r = s = 0
    while True:
        b = d[p]; p += 1; r |= (b & 0x7F) << s; s += 7
        if not b & 0x80: return r, p

z = zipfile.ZipFile(sys.argv[1]); rx = re.compile(sys.argv[2])
for n in sorted(x for x in z.namelist() if re.fullmatch(r"classes\d*\.dex", x)):
    d = z.read(n); cnt, off = struct.unpack_from("<II", d, 0x38)
    for i in range(cnt):
        p = struct.unpack_from("<I", d, off + 4 * i)[0]
        _, p = uleb(d, p); e = d.index(b"\0", p)
        t = d[p:e].decode("utf-8", "replace")
        if rx.search(t): print(f"{n}: {t}")
