"""USB bridge for the SJRC remote: runs on a small board with a USB *device* port (Raspberry Pi
Zero 2 W, Pi 4 or Pi 5 in USB gadget mode), poses as the Android phone towards the remote and relays
everything over the network to the ground station (DGX Spark), exactly like the Android app's PC link:

  bridge -> ground station:  every FF 56 53 frame from the remote (video, telemetry, status), raw
  ground station -> bridge:  [type LE16][length LE16][payload] packets, sent on as FF 56 53 frames

The bridge sends the remote's heartbeats itself (also with no ground station connected), so the
remote keeps streaming. Frame types 18 and 20 (power, firmware upgrade) are refused.

    python3 -m pindad_gcs.bridge                    # on the board, after bridge/setup_gadget.sh
    python3 -m pindad_gcs.bridge --replay FILE      # test without USB: plays a capture instead
The ground station then connects with:  python3 -m pindad_gcs --link tcp://BRIDGE_IP:9000
"""
import argparse
import queue
import socket
import struct
import threading
import time

from . import protocol as P


class Bridge:
    def __init__(self, port, log):
        self.port, self.log = port, log
        self.connected = False
        self.client = None
        self.out = queue.Queue(maxsize=2048)   # remote -> ground station
        self.backend = None
        self.seq = 0
        self.lock = threading.Lock()
        self.rx = self.tx = 0

    # ---- interface used by the USB backend (same as Link) ----
    def on_connected(self, up):
        if up != self.connected:
            self.connected = up
            self.log("Remote connected." if up else "Remote disconnected.")

    def on_bytes(self, data):
        self.rx += len(data)
        if self.client is not None:
            try:
                self.out.put_nowait(data)
            except queue.Full:
                pass  # a slow network must never stall the USB side

    # ---- to the remote ----
    def send(self, ftype, payload):
        if ftype in P.BLOCKED_FRAME_TYPES:
            self.log(f"Refused frame type {ftype}.")
            return
        with self.lock:
            self.seq = (self.seq + 1) & 0xFFFF
            if self.backend.send_frame(ftype, payload, self.seq):
                self.tx += 1

    def heartbeats(self):
        while True:
            if self.connected:
                self.send(1, P.HB_TYPE1)
                self.send(5, P.HB_TYPE5)
                self.send(3, P.HB_VIDEO)
            time.sleep(1.0)

    # ---- network side ----
    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(1)
        self.log(f"Waiting for the ground station on TCP port {self.port}.")
        threading.Thread(target=self._writer, name="net-out", daemon=True).start()
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            old, self.client = self.client, conn
            if old is not None:
                old.close()
            while not self.out.empty():
                self.out.get_nowait()
            self.log(f"Ground station connected from {addr[0]}.")
            threading.Thread(target=self._reader, args=(conn,), name="net-in", daemon=True).start()

    def _reader(self, conn):
        f = conn.makefile("rb")
        try:
            while True:
                hdr = f.read(4)
                if len(hdr) < 4:
                    break
                ftype, n = struct.unpack("<HH", hdr)
                payload = f.read(n)
                if len(payload) < n:
                    break
                self.send(ftype, payload)
        except OSError:
            pass
        if self.client is conn:
            self.client = None
            self.log("Ground station disconnected.")

    def _writer(self):
        while True:
            data = self.out.get()
            c = self.client
            if c is None:
                continue
            try:
                c.sendall(data)
            except OSError:
                if self.client is c:
                    self.client = None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pindad_gcs.bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--replay", default="", help="test without USB: play this capture file")
    args = ap.parse_args(argv)

    def log(msg):
        print(time.strftime("%H:%M:%S  ") + msg, flush=True)

    bridge = Bridge(args.port, log)
    if args.replay:
        from .link import ReplayBackend
        bridge.backend = ReplayBackend(args.replay)
    else:
        from .gadget import GadgetBackend
        bridge.backend = GadgetBackend()
    bridge.backend.start(bridge)
    threading.Thread(target=bridge.heartbeats, name="heartbeat", daemon=True).start()
    threading.Thread(target=bridge.serve, name="server", daemon=True).start()
    try:
        while True:
            time.sleep(30)
            log(f"remote={'connected' if bridge.connected else 'waiting'} "
                f"ground station={'connected' if bridge.client else 'waiting'} "
                f"received={bridge.rx // 1024} KB sent frames={bridge.tx}")
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    main()
