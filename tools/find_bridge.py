#!/usr/bin/env python3
"""
find_bridge.py - find the phone running Drone Bridge / Pindad UGV - UAV (TCP port 9000) on this
PC's local networks. Probes through each network interface separately (SO_BINDTODEVICE), because
this PC has a cable and Wi-Fi on the same subnet and only Wi-Fi reaches the phone.

Usage: python3 tools/find_bridge.py [--wait 600] [--port 9000]
Prints "address interface" and exits 0 when found; exits 1 if nothing answers within --wait seconds.
"""
import argparse
import ipaddress
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def local_networks():
    """[(network, own address, interface)] for this PC's IPv4 LANs."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True).stdout
    nets = []
    for line in out.splitlines():
        parts = line.split()
        dev = parts[1]
        if "inet" not in parts or dev.startswith(("docker", "br-", "veth")):
            continue
        iface = ipaddress.ip_interface(parts[parts.index("inet") + 1])
        if iface.network.prefixlen >= 22:
            nets.append((iface.network, iface.ip, dev))
    return nets


def port_open(ip, port, dev):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
        return s.connect_ex((str(ip), port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def scan(port):
    jobs = [(h, dev) for net, own, dev in local_networks() for h in net.hosts() if h != own]
    with ThreadPoolExecutor(256) as pool:
        ok = list(pool.map(lambda j: port_open(j[0], port, j[1]), jobs))
    return [j for j, o in zip(jobs, ok) if o]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=0)
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()
    end = time.time() + args.wait
    while True:
        hits = scan(args.port)
        if hits:
            for ip, dev in hits:
                print(f"{ip} {dev}", flush=True)
            return 0
        if time.time() >= end:
            print("not found", flush=True)
            return 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
