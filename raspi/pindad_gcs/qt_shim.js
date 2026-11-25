// Makes the Qt WebChannel look like the Android app's "Android" JavaScript interface, so the same
// interface (android/app/src/main/assets/ui) runs unchanged in the Raspberry Pi app.
// State is pushed from Python 10 times a second; calls go to Python asynchronously and any error
// message comes back as a toast. The live video is an <img> (MJPEG from Python) behind the page.
(function () {
  'use strict';
  var state = JSON.stringify({
    link: { connected: 0, present: 0, freshAttach: 0, rx: 0, pc: '', pcControl: 0, videoOn: 1 },
    d: { fresh: 0, armed: 0, flyMode: 0, indoor: -1, gpsNum: 0, gpsFine: 0, hasFix: 0, lat: 0, lon: 0, alt: 0, dist: 0,
         hs: 0, vs: 0, roll: 0, pitch: 0, yaw: 0, batV: 0, batPct: -1, remotePct: -1, flightS: 0, gimbalPos: -1 },
    home: null, mission: { state: 'idle', msg: '', total: 0, acked: 0 }, cam: { rec: 0, recS: 0 }, handshake: 'idle',
    media: { uri: '', mime: '', at: 0, rec: 0 }, video: { codec: '', w: 0, h: 0, fps: 0, active: 0, dropped: 0 },
    phone: null, insets: { l: 0, t: 0, r: 0, b: 0 }, logV: 0
  });
  var settings = '{"sticks":1,"video":1}', logText = '', gcs = null, videoUrl = '', fill = false, rect = null;

  function call(name, args, cb) {
    if (!gcs || !gcs[name]) return;
    gcs[name].apply(gcs, (args || []).concat([function (r) { if (cb) cb(r); }]));
  }
  function err(r) { if (r && window.uiToast) window.uiToast(r, 'bad'); }

  function placeVideo() {
    var img = document.getElementById('qtvideo');
    if (!img) return;
    var s = img.style;
    if (rect) {
      s.left = rect[0] + 'px'; s.top = rect[1] + 'px'; s.width = rect[2] + 'px'; s.height = rect[3] + 'px';
      s.objectFit = 'cover'; s.borderRadius = '13px';
    } else {
      s.left = '0'; s.top = '0'; s.width = '100%'; s.height = '100%';
      s.objectFit = fill ? 'cover' : 'contain'; s.borderRadius = '0';
    }
  }

  window.Android = {
    state: function () { return state; },
    command: function (n) { call('command', [n], err); return ''; },
    mission: function (j) { call('mission', [j], err); return ''; },
    sticks: function (a, b, c, d) { call('sticks', [a, b, c, d]); },
    clearMission: function () { call('clearMission'); },
    connect: function () { call('connect'); },
    log: function () { call('log', [], function (t) { logText = t; }); return logText; },
    settings: function () { call('settings', [], function (t) { settings = t; }); return settings; },
    set: function (k, v) {
      if (k === 'fill') { fill = v === '1'; placeVideo(); }
      call('set', [k, v]);
    },
    videoRect: function (x, y, w, h) { rect = w > 0 && h > 0 ? [x, y, w, h] : null; placeVideo(); },
    haptic: function () {},
    snapshot: function () { call('snapshot', [], err); return ''; },
    openLast: function () { call('openLast', [], err); return ''; }
  };

  function connectChannel() {
    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) {
      setTimeout(connectChannel, 50);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      gcs = channel.objects.gcs;
      gcs.stateChanged.connect(function (s) { state = s; });
      gcs.toast.connect(function (msg, kind) { if (window.uiToast) window.uiToast(msg, kind); });
      call('settings', [], function (t) { settings = t; });
      call('videoUrl', [], function (u) { videoUrl = u; addVideo(); });
    });
  }

  function addVideo() {
    if (!videoUrl || !document.body || document.getElementById('qtvideo')) return;
    var img = document.createElement('img');
    img.id = 'qtvideo';
    img.src = videoUrl;
    img.alt = '';
    img.style.cssText = 'position:fixed;background:#000;pointer-events:none;';
    document.body.insertBefore(img, document.body.firstChild);
    img.onerror = function () { setTimeout(function () { img.src = videoUrl + '?t=' + Date.now(); }, 2000); };
    placeVideo();
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Texts of the connect card for the Raspberry Pi
    var s2 = document.querySelector('#cs-2'), s3 = document.querySelector('#cs-3');
    if (s2) s2.lastChild.textContent = 'Plug the remote into the Raspberry Pi (USB-C), or start the phone app link';
    if (s3) s3.lastChild.textContent = 'The Raspberry Pi connects by itself';
    var b = document.querySelector('#cn-connect');
    if (b) b.style.display = 'none';
    addVideo();
  });
  connectChannel();
})();
