"""Pindad UGV - UAV ground station for the Raspberry Pi 5 (or any Linux computer).

    python3 -m pindad_gcs --link gadget                       # remote plugged into the Pi (USB-C)
    python3 -m pindad_gcs --link tcp://PHONE_IP:9000          # through the Android app on a phone
    python3 -m pindad_gcs --link replay:capture.bin           # test with a recorded capture
Options: --headless (no window: link + RTSP + MAVLink only), --mavlink udpout:GCS_IP:14550,
--rtsp-port 8554 --rtsp-path /drone, --fullscreen.
"""
import argparse
import os
import sys
import threading
import time

from . import __version__
from .link import Link, make_backend
from .video import HAVE_GST, HAVE_RTSP, Media, Preview, RtspServer, VideoHub

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_UI = os.path.join(ROOT, "android", "app", "src", "main", "assets", "ui")


class Log:
    def __init__(self):
        self.lines, self.version, self.lock = [], 0, threading.Lock()

    def __call__(self, msg):
        line = time.strftime("%H:%M:%S  ") + msg
        print(line, flush=True)
        with self.lock:
            self.lines.append(line)
            del self.lines[:-300]
            self.version += 1

    def text(self):
        with self.lock:
            return "\n".join(self.lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pindad_gcs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--link", default="gadget", help="gadget | tcp://HOST[:9000] | replay:FILE")
    ap.add_argument("--headless", action="store_true", help="no window (link, RTSP and MAVLink only)")
    ap.add_argument("--rtsp-port", type=int, default=8554)
    ap.add_argument("--rtsp-path", default="/drone")
    ap.add_argument("--no-rtsp", action="store_true")
    ap.add_argument("--mavlink", default="", help="e.g. udpout:192.168.1.50:14550 (needs pymavlink)")
    ap.add_argument("--preview-port", type=int, default=8081, help="local MJPEG port for the window")
    ap.add_argument("--media-dir", default=os.path.expanduser("~/Pindad UGV-UAV"))
    ap.add_argument("--ui-dir", default=DEFAULT_UI)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--screenshot", default="", help=argparse.SUPPRESS)
    ap.add_argument("--screenshot-after", type=float, default=6, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    log = Log()
    log(f"Pindad UGV - UAV ground station {__version__}")
    if not HAVE_GST:
        log("GStreamer (python3-gi) not found: no video, RTSP or recording.")
    hub = VideoHub(log)
    preview = None
    if HAVE_GST and not args.headless:
        preview = Preview(args.preview_port, log)
        hub.add(preview)
    media = Media(args.media_dir, log, preview)
    if HAVE_GST:
        hub.add(media)
    rtsp = None
    if not args.no_rtsp:
        if HAVE_RTSP:
            rtsp = RtspServer(args.rtsp_port, args.rtsp_path, log)
            hub.add(rtsp)
        else:
            log("RTSP server not available (install gir1.2-gst-rtsp-server-1.0).")
    link = Link(make_backend(args.link), log, hub, media)
    link.start()
    if args.mavlink:
        try:
            from .mavlink_bridge import MavlinkBridge
            MavlinkBridge(args.mavlink, link, log)
        except ImportError:
            log("MAVLink needs pymavlink: pip install pymavlink")
    ctx = {"args": args, "log": log, "hub": hub, "preview": preview, "media": media, "rtsp": rtsp, "link": link}

    if args.headless:
        try:
            while True:
                time.sleep(10)
                d = link.tel.snapshot()
                log(f"link={'up' if link.connected else 'down'} video={hub.state()['fps']}fps "
                    f"armed={d['armed']} alt={d['alt']:.1f}m bat={d['batPct']}% gps={d['gpsNum']}")
        except KeyboardInterrupt:
            return 0
    from .ui import run_ui
    return run_ui(ctx)


if __name__ == "__main__":
    sys.exit(main())
