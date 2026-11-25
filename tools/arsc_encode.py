#!/usr/bin/env python3
"""
arsc_encode.py - write a minimal resources.arsc holding mipmap PNG entries (the launcher icon),
for building an APK without aapt2 (which has no ARM64 Linux build).

Usage: arsc_encode.py <package> <out.arsc> <zip path of PNG> [<zip path of PNG> ...]
Entry i becomes @mipmap/<file name without .png> = 0x7f01000i, density xxxhdpi (640), so the
PNGs must be 192 x 192 and stored in the APK under the given paths.
"""
import os
import struct
import sys

RES_STRING_POOL, RES_TABLE, RES_TABLE_PACKAGE = 0x0001, 0x0002, 0x0200
RES_TABLE_TYPE, RES_TABLE_TYPE_SPEC = 0x0201, 0x0202
TYPE_STRING = 0x03
CONFIG_DENSITY = 0x0100


def string_pool(strings):
    """ResStringPool, UTF-16: per string a u16 length, the characters and a u16 terminator."""
    data, offsets = b"", []
    for s in strings:
        offsets.append(len(data))
        assert len(s) < 0x8000
        data += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
    while len(data) % 4:
        data += b"\0"
    start = 28 + 4 * len(strings)
    header = struct.pack("<HHIIIIII", RES_STRING_POOL, 28, start + len(data), len(strings), 0, 0, start, 0)
    return header + b"".join(struct.pack("<I", o) for o in offsets) + data


def main():
    package, out, paths = sys.argv[1], sys.argv[2], sys.argv[3:]
    keys = [os.path.splitext(os.path.basename(p))[0] for p in paths]
    n = len(paths)

    type_pool = string_pool(["mipmap"])
    key_pool = string_pool(keys)
    spec = struct.pack("<HHIBBHI", RES_TABLE_TYPE_SPEC, 16, 16 + 4 * n, 1, 0, 0, n)
    spec += b"".join(struct.pack("<I", CONFIG_DENSITY) for _ in range(n))

    config = bytearray(64)
    struct.pack_into("<I", config, 0, 64)    # size of ResTable_config
    struct.pack_into("<H", config, 14, 640)  # density: xxxhdpi
    struct.pack_into("<H", config, 24, 4)    # sdkVersion 4, as aapt adds for density folders ("-v4")
    entries, offsets = b"", []
    for i in range(n):
        offsets.append(len(entries))
        entries += struct.pack("<HHI", 8, 0, i)                 # ResTable_entry: size, flags, key index
        entries += struct.pack("<HBBI", 8, 0, TYPE_STRING, i)  # Res_value: string i of the global pool
    header_size = 8 + 12 + 64
    start = header_size + 4 * n
    typ = struct.pack("<HHIBBHII", RES_TABLE_TYPE, header_size, start + len(entries), 1, 0, 0, n, start)
    typ += bytes(config) + b"".join(struct.pack("<I", o) for o in offsets) + entries

    body = type_pool + key_pool + spec + typ
    name = package.encode("utf-16-le")[:254].ljust(256, b"\0")
    pkg = struct.pack("<HHII", RES_TABLE_PACKAGE, 288, 288 + len(body), 0x7F) + name
    pkg += struct.pack("<IIIII", 288, 1, 288 + len(type_pool), n, 0) + body

    values = string_pool(paths)
    table = struct.pack("<HHII", RES_TABLE, 12, 12 + len(values) + len(pkg), 1) + values + pkg
    with open(out, "wb") as f:
        f.write(table)
    for i, k in enumerate(keys):
        print(f"  @mipmap/{k} = 0x7f01{i:04x} -> {paths[i]}")


if __name__ == "__main__":
    main()
