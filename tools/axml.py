"""Minimal Android binary XML (AXML) to text decoder. Usage: axml.py <apk> <entry>"""
import struct, sys, zipfile

def read_pool(d, off):
    _, hsz, csz = struct.unpack_from("<HHI", d, off)
    n, _, flags, sstart, _ = struct.unpack_from("<IIIII", d, off + 8)
    utf8 = flags & 0x100
    offs = struct.unpack_from(f"<{n}I", d, off + hsz)
    out = []
    for o in offs:
        p = off + sstart + o
        if utf8:
            l = d[p]; p += 2 if l & 0x80 else 1
            l = d[p]; ln = ((l & 0x7F) << 8 | d[p+1]) if l & 0x80 else l; p += 2 if l & 0x80 else 1
            out.append(d[p:p+ln].decode("utf-8", "replace"))
        else:
            ln = struct.unpack_from("<H", d, p)[0]; p += 2
            if ln & 0x8000: ln = ((ln & 0x7FFF) << 16) | struct.unpack_from("<H", d, p)[0]; p += 2
            out.append(d[p:p+ln*2].decode("utf-16le", "replace"))
    return out, off + csz

def decode(d):
    pool, off = [], 8
    depth, lines = 0, []
    s = lambda i: pool[i] if 0 <= i < len(pool) else ""
    while off < len(d):
        typ, hsz, csz = struct.unpack_from("<HHI", d, off)
        if typ == 0x0001: pool, _ = read_pool(d, off)
        elif typ == 0x0102:
            name = struct.unpack_from("<I", d, off + 20)[0]
            astart, asz, acount = struct.unpack_from("<HHH", d, off + 24)
            attrs = []
            for i in range(acount):
                a = off + 16 + astart + i * asz
                _, an, raw, _, _, dt, data = struct.unpack_from("<IIIHBBI", d, a)
                if raw != 0xFFFFFFFF: v = s(raw)
                elif dt == 0x03: v = s(data)
                elif dt == 0x12: v = "true" if data else "false"
                elif dt == 0x01: v = f"@0x{data:08x}"
                elif dt in (0x10, 0x11): v = str(data)
                else: v = f"0x{data:x}"
                attrs.append(f'{s(an)}="{v}"')
            lines.append("  " * depth + f"<{s(name)} " + " ".join(attrs) + ">"); depth += 1
        elif typ == 0x0103: depth -= 1
        off += csz
    return "\n".join(lines)

if __name__ == "__main__":
    z = zipfile.ZipFile(sys.argv[1])
    print(decode(z.read(sys.argv[2])))
