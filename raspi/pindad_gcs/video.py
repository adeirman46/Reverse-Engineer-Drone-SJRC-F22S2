"""Live video: frame assembly, RTSP server, local preview (MJPEG for the interface) and recording.

The remote wraps each compressed frame in a 44-byte header: 00 00 01 A0 (A1 = keyframe), frame
counter LE16 @10, payload length LE32 @16, height LE16 @24, width LE16 @26, timestamp us LE32 @36,
then Annex-B NAL units. H.264 or H.265 is detected from the parameter sets of the first keyframe.
Nothing is re-encoded for RTSP or recording: the drone's stream is passed through.

GStreamer (PyGObject) is optional: without it the RTSP server, preview and recording are off.
"""
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEADER = 44
HEVC, AVC = "h265", "h264"

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    Gst.init(None)
    HAVE_GST = True
except (ImportError, ValueError):
    HAVE_GST = False

try:
    gi.require_version("GstRtspServer", "1.0")
    from gi.repository import GstRtspServer
    HAVE_RTSP = HAVE_GST
except (NameError, ImportError, ValueError):
    HAVE_RTSP = False


def nal_units(d):
    """NAL units (without start codes) of an Annex-B buffer."""
    out, start, i, n = [], -1, 0, len(d)
    while i + 2 < n:
        if d[i] == 0 and d[i + 1] == 0 and d[i + 2] == 1:
            if start >= 0:
                end = i - 1 if i > start and d[i - 1] == 0 else i
                if end > start:
                    out.append(d[start:end])
            start = i + 3
            i += 3
        else:
            i += 1
    if 0 <= start < n:
        out.append(d[start:])
    return out


def detect_codec(frame):
    for n in nal_units(frame):
        h = n[0]
        if not h & 0x81 and 32 <= (h >> 1) & 0x3F <= 34 and len(n) > 1 and n[1] == 1:
            return HEVC
        if not h & 0x80 and h & 0x1F in (7, 8):
            return AVC
    return None


class Frame:
    __slots__ = ("data", "key", "pts", "w", "h")

    def __init__(self, data, key, pts, w, h):
        self.data, self.key, self.pts, self.w, self.h = data, key, pts, w, h


class VideoHub:
    """Assembles frames from the USB video payloads and hands them to the consumers."""

    def __init__(self, log):
        self.log = log
        self.buf = bytearray()
        self.consumers = []
        self.codec = None
        self.w = self.h = 0
        self.frames = 0
        self.last_frame = 0.0
        self._fps_n, self._fps_t, self.fps = 0, time.monotonic(), 0

    def add(self, consumer):
        self.consumers.append(consumer)

    def active(self):
        return self.last_frame > 0 and time.monotonic() - self.last_frame < 1.5

    def feed(self, payload):
        b = self.buf
        b += payload
        if len(b) > (4 << 20):
            b.clear()
        p = 0
        while True:
            m = self._marker(p)
            if m < 0:
                p = max(p, len(b) - 3)
                break
            if len(b) - m < HEADER:
                p = m
                break
            plen = struct.unpack_from("<i", b, m + 16)[0]
            if plen <= 0 or plen > (4 << 20) - HEADER:
                p = m + 4
                continue
            if len(b) - m < HEADER + plen:
                p = m
                break
            key = b[m + 3] == 0xA1
            h, w = struct.unpack_from("<HH", b, m + 24)
            pts = struct.unpack_from("<I", b, m + 36)[0]
            frame = Frame(bytes(b[m + HEADER:m + HEADER + plen]), key, pts, w, h)
            p = m + HEADER + plen
            self._publish(frame)
        del b[:p]

    def _marker(self, start):
        b = self.buf
        while True:
            i = b.find(b"\x00\x00\x01", start)
            if i < 0 or i + 3 >= len(b):
                return -1
            if b[i + 3] in (0xA0, 0xA1):
                return i
            start = i + 1

    def _publish(self, f):
        if f.key:
            c = detect_codec(f.data)
            if c and c != self.codec:
                self.codec = c
                self.log(f"Video: {c.upper()} {f.w}x{f.h}")
        if not self.codec:
            return
        self.w, self.h = f.w, f.h
        self.frames += 1
        self.last_frame = now = time.monotonic()
        self._fps_n += 1
        if now - self._fps_t >= 1:
            self.fps, self._fps_n, self._fps_t = round(self._fps_n / (now - self._fps_t)), 0, now
        for c in self.consumers:
            try:
                c.push(f, self.codec)
            except Exception as e:  # one consumer must not stop the others
                self.log(f"video consumer {type(c).__name__}: {e}")

    def state(self):
        return {"codec": {HEVC: "H.265", AVC: "H.264"}.get(self.codec, ""), "w": self.w, "h": self.h,
                "fps": self.fps if self.active() else 0, "active": 1 if self.active() else 0, "dropped": 0}


