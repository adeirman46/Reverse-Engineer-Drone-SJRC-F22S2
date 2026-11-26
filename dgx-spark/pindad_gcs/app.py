"""Pindad UGV - UAV ground station for the NVIDIA DGX Spark (or any Linux computer).

    python3 -m pindad_gcs                                     # find the bridge on the network (tcp://auto)
    python3 -m pindad_gcs --link tcp://BRIDGE_IP:9000         # USB bridge board or the phone app
    python3 -m pindad_gcs --link gadget                       # this computer has a USB device port
    python3 -m pindad_gcs --link replay:capture.bin           # test with a recorded capture
Options: --headless (no window: link + RTSP + MAVLink only), --mavlink udpout:GCS_IP:14550,
--rtsp-port 8554 --rtsp-path /drone, --fullscreen, --limits ALT,DIST,RTH (drone flight limits in metres,
kept in the settings file and re-sent after every connection; also set from the window's Settings).
"""
import argparse
import json
import os
import sys
import threading
import time

from . import __version__
from .link import Link, make_backend
from .video import HAVE_GST, HAVE_RTSP, Media, Preview, RtspServer, VideoHub

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_UI = os.path.join(ROOT, "android", "app", "src", "main", "assets", "ui")
SETTINGS_FILE = os.path.expanduser("~/.config/pindad-gcs/settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(changes):
    s = load_settings()
    s.update(changes)
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, SETTINGS_FILE)


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
    ap.add_argument("--link", default="tcp://auto", help="tcp://auto | tcp://HOST[:9000] | gadget | replay:FILE")
    ap.add_argument("--headless", action="store_true", help="no window (link, RTSP and MAVLink only)")
    ap.add_argument("--rtsp-port", type=int, default=8554)
    ap.add_argument("--rtsp-path", default="/drone")
    ap.add_argument("--no-rtsp", action="store_true")
    ap.add_argument("--mavlink", default="", help="e.g. udpout:192.168.1.50:14550 (needs pymavlink)")
    ap.add_argument("--preview-port", type=int, default=8081, help="local MJPEG port for the window")
    ap.add_argument("--media-dir", default=os.path.expanduser("~/Pindad UGV-UAV"))
    ap.add_argument("--ui-dir", default=DEFAULT_UI)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--limits", default="", metavar="ALT,DIST,RTH",
                    help="drone flight limits in metres, e.g. 500,2000,40 (saved and re-sent after each connection)")
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
    if args.limits:
        try:
            lim = [int(v) for v in args.limits.split(",")]
            if len(lim) != 3:
                raise ValueError
        except ValueError:
            ap.error("--limits needs three whole numbers: ALT,DIST,RTH (metres)")
        save_settings({"limits": lim})
    lim = load_settings().get("limits")
    if isinstance(lim, list) and len(lim) == 3:
        link.flight.saved_limits = tuple(int(v) for v in lim)
        log(f"Flight limits to apply after connecting: altitude {lim[0]} m, distance {lim[1]} m, return {lim[2]} m.")
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
