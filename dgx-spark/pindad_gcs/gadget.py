"""USB gadget backend: the Raspberry Pi poses as the Android phone, so the remote can plug into it.

The SJRC remote is a USB host that expects an Android device in "Android Open Accessory" (AOA)
mode: vendor 0x18D1, product 0x2D00, one vendor-specific interface with a bulk IN and a bulk OUT
endpoint. On Linux this is a USB gadget built with configfs + FunctionFS (gadget/setup_gadget.sh);
this module writes the descriptors, answers the AOA control requests (GET_PROTOCOL, SEND_STRING,
START) and moves the data:  bulk OUT (remote -> Pi): video and telemetry,  bulk IN (Pi -> remote):
heartbeats and flight messages.

Needs a USB port that can work as a device: on the Raspberry Pi 5 the USB-C port with
dtoverlay=dwc2,dr_mode=peripheral. EXPERIMENTAL: written to the kernel and AOA specifications,
not yet tested with the remote; see bridge/README.md.
"""
import os
import queue
import struct
import threading
import time

from . import protocol as P

FFS = os.environ.get("PINDAD_FFS", "/dev/ffs-pindad")
GADGET = os.environ.get("PINDAD_GADGET", "/sys/kernel/config/usb_gadget/pindad")

# linux/usb/functionfs.h
DESCRIPTORS_MAGIC_V2, STRINGS_MAGIC = 3, 2
HAS_FS_DESC, HAS_HS_DESC, ALL_CTRL_RECIP = 1, 2, 64
BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME = range(7)
EVENT_SIZE = 12  # struct usb_functionfs_event

# Android Open Accessory control requests (vendor, recipient device)
AOA_GET_PROTOCOL, AOA_SEND_STRING, AOA_START = 51, 52, 53
AOA_STRING_NAMES = ["manufacturer", "model", "description", "version", "uri", "serial"]


def _interface():
    # bLength, INTERFACE, number 0, alt 0, 2 endpoints, vendor class 0xFF/0xFF/0x00, string 1
    return struct.pack("<9B", 9, 4, 0, 0, 2, 0xFF, 0xFF, 0x00, 1)


def _endpoint(address, max_packet):
    return struct.pack("<BBBBHB", 7, 5, address, 2, max_packet, 0)  # bulk


def descriptors():
    fs = _interface() + _endpoint(0x81, 64) + _endpoint(0x02, 64)
    hs = _interface() + _endpoint(0x81, 512) + _endpoint(0x02, 512)
    body = struct.pack("<II", 3, 3) + fs + hs  # three descriptors per speed
    flags = HAS_FS_DESC | HAS_HS_DESC | ALL_CTRL_RECIP  # ALL_CTRL_RECIP: we get the AOA vendor requests
    return struct.pack("<III", DESCRIPTORS_MAGIC_V2, 12 + len(body), flags) + body


def strings():
    body = struct.pack("<H", 0x0409) + b"Android Accessory Interface\0"
    return struct.pack("<IIII", STRINGS_MAGIC, 16 + len(body), 1, 1) + body