def _caps(codec):
    return f"video/x-{codec},stream-format=byte-stream,alignment=au"


def _buffer(data):
    return Gst.Buffer.new_wrapped(bytes(data))


class _GLibLoop:
    """A private GLib main context + loop thread for the RTSP server. Private, so it never fights
    with the Qt event loop, which runs the default GLib context."""
    _ctx = None

    @classmethod
    def context(cls):
        if cls._ctx is None:
            cls._ctx = GLib.MainContext.new()
            loop = GLib.MainLoop.new(cls._ctx, False)
            threading.Thread(target=loop.run, name="glib-rtsp", daemon=True).start()
        return cls._ctx


class RtspServer:
    """rtsp://<this computer>:<port><path> with the drone's stream as it arrives (no re-encoding)."""

    def __init__(self, port, path, log):
        self.port, self.path, self.log = port, path, log
        self.server = GstRtspServer.RTSPServer()
        self.server.set_service(str(port))
        self.server.attach(_GLibLoop.context())
        self.factory = None
        self.sources = {}  # appsrc -> waiting for a keyframe
        self.lock = threading.Lock()

    def push(self, f, codec):
        if self.factory is None:
            launch = (f"( appsrc name=src is-live=true format=time do-timestamp=true caps={_caps(codec)} "
                      f"! {codec}parse config-interval=-1 ! rtp{codec}pay name=pay0 pt=96 config-interval=-1 )")
            self.factory = GstRtspServer.RTSPMediaFactory()
            self.factory.set_launch(launch)
            self.factory.set_shared(True)
            self.factory.connect("media-configure", self._configure)
            self.server.get_mount_points().add_factory(self.path, self.factory)
            self.log(f"RTSP: serving rtsp://<this computer>:{self.port}{self.path}")
        with self.lock:
            targets = list(self.sources.items())
        for src, waiting in targets:
            if waiting and not f.key:
                continue
            if waiting:
                with self.lock:
                    self.sources[src] = False
            src.emit("push-buffer", _buffer(f.data))

    def _configure(self, factory, media):
        src = media.get_element().get_child_by_name("src")
        with self.lock:
            self.sources[src] = True
        self.log("RTSP: a viewer connected.")

        def gone(*_):
            with self.lock:
                self.sources.pop(src, None)
        media.connect("unprepared", gone)


