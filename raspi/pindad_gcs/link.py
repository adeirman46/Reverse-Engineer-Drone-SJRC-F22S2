"""The link to the SJRC remote, through one of three backends:

  gadget       the remote plugs into the Raspberry Pi 5's USB-C port; the Pi poses as the Android
               phone (USB gadget, Android Open Accessory). See gadget.py and raspi/README.md.
  tcp://HOST   the Pindad UGV - UAV Android app keeps the remote and relays it over the network
               (TCP 9000). Flight commands need "PC control" on in the app's Settings.
  replay:FILE  plays back a capture (raw frames saved by tools/link.py) for testing without hardware.

Remote -> us: FF 56 53 frames. Type 3 = video, type 5 = telemetry stream, type 6 = status.
"""
import os
import socket
import struct
import threading
import time
from collections import Counter

from . import protocol as P
from .flight import Flight
from .telemetry import Telemetry


class FrameParser:
    """Splits the byte stream into FF 56 53 frames: header 11 bytes (length LE16 at 5, type LE16 at 7)."""

    def __init__(self, on_frame):
        self.buf = bytearray()
        self.on_frame = on_frame

    def feed(self, data):
        b = self.buf
        b += data
        if len(b) > (1 << 20):
            del b[:-(1 << 16)]
        p = 0
        while True:
            m = b.find(b"\xff\x56\x53", p)
            if m < 0:
                p = max(p, len(b) - 2)
                break
            if len(b) - m < 11:
                p = m
                break
            n = b[m + 5] | b[m + 6] << 8
            ftype = b[m + 7] | b[m + 8] << 8
            if len(b) - m < 11 + n:
                p = m
                break
            self.on_frame(ftype, bytes(b[m + 11:m + 11 + n]))
            p = m + 11 + n
        del b[:p]


class Link:
    def __init__(self, backend, log, video, media):
        self.backend, self.log, self.video, self.media = backend, log, video, media
        self.connected = False
        self.rx_bytes = 0
        self.frames = Counter()
        self.video_enabled = True
        self.tel = Telemetry(log)
        self.flight = Flight(self, self.tel, log)
        self.parser = FrameParser(self._on_frame)
        self._seq = 0
        self._send_lock = threading.Lock()

    def start(self):
        self.backend.start(self)
        if self.backend.sends_heartbeats:
            threading.Thread(target=self._heartbeats, name="heartbeat", daemon=True).start()

    # called by the backend
    def on_connected(self, up):
        if up and not self.connected:
            self.tel.reset()
            self.connected = True
            self.log(f"Connected to the remote ({self.backend.name}).")
            self.flight.start()
        elif not up and self.connected:
            self.connected = False
            self.log("Remote disconnected.")

    def on_bytes(self, data):
        self.rx_bytes += len(data)
        self.parser.feed(data)

    def _on_frame(self, ftype, payload):
        self.frames[ftype] += 1
        if ftype == 3:
            if self.video_enabled:
                self.video.feed(payload)
        else:
            self.tel.on_frame(ftype, payload)

    # sending
    def send_frame(self, ftype, payload):
        if ftype in P.BLOCKED_FRAME_TYPES:
            raise ValueError(f"frame type {ftype} is blocked")
        with self._send_lock:
            self._seq = (self._seq + 1) & 0xFFFF
            try:
                return self.backend.send_frame(ftype, bytes(payload), self._seq)
            except OSError as e:
                self.log(f"send failed: {e}")
                return False

    def send_drone(self, payload):
        return self.send_frame(6, payload)

    def _heartbeats(self):
        while True:
            if self.connected:
                self.send_frame(1, P.HB_TYPE1)
                self.send_frame(5, P.HB_TYPE5)
                if self.video_enabled:
                    self.send_frame(3, P.HB_VIDEO)
            time.sleep(1.0)


class TcpBackend:
    """The Android app (or the older Drone Bridge app) relays the remote on TCP port 9000.
    Up: [type LE16][length LE16][payload]; down: the raw FF 56 53 frames from the remote."""
    sends_heartbeats = False  # the phone app already sends them

    def __init__(self, host, port=9000):
        self.host, self.port = host, port
        self.name = f"phone app at {host}:{port}"
        self.sock = None

    def start(self, link):
        threading.Thread(target=self._run, args=(link,), name="tcp-link", daemon=True).start()

    def _run(self, link):
        while True:
            try:
                s = socket.create_connection((self.host, self.port), timeout=5)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(3)
                self.sock = s
                got_data = False
                while True:
                    try:
                        data = s.recv(65536)
                    except socket.timeout:
                        if got_data:
                            link.on_connected(False)
                            got_data = False
                        continue
                    if not data:
                        break
                    if not got_data:
                        got_data = True
                        link.on_connected(True)
                    link.on_bytes(data)
            except OSError as e:
                link.log(f"phone app link: {e}; retrying in 3 s")
            self.sock = None
            link.on_connected(False)
            time.sleep(3)

    def send_frame(self, ftype, payload, seq):
        s = self.sock
        if s is None:
            return False
        s.sendall(struct.pack("<HH", ftype, len(payload)) + payload)
        return True


class ReplayBackend:
    """Plays a capture file in a loop at about the recorded rate (for testing without hardware)."""
    sends_heartbeats = False

    def __init__(self, path, rate=450_000):
        self.path, self.rate = path, rate
        self.name = f"replay of {os.path.basename(path)}"

    def start(self, link):
        threading.Thread(target=self._run, args=(link,), name="replay", daemon=True).start()

    def _run(self, link):
        data = open(self.path, "rb").read()
        if not data:
            link.log("replay: empty file")
            return
        link.on_connected(True)
        chunk = 16384
        while True:
            for i in range(0, len(data), chunk):
                link.on_bytes(data[i:i + chunk])
                time.sleep(chunk / self.rate)

    def send_frame(self, ftype, payload, seq):
        return True  # nothing to send to


def make_backend(spec):
    if spec == "gadget":
        from .gadget import GadgetBackend
        return GadgetBackend()
    if spec.startswith("tcp://"):
        hostport = spec[6:]
        host, _, port = hostport.partition(":")
        return TcpBackend(host, int(port or 9000))
    if spec.startswith("replay:"):
        return ReplayBackend(spec[7:])
    raise SystemExit(f"unknown --link {spec!r} (use gadget, tcp://HOST[:9000] or replay:FILE)")