class GadgetBackend:
    sends_heartbeats = True  # we are the phone: send the official app's heartbeats ourselves

    def __init__(self, ffs=FFS, gadget=GADGET):
        self.ffs, self.gadget = ffs, gadget
        self.name = "USB gadget (Pi as the phone)"
        self.ep0 = None
        self.out_q = queue.Queue(maxsize=256)
        self.enabled = False
        self.link = None
        self.accessory = {}

    def start(self, link):
        self.link = link
        self.ep0 = os.open(os.path.join(self.ffs, "ep0"), os.O_RDWR)
        os.write(self.ep0, descriptors())
        os.write(self.ep0, strings())
        threading.Thread(target=self._ep0_loop, name="ffs-ep0", daemon=True).start()
        threading.Thread(target=self._writer, name="ffs-in", daemon=True).start()
        self._bind()

    # ---- UDC binding (needs write access to the gadget's UDC file; see setup_gadget.sh) ----
    def _udc_name(self):
        names = sorted(os.listdir("/sys/class/udc"))
        if not names:
            raise RuntimeError("no USB device controller: add dtoverlay=dwc2,dr_mode=peripheral and reboot")
        return names[0]

    def _bind(self):
        path = os.path.join(self.gadget, "UDC")
        try:
            with open(path, "w") as fh:
                fh.write(self._udc_name())
            self.link.log("USB gadget ready: plug the remote into the Raspberry Pi's USB-C port.")
        except OSError as e:
            self.link.log(f"Could not bind the USB gadget ({e}). Run gadget/setup_gadget.sh first.")

    def _reenumerate(self):
        """After AOA START a phone reconnects as an accessory; do the same with a short unplug."""
        path = os.path.join(self.gadget, "UDC")
        try:
            time.sleep(0.2)
            with open(path, "w") as fh:
                fh.write("\n")
            time.sleep(0.5)
            with open(path, "w") as fh:
                fh.write(self._udc_name())
        except OSError as e:
            self.link.log(f"USB gadget re-enumeration failed: {e}")

    # ---- control endpoint ----
    def _ep0_loop(self):
        while True:
            data = os.read(self.ep0, EVENT_SIZE * 4)
            for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                rt, req, val, idx, length, etype = struct.unpack_from("<BBHHHB", data, i)
                if etype == SETUP:
                    self._setup(rt, req, val, idx, length)
                elif etype == ENABLE:
                    self._enable()
                elif etype in (DISABLE, UNBIND, SUSPEND):
                    self._disable()

    def _setup(self, rt, req, val, idx, length):
        try:
            if rt & 0x60 == 0x40:  # vendor request
                if req == AOA_GET_PROTOCOL and rt & 0x80:
                    os.write(self.ep0, struct.pack("<H", 2))
                    return
                if req == AOA_SEND_STRING and not rt & 0x80:
                    s = os.read(self.ep0, length).rstrip(b"\0").decode("utf-8", "replace") if length else ""
                    if idx < len(AOA_STRING_NAMES):
                        self.accessory[AOA_STRING_NAMES[idx]] = s
                    return
                if req == AOA_START and not rt & 0x80:
                    os.read(self.ep0, 0)  # acknowledge
                    self.link.log(f"Remote asked for accessory mode: {self.accessory}")
                    threading.Thread(target=self._reenumerate, daemon=True).start()
                    return
                if not rt & 0x80:  # other AOA requests (HID, audio): accept
                    os.read(self.ep0, length)
                    return
            # anything else: stall (by doing the opposite direction)
            if rt & 0x80:
                os.read(self.ep0, 0)
            else:
                os.write(self.ep0, b"")
        except OSError:
            pass

    # ---- data endpoints ----
    def _enable(self):
        if self.enabled:
            return
        self.enabled = True
        threading.Thread(target=self._reader, name="ffs-out", daemon=True).start()
        self.link.on_connected(True)

    def _disable(self):
        if self.enabled:
            self.enabled = False
            self.link.on_connected(False)

    def _reader(self):
        try:
            fd = os.open(os.path.join(self.ffs, "ep2"), os.O_RDONLY)
        except OSError as e:
            self.link.log(f"USB gadget: cannot open ep2: {e}")
            return
        try:
            while self.enabled:
                data = os.read(fd, 16384)
                if data:
                    self.link.on_bytes(data)
        except OSError:
            pass  # endpoint disabled (unplugged)
        finally:
            os.close(fd)

    def _writer(self):
        fd = None
        while True:
            frame = self.out_q.get()
            if not self.enabled:
                continue
            try:
                if fd is None:
                    fd = os.open(os.path.join(self.ffs, "ep1"), os.O_WRONLY)
                os.write(fd, frame)
            except OSError:
                if fd is not None:
                    os.close(fd)
                fd = None

    def send_frame(self, ftype, payload, seq):
        if not self.enabled:
            return False
        try:
            self.out_q.put_nowait(P.usb_frame(ftype, payload, seq))
            return True
        except queue.Full:
            return False