class Preview:
    """Decodes the stream for the on-screen interface and serves it as MJPEG on 127.0.0.1."""

    def __init__(self, port, log, width=1280):
        self.port, self.log, self.width = port, log, width
        self.pipeline = self.src = None
        self.codec = None
        self.jpeg = None
        self.failed = False
        self.cond = threading.Condition()
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/snapshot.jpg"):
                    j = hub.jpeg
                    if not j:
                        self.send_error(503)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(j)))
                    self.end_headers()
                    self.wfile.write(j)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last = None
                try:
                    while True:
                        with hub.cond:
                            hub.cond.wait_for(lambda: hub.jpeg is not last, timeout=2)
                            j = last = hub.jpeg
                        if j is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(j)).encode() + b"\r\n\r\n" + j + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.http.daemon_threads = True
        threading.Thread(target=self.http.serve_forever, name="mjpeg", daemon=True).start()

    def _build(self, codec):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        desc = (f"appsrc name=src is-live=true format=time do-timestamp=true caps={_caps(codec)} "
                f"! {codec}parse ! decodebin ! videoconvert ! videoscale "
                f"! video/x-raw,width={self.width},pixel-aspect-ratio=1/1 ! jpegenc quality=82 "
                f"! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false")
        try:
            self.pipeline = Gst.parse_launch(desc)
        except GLib.Error as e:
            self._fail(f"cannot build the decoder ({e.message}); install gstreamer1.0-plugins-bad and gstreamer1.0-libav")
            return
        self.src = self.pipeline.get_by_name("src")
        self.pipeline.get_by_name("sink").connect("new-sample", self._sample)
        self.pipeline.get_bus().set_sync_handler(self._bus)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.codec = codec

    def _bus(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            threading.Thread(target=self._fail, args=(msg.parse_error()[0].message,), daemon=True).start()
        return Gst.BusSyncReply.PASS

    def _fail(self, why):
        if not self.failed:
            self.failed = True
            self.log(f"Video preview off: {why}")
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

    def _sample(self, sink):
        s = sink.emit("pull-sample")
        if s:
            buf = s.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                with self.cond:
                    self.jpeg = bytes(info.data)
                    self.cond.notify_all()
                buf.unmap(info)
        return Gst.FlowReturn.OK

    def push(self, f, codec):
        if self.failed:
            return
        if codec != self.codec:
            if not f.key:
                return
            self._build(codec)
            if self.failed:
                return
        self.src.emit("push-buffer", _buffer(f.data))


class Media:
    """Photos (latest preview JPEG) and videos (stream passed through into an MP4) on this computer."""

    def __init__(self, folder, log, preview=None):
        self.folder, self.log, self.preview = folder, log, preview
        self.last_path, self.last_mime, self.last_at = "", "", 0
        self.rec = None
        self.rec_src = None
        self.waiting_key = False
        self.rec_path = ""

    def save_photo(self):
        j = self.preview.jpeg if self.preview else None
        if not j:
            return "No live video to save on this computer."
        os.makedirs(os.path.join(self.folder, "Pictures"), exist_ok=True)
        path = os.path.join(self.folder, "Pictures", time.strftime("UGVUAV_%Y%m%d_%H%M%S.jpg"))
        with open(path, "wb") as fh:
            fh.write(j)
        self._saved(path, "image/jpeg")
        return ""

    def start_recording(self):
        if not HAVE_GST or self.rec:
            return
        self.waiting_key = True
        self.rec = "pending"

    def push(self, f, codec):
        if self.rec is None:
            return
        if self.rec == "pending":
            if not f.key:
                return
            os.makedirs(os.path.join(self.folder, "Videos"), exist_ok=True)
            self.rec_path = os.path.join(self.folder, "Videos", time.strftime("UGVUAV_%Y%m%d_%H%M%S.mp4"))
            desc = (f"appsrc name=src is-live=true format=time do-timestamp=true caps={_caps(codec)} "
                    f"! {codec}parse ! mp4mux ! filesink location=\"{self.rec_path}\"")
            try:
                self.rec = Gst.parse_launch(desc)
            except GLib.Error as e:
                self.rec = None
                self.log(f"Cannot record: {e.message}")
                return
            self.rec_src = self.rec.get_by_name("src")
            self.rec.set_state(Gst.State.PLAYING)
            self.log(f"Recording to {self.rec_path}")
        self.rec_src.emit("push-buffer", _buffer(f.data))

    def stop_recording(self):
        rec, self.rec = self.rec, None
        if rec is None or rec == "pending":
            return
        self.rec_src.emit("end-of-stream")
        rec.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        rec.set_state(Gst.State.NULL)
        self._saved(self.rec_path, "video/mp4")

    def _saved(self, path, mime):
        self.last_path, self.last_mime, self.last_at = path, mime, int(time.monotonic() * 1000)
        self.log(f"Saved: {path}")

    def state(self):
        return {"uri": self.last_path, "mime": self.last_mime, "at": self.last_at, "rec": 1 if self.rec else 0}
