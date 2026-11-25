#!/usr/bin/env python3
"""
analyze_capture.py - look inside a capture from bridge_client.py.

Shows incoming USB frame headers, splits type-5 payloads into 0x58 telemetry messages
(0x58, id, len, payload[len], check), and lists the type-6 "FF 53 54" status messages.

Usage: python3 tools/analyze_capture.py captures/<file>.bin
"""
import collections
import sys


def frames(data):
    i = 0
    while True:
        i = data.find(b"\xff\x56\x53", i)
        if i < 0 or i + 11 > len(data):
            return
        n = data[i + 5] | data[i + 6] << 8
        if i + 11 + n > len(data):
            return
        yield data[i:i + 11], data[i + 11:i + 11 + n]
        i += 11 + n


def main():
    data = open(sys.argv[1], "rb").read()
    fs = list(frames(data))
    print(f"{len(fs)} frames")

    print("\nFirst headers (bytes 0-10):")
    for h, p in fs[:8]:
        x = 0
        for b in p:
            x ^= b
        print(f"  {h.hex(' ')}   type {h[7] | h[8] << 8}, len {len(p)}, xor(payload)={x:02x}, sum&ff={sum(p) & 0xFF:02x}")

    # type 5: stream of 0x58 messages (may continue across frames)
    stream = b"".join(p for h, p in fs if (h[7] | h[8] << 8) == 5)
    msgs, i, skipped = collections.defaultdict(list), 0, 0
    check_ok = collections.Counter()
    while i + 3 <= len(stream):
        if stream[i] != 0x58:
            i += 1
            skipped += 1
            continue
        mid, ln = stream[i + 1], stream[i + 2]
        if i + 3 + ln + 1 > len(stream):
            break
        body = stream[i + 3:i + 3 + ln]
        chk = stream[i + 3 + ln]
        x = mid ^ ln
        for b in body:
            x ^= b
        s = (mid + ln + sum(body)) & 0xFF
        check_ok["xor(id,len,payload)" if x == chk else "sum(id,len,payload)" if s == chk else "neither"] += 1
        msgs[mid].append(body)
        i += 3 + ln + 1
    print(f"\nType-5 stream: {len(stream)} bytes, {sum(len(v) for v in msgs.values())} messages, "
          f"{skipped} bytes skipped; check byte matches: {dict(check_ok)}")
    for mid in sorted(msgs):
        bodies = msgs[mid]
        lens = sorted({len(b) for b in bodies})
        varying = [k for k in range(min(map(len, bodies))) if len({b[k] for b in bodies}) > 1]
        print(f"  id 0x{mid:02X}: {len(bodies):4d} msgs, len {lens}, changing bytes {varying}")
        print(f"      first: {bodies[0].hex(' ')}")
        print(f"      last:  {bodies[-1].hex(' ')}")

    # type 6: FF 53 54 <cmd> ... status messages
    t6 = collections.defaultdict(list)
    for h, p in fs:
        if (h[7] | h[8] << 8) == 6 and p[:3] == b"\xff\x53\x54" and len(p) >= 4:
            t6[p[3]].append(p[4:])
    print("\nType-6 'FF 53 54' messages:")
    for cmd in sorted(t6):
        vals = t6[cmd]
        distinct = list(dict.fromkeys(v.hex(' ') for v in vals))
        print(f"  cmd 0x{cmd:02X}: {len(vals):4d} msgs, {len(distinct)} distinct values: {distinct[:8]}")


if __name__ == "__main__":
    main()
