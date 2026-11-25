#!/usr/bin/env python3
"""
axml_encode.py - compile a plain-text AndroidManifest.xml into Android binary XML (AXML).

Replaces aapt2 for simple manifests (no resource references), since Google ships aapt2 only
for x86_64. Attribute resource IDs are read from android.jar (android.R$attr, via javap).
Value types: "true"/"false" -> boolean, decimal -> int, 0x.. -> hex int, anything else -> string.

Usage: axml_encode.py <AndroidManifest.xml> <android.jar> <javap> <out.bin>
"""
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
NONE = 0xFFFFFFFF


def u16(v): return struct.pack("<H", v & 0xFFFF)
def u32(v): return struct.pack("<I", v & 0xFFFFFFFF)


def string_pool(strings):
    """UTF-16 ResStringPool chunk (used by binary XML and resources.arsc)."""
    data, offsets = bytearray(), []
    for s in strings:
        offsets.append(len(data))
        enc = s.encode("utf-16le")
        n = len(enc) // 2
        data += (u16(0x8000 | (n >> 16)) + u16(n)) if n > 0x7FFF else u16(n)
        data += enc + b"\0\0"
    while len(data) % 4:
        data += b"\0"
    start = 28 + 4 * len(strings)
    return (u16(0x0001) + u16(28) + u32(start + len(data)) + u32(len(strings)) + u32(0) + u32(0)
            + u32(start) + u32(0) + b"".join(u32(o) for o in offsets) + bytes(data))


def attr_ids(android_jar, javap):
    out = subprocess.run([javap, "-constants", "-cp", android_jar, "android.R$attr"],
                         capture_output=True, text=True, check=True).stdout
    return {m[1]: int(m[2]) for m in re.finditer(r"public static final int (\w+) = (-?\d+);", out)}


def split(tag):
    if tag.startswith("{"):
        ns, name = tag[1:].split("}", 1)
        return ns, name
    return None, tag


def encode(root, ids):
    # Android-namespace attribute names go first in the string pool, sorted by resource ID,
    # so the resource map (string index -> attribute ID) lines up with them.
    used = set()
    for el in root.iter():
        for k in el.attrib:
            ns, name = split(k)
            if ns == ANDROID_NS:
                if name not in ids:
                    raise SystemExit(f"unknown android attribute: {name}")
                used.add(name)
    mapped = sorted(used, key=lambda n: ids[n])
    pool, index = [], {}

    def idx(s):
        if s not in index:
            index[s] = len(pool)
            pool.append(s)
        return index[s]

    for n in mapped:
        idx(n)
    idx("android")
    idx(ANDROID_NS)

    def typed(value):
        if value in ("true", "false"):
            return 0x12, NONE if value == "true" else 0, NONE
        if re.fullmatch(r"@0x[0-9a-fA-F]{8}", value):  # resource reference, e.g. @0x7f010000
            return 0x01, int(value[1:], 16), NONE
        if re.fullmatch(r"-?\d+", value):
            return 0x10, int(value), NONE
        if re.fullmatch(r"0x[0-9a-fA-F]+", value):
            return 0x11, int(value, 16), NONE
        i = idx(value)
        return 0x03, i, i

    body, line = [], [1]

    def walk(el):
        line[0] += 1
        attrs = []
        for k, v in el.attrib.items():
            ns, name = split(k)
            dtype, data, raw = typed(v)
            key = (0, ids[name]) if ns == ANDROID_NS else (1, name)
            attrs.append((key, ns, name, dtype, data, raw))
        attrs.sort(key=lambda a: a[0])  # Android's attribute lookup expects ID order
        chunk = bytearray()
        chunk += u32(NONE) + u32(idx(el.tag)) + u16(20) + u16(20) + u16(len(attrs)) + u16(0) * 3
        for _, ns, name, dtype, data, raw in attrs:
            chunk += u32(idx(ANDROID_NS) if ns == ANDROID_NS else NONE) + u32(idx(name))
            chunk += u32(raw) + u16(8) + bytes([0, dtype]) + u32(data)
        body.append(u16(0x0102) + u16(16) + u32(16 + len(chunk)) + u32(line[0]) + u32(NONE) + chunk)
        for child in el:
            walk(child)
        body.append(u16(0x0103) + u16(16) + u32(24) + u32(line[0]) + u32(NONE) + u32(NONE) + u32(idx(el.tag)))

    walk(root)

    strpool = string_pool(pool)
    resmap = (u16(0x0180) + u16(8) + u32(8 + 4 * len(mapped)) + b"".join(u32(ids[n]) for n in mapped)
              if mapped else b"")
    ns_start = u16(0x0100) + u16(16) + u32(24) + u32(1) + u32(NONE) + u32(idx("android")) + u32(idx(ANDROID_NS))
    ns_end = u16(0x0101) + u16(16) + u32(24) + u32(line[0]) + u32(NONE) + u32(idx("android")) + u32(idx(ANDROID_NS))
    # the pool was finished while walking, so build it after the body
    doc = strpool + resmap + ns_start + b"".join(body) + ns_end
    return u16(0x0003) + u16(8) + u32(8 + len(doc)) + doc


def main():
    src, android_jar, javap, out = sys.argv[1:5]
    root = ET.parse(src).getroot()
    blob = encode(root, attr_ids(android_jar, javap))
    with open(out, "wb") as f:
        f.write(blob)
    print(f"wrote {out} ({len(blob)} bytes)")


if __name__ == "__main__":
    main()
