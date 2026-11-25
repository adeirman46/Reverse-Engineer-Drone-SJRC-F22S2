"""Qt window: the same interface as the Android app (HTML/CSS/JS) in a QWebEngineView.

Works with PySide6 (pip) or PyQt5 (Raspberry Pi OS: apt install python3-pyqt5.qtwebengine).
"""
import json
import os
import socket
import subprocess
import time

try:
    from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QColor
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
    QT = "PySide6"
except ImportError:
    from PyQt5.QtCore import QFile, QIODevice, QObject, QTimer, QUrl
    from PyQt5.QtCore import pyqtSignal as Signal, pyqtSlot as Slot
    from PyQt5.QtGui import QColor
    from PyQt5.QtWebChannel import QWebChannel
    from PyQt5.QtWebEngineWidgets import QWebEngineScript, QWebEngineSettings, QWebEngineView
    from PyQt5.QtWidgets import QApplication
    QT = "PyQt5"

from . import __version__


def local_addresses():
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout.split()
        return [a for a in out if ":" not in a]
    except (OSError, subprocess.TimeoutExpired):
        return [socket.gethostbyname(socket.gethostname())]


class Bridge(QObject):
    """Object "gcs" on the page's WebChannel; mirrors MainActivity.Api of the Android app."""
    stateChanged = Signal(str)
    toast = Signal(str, str)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def state_json(self):
        c = self.ctx
        link, flight, tel = c["link"], c["link"].flight, c["link"].tel
        d = tel.snapshot()
        s = {
            "link": {"connected": int(link.connected), "present": int(link.connected), "freshAttach": 0,
                     "rx": link.rx_bytes, "pc": "", "pcControl": 0 if flight.enabled else 1,
                     "videoOn": int(link.video_enabled)},
            "d": d,
            "home": {"lat": tel.home[0], "lon": tel.home[1]} if tel.home else None,
            "mission": flight.mission,
            "cam": {"rec": int(flight.recording),
                    "recS": time.monotonic() - flight.record_start if flight.recording else 0},
            "handshake": flight.handshake,
            "media": c["media"].state(),
            "video": c["hub"].state(),
            "phone": None,
            "insets": {"l": 0, "t": 0, "r": 0, "b": 0},
            "logV": c["log"].version,
        }
        return json.dumps(s)

    @Slot(str, result=str)
    def command(self, name):
        if name == "photo":
            err = self.ctx["link"].flight.command("photo") or ""
            saved = self.ctx["media"].save_photo()
            return err or saved
        return self.ctx["link"].flight.command(name) or ""

    @Slot(str, result=str)
    def mission(self, text):
        try:
            o = json.loads(text)
            pts = [(float(p[0]), float(p[1])) for p in o["points"]]
            return self.ctx["link"].flight.start_mission(pts, float(o["alt"]), int(o["speed"]), int(o["hover"])) or ""
        except (ValueError, KeyError, TypeError) as e:
            return f"Bad mission data: {e}"

    @Slot(float, float, float, float)
    def sticks(self, yaw, thr, roll, pitch):
        self.ctx["link"].flight.set_sticks(yaw, thr, roll, pitch)

    @Slot()
    def clearMission(self):
        self.ctx["link"].flight.clear_mission()

    @Slot()
    def connect(self):
        pass  # the Raspberry Pi connects by itself

    @Slot(result=str)
    def log(self):
        return self.ctx["log"].text()

    @Slot(result=str)
    def videoUrl(self):
        return f"http://127.0.0.1:{self.ctx['args'].preview_port}/video.mjpg" if self.ctx["preview"] else ""

    @Slot(result=str)
    def settings(self):
        a, link = self.ctx["args"], self.ctx["link"]
        addr = ", ".join(local_addresses())
        where = (f"RTSP rtsp://{local_addresses()[0] if local_addresses() else '<pi>'}:{a.rtsp_port}{a.rtsp_path}"
                 if self.ctx["rtsp"] else "RTSP off")
        return json.dumps({"video": int(link.video_enabled), "fill": 0, "pcControl": 0 if link.flight.enabled else 1,
                           "pcVideo": 0, "sticks": int(link.flight.sticks_enabled), "stickPriority": 0, "port": 0,
                           "addresses": f"{addr}  ·  {where}", "version": f"{__version__} (Raspberry Pi, {QT})"})

    @Slot(str, str)
    def set(self, key, value):
        on = value in ("1", "true")
        link = self.ctx["link"]
        if key == "video":
            link.video_enabled = on
        elif key == "sticks":
            link.flight.sticks_enabled = on
        elif key == "pcControl":
            link.flight.enabled = not on

    @Slot(result=str)
    def snapshot(self):
        return ""  # the photo command already saves the picture on this computer

    @Slot(result=str)
    def openLast(self):
        path = self.ctx["media"].last_path
        if not path:
            return "Nothing saved yet."
        try:
            subprocess.Popen(["xdg-open", path])
            return ""
        except OSError as e:
            return f"Could not open it: {e}"


def _script(name, source):
    s = QWebEngineScript()
    s.setName(name)
    s.setSourceCode(source)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    return s


def run_ui(ctx):
    args = ctx["args"]
    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    page = view.page()
    page.setBackgroundColor(QColor("black"))
    st = page.settings()
    st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

    f = QFile(":/qtwebchannel/qwebchannel.js")
    f.open(QIODevice.OpenModeFlag.ReadOnly)
    webchannel_js = bytes(f.readAll()).decode()
    f.close()
    shim = open(os.path.join(os.path.dirname(__file__), "qt_shim.js"), encoding="utf-8").read()
    page.scripts().insert(_script("qwebchannel", webchannel_js))
    page.scripts().insert(_script("android-shim", shim))

    bridge = Bridge(ctx)
    channel = QWebChannel(page)
    channel.registerObject("gcs", bridge)
    page.setWebChannel(channel)

    timer = QTimer()
    timer.timeout.connect(lambda: bridge.stateChanged.emit(bridge.state_json()))
    timer.start(100)

    index = os.path.join(args.ui_dir, "index.html")
    view.setWindowTitle("Pindad UGV - UAV")
    view.load(QUrl.fromLocalFile(os.path.abspath(index)))
    view.resize(1280, 720)
    if args.fullscreen:
        view.showFullScreen()
    else:
        view.show()
    ctx["view"] = view  # keep references alive
    ctx["timer"] = timer
    ctx["bridge"] = bridge
    if args.screenshot:  # for documentation/tests: grab the window after a few seconds and quit
        def grab():
            view.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(int(args.screenshot_after * 1000), grab)
    return app.exec() if hasattr(app, "exec") else app.exec_()
