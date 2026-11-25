#!/usr/bin/env python3
"""
decode_bean.py - map Java fields to output bits for a LGDataUtils "bean -> byte[]" converter.

Runs the converter in the emulator (jni_emu.py), sets one bit of one field at a time, and
records which output bit changes. Then checks the map against random combinations.

Usage (from the repository root):
    python3 tools/decode_bean.py convertHyControl LGControlHyBean
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # -I leaves the script directory off sys.path
from jni_emu import INT_SIGS, JniEmu  # noqa: E402

ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, "native", "libLGDataUtils.so")


def main():
    func, bean = sys.argv[1], sys.argv[2]
    symbol = f"Java_com_vison_macrochip_sdk_LGDataUtils_{func}"
    bean_cls = f"com/vison/macrochip/mode/{bean}"
    emu = JniEmu(LIB)
    cls = emu.new_class("com/vison/macrochip/sdk/LGDataUtils")

    def run(fields):
        return emu.array_bytes(emu.call_jni(symbol, cls, emu.new_object(bean_cls, fields)))

    emu.field_log.clear()
    base = run({})
    fields = list(dict.fromkeys(emu.field_log))
    n = len(base)
    print(f"{func}: {n} output bytes, reads {len(fields)} fields: "
          + ", ".join(f"{f}({s})" for f, s in fields))
    print(f"output with every field = 0: {base.hex(' ')}")

    def changed(out):
        return [(i, k) for i in range(n) for k in range(8) if (base[i] ^ out[i]) >> k & 1]

    bitmap, odd = {}, {}
    for fname, sig in fields:
        for k in range(min(8 * INT_SIGS.get(sig, 4), 16)):
            d = changed(run({fname: 1 << k}))
            if len(d) == 1:
                bitmap[d[0]] = (fname, k)
            elif d:
                odd.setdefault(fname, []).append((k, d))

    print("\nByte layout (bit 7 .. bit 0):")
    for i in range(n):
        cells = []
        for k in range(7, -1, -1):
            f = bitmap.get((i, k))
            cells.append(f"{f[0]}[{f[1]}]" if f else "-")
        print(f"  byte {i:2d}: " + "  ".join(cells))

    print("\nPer field:")
    for fname, _ in fields:
        spots = sorted((fb, i, k) for (i, k), (f, fb) in bitmap.items() if f == fname)
        if not spots and fname not in odd:
            print(f"  {fname:24s} not used in the output")
            continue
        if spots:
            groups, start = [], spots[0]
            prev = start
            for s in spots[1:] + [None]:
                if s and s[1] == prev[1] and s[2] == prev[2] + 1 and s[0] == prev[0] + 1:
                    prev = s
                    continue
                lo, hi = start, prev
                groups.append(f"value bits {lo[0]}-{hi[0]} -> byte {lo[1]} bits {lo[2]}-{hi[2]}"
                              if lo != hi else f"value bit {lo[0]} -> byte {lo[1]} bit {lo[2]}")
                start = prev = s
            print(f"  {fname:24s} " + "; ".join(groups))
        if fname in odd:
            print(f"  {fname:24s} NON-LINEAR: " + "; ".join(
                f"bit {k} changes {d}" for k, d in odd[fname][:3]))

    # Check: random values restricted to the mapped bits must give exactly the predicted bytes.
    rng = random.Random(1)
    mapped = {}
    for (i, k), (f, fb) in bitmap.items():
        mapped.setdefault(f, []).append((fb, i, k))
    bad = 0
    for _ in range(300):
        vals = {f: sum(1 << fb for fb, _, _ in m if rng.random() < 0.5) for f, m in mapped.items()}
        pred = bytearray(base)
        for f, m in mapped.items():
            for fb, i, k in m:
                if vals[f] >> fb & 1:
                    pred[i] ^= 1 << k
        if run(vals) != bytes(pred):
            bad += 1
    print(f"\nRandom check, mapped bits only: {300 - bad}/300 outputs match the map")

    # Values outside the mapped bits (e.g. 300 for an 8-bit field): masked, clamped or other?
    for f, m in mapped.items():
        width = max(fb for fb, _, _ in m) + 1
        big = (1 << width) + 1
        out = run({f: big})
        expect = run({f: big & ((1 << width) - 1)})
        if out != expect:
            print(f"  note: {f} = {big} gives {out.hex(' ')} (not simply masked to {width} bits)")


if __name__ == "__main__":
    main()
