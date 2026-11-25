#!/usr/bin/env python3
"""
bridge_client.py - listen to the Pindad UGV - UAV app on the phone (listen-only, sends nothing).

Receives the raw bytes the remote sends to the phone, splits them into FF 56 53 frames,
prints a summary, and saves everything to captures/<time>.bin for later analysis.

Usage (from the repository root):
    python3 tools/bridge_client.py <phone-ip> [--port 9000] [--show 60]
"""
import argparse
import collections
import functools
import os
import socket
import string
import time

PRINTABLE = set(string.printable.encode()) - set(b"\t\n\r\x0b\x0c")


def ascii_runs(data, min_len=4):
    runs, cur = [], bytearray()
    for b in data:
        if b in PRINTABLE:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                runs.append(cur.decode())
            cur = bytearray()
    if len(cur) >= min_len:
        runs.append(cur.decode())
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--show", type=int, default=60, help="how many non-video frames to print")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = never)")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(root, "captures"), exist_ok=True)
    path = os.path.join(root, "captures", time.strftime("%Y%m%d-%H%M%S") + ".bin")
    raw = open(path, "wb")

    sock = socket.create_connection((args.host, args.port), timeout=10)
    sock.settimeout(1.0)
    print(f"connected to {args.host}:{args.port}, saving to {path}")

    buf = bytearray()
    frames, sizes = collections.Counter(), collections.Counter()
    first_payload = {}
    total = junk = shown = bad_xor = 0
    started = last_stats = time.time()
    try:
        while not args.seconds or time.time() - started < args.seconds:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                data = None
            if data == b"":
                print("phone closed the connection")
                break
            if data:
                raw.write(data)
                total += len(data)
                buf += data
            while True:
                i = buf.find(b"\xff\x56\x53")
                if i < 0:
                    junk += max(0, len(buf) - 2)
                    del buf[:-2]
                    break
                junk += i
                del buf[:i]
                if len(buf) < 11:
                    break
                length = buf[5] | buf[6] << 8
                ftype = buf[7] | buf[8] << 8
                if len(buf) < 11 + length:
                    break
                payload = bytes(buf[11:11 + length])
                xor_ok = functools.reduce(lambda a, b: a ^ b, payload, 0) == buf[9]
                bad_xor += not xor_ok
                del buf[:11 + length]
                frames[ftype] += 1
                sizes[ftype] += length
                first_payload.setdefault(ftype, payload)
                if ftype not in (3, 10) and shown < args.show:
                    shown += 1
                    text = ascii_runs(payload)
                    print(f"type {ftype:3d}  len {length:4d}  xor {'ok ' if xor_ok else 'BAD'}  "
                          f"{payload[:40].hex(' ')}{' ...' if length > 40 else ''}"
                          + (f"   text: {text}" if text else ""))
            if total > 4096 and not frames and junk:
                print(f"no FF 56 53 frames in the first {total} bytes; start of stream: "
                      f"{open(path, 'rb').read(64).hex(' ')}")
                junk = 0
            if time.time() - last_stats >= 5:
                last_stats = time.time()
                summary = ", ".join(f"type {t}: {frames[t]} ({sizes[t]} B)" for t in sorted(frames))
                print(f"[{time.time() - started:5.0f}s] {total} bytes | {summary or 'no frames'}"
                      f" | bad checksums {bad_xor} | bytes outside frames {junk}")
    except KeyboardInterrupt:
        pass
    finally:
        raw.close()
        print(f"\nsaved {total} bytes to {path}")
        for t in sorted(frames):
            print(f"  type {t}: {frames[t]} frames, {sizes[t]} bytes, first: {first_payload[t][:24].hex(' ')}")


if __name__ == "__main__":
    main()
