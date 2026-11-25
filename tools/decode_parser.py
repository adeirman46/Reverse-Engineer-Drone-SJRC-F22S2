#!/usr/bin/env python3
"""
decode_parser.py - map input bits to output fields for a LGDataUtils "byte[] -> bean" parser.

Runs the parser in the emulator on an all-zero message, then with one input bit set at a time,
and reports which field each bit feeds (offset, width, signedness, scale).

Usage (from the repository root):
    python3 tools/decode_parser.py convertGetPlaneSjInfo 14
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from jni_emu import HANDLE_BASE, JniEmu  # noqa: E402

ROOT = os.path.dirname(HERE)


def main():
    func, length = sys.argv[1], int(sys.argv[2])
    emu = JniEmu(os.path.join(ROOT, "native", "libLGDataUtils.so"))
    cls = emu.new_class("com/vison/macrochip/sdk/LGDataUtils")
    symbol = f"Java_com_vison_macrochip_sdk_LGDataUtils_{func}"

    def run(data):
        obj = emu.call_jni(symbol, cls, emu.new_byte_array(bytes(data)))
        out = {}
        for k, v in emu.handles[obj]["fields"].items():
            arr = emu.handles.get(v) if isinstance(v, int) and v >= HANDLE_BASE else None
            if arr and arr.get("kind") == "array":
                for i, e in enumerate(emu.array_values(v)):
                    out[f"{k}[{i}]"] = e
            else:
                out[k] = v
        return out

    base = run(bytearray(length))
    contrib = {f: [] for f in base}
    for byte in range(length):
        for bit in range(8):
            data = bytearray(length)
            data[byte] = 1 << bit
            out = run(data)
            for f in base:
                if out[f] != base[f]:
                    contrib[f].append((8 * byte + bit, out[f] - base[f]))

    print(f"{func}: {length}-byte message, {len(base)} fields (bit n = byte n//8, bit n%8)")
    for f, c in contrib.items():
        if not c:
            print(f"  {f:26s} not fed by any input bit (always {base[f]})")
            continue
        bits = [b for b, _ in c]
        w = [d for _, d in c]
        lo, hi, n = bits[0], bits[-1], len(bits)
        contiguous = bits == list(range(lo, hi + 1))
        unsigned = all(w[i] == w[0] * 2 ** i for i in range(n))
        signed = n > 1 and all(w[i] == w[0] * 2 ** i for i in range(n - 1)) and w[-1] == -w[0] * 2 ** (n - 1)
        if contiguous and (unsigned or signed):
            where = f"byte {lo // 8}" + (f" bit {lo % 8}" if n < 8 or lo % 8 else "")
            if n >= 8 and lo % 8 == 0 and n % 8 == 0:
                where = f"bytes {lo // 8}-{hi // 8}" if n > 8 else f"byte {lo // 8}"
            elif n > 1:
                where = f"bits {lo}-{hi} (byte {lo // 8} bit {lo % 8} .. byte {hi // 8} bit {hi % 8})"
            scale = "" if w[0] == 1 else f", x{w[0]}"
            print(f"  {f:26s} {where}: {n}-bit {'signed' if signed else 'unsigned'}{scale}")
        else:
            print(f"  {f:26s} IRREGULAR: {c[:8]}{' ...' if len(c) > 8 else ''}")


if __name__ == "__main__":
    main()
