#!/usr/bin/env python3
"""
decode_telemetry.py - decode captured telemetry with the official app's own parsers.

Splits type-5 frames of a capture into 0x58 messages and runs the matching LGDataUtils parser
(byte[] -> bean) in the emulator. Prints each message type's fields: constant or min..max.

Usage: python3 tools/decode_telemetry.py captures/<file>.bin
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from jni_emu import JniEmu  # noqa: E402

ROOT = os.path.dirname(HERE)

# wire id -> parser, from com.sj.baselibrary.utils.AnalysisUtils.hackFly
PARSERS = {
    0x8B: "convertGetPlaneSjInfo",
    0x8C: "convertGetPlaneGpsInfo",
    0x9B: "convertSjGetHyInfo9B",
    0x9C: "convertSjGetHyInfo9C",
    0x9D: "convertGetPlaneGpsInfo2",
    0x9E: "convertSjHy9EInfo",
}


def messages(path):
    data = open(path, "rb").read()
    stream, i = bytearray(), 0
    while True:
        i = data.find(b"\xff\x56\x53", i)
        if i < 0 or i + 11 > len(data):
            break
        n = data[i + 5] | data[i + 6] << 8
        if (data[i + 7] | data[i + 8] << 8) == 5:
            stream += data[i + 11:i + 11 + n]
        i += 11 + n
    j = 0
    while j + 4 <= len(stream):
        if stream[j] != 0x58:
            j += 1
            continue
        mid, ln = stream[j + 1], stream[j + 2]
        if j + 4 + ln > len(stream):
            break
        yield mid, bytes(stream[j + 3:j + 3 + ln])
        j += 4 + ln


def main():
    emu = JniEmu(os.path.join(ROOT, "native", "libLGDataUtils.so"))
    cls = emu.new_class("com/vison/macrochip/sdk/LGDataUtils")
    by_id = collections.defaultdict(list)
    for mid, body in messages(sys.argv[1]):
        by_id[mid].append(body)
    for mid in sorted(by_id):
        bodies = by_id[mid]
        name = PARSERS.get(mid)
        if not name:
            print(f"\nid 0x{mid:02X}: {len(bodies)} messages, no parser known")
            continue
        symbol = f"Java_com_vison_macrochip_sdk_LGDataUtils_{name}"
        values = collections.defaultdict(list)
        cls_name = "?"
        try:
            for body in bodies:
                obj = emu.call_jni(symbol, cls, emu.new_byte_array(body))
                o = emu.handles.get(obj, {})
                cls_name = o.get("cls", cls_name)
                for k, v in o.get("fields", {}).items():
                    arr = emu.handles.get(v) if isinstance(v, int) else None
                    if arr and arr.get("kind") == "array":  # int[] / byte[] field: one entry per element
                        for idx, e in enumerate(emu.array_values(v)):
                            values[f"{k}[{idx}]"].append(e)
                    else:
                        values[k].append(v)
        except Exception as e:
            print(f"\nid 0x{mid:02X}: {name} failed in the emulator: {e}")
            continue
        print(f"\nid 0x{mid:02X}  {name} -> {cls_name.split('/')[-1]}  ({len(bodies)} messages)")
        const = [f"{k}={v[0]:g}" if isinstance(v[0], float) else f"{k}={v[0]}"
                 for k, v in values.items() if len(set(v)) == 1]
        changing = [(k, v) for k, v in values.items() if len(set(v)) > 1]
        for k, v in changing:
            print(f"   {k:28s} {min(v)} .. {max(v)}   (first {v[0]}, last {v[-1]})")
        if const:
            print("   constant: " + ", ".join(const))


if __name__ == "__main__":
    main()
