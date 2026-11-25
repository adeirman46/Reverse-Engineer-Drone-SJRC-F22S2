/* Pindad UGV - UAV cockpit.
 * Talks to the app through window.Android (MainActivity.Api); state() is polled 10 times a second.
 * In a desktop browser it runs a demo with simulated data. Preview switches (query string):
 *   ?demo=connect|replug|plugged  ?armed=0|1|2  ?indoor=1  ?warn=1  ?rec=1  ?novideo=1
 *   ?main=map  ?view=plan  ?plan=demo  ?mission=uploading|running|failed  ?panel=settings
 *   ?confirm=fly|rth|motors|mission  ?menu=1  ?touch=1  ?sticks=0 */
'use strict';
(function () {
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const query = new URLSearchParams(location.search);
  const DEMO = !window.Android;
  const api = DEMO ? demoApi() : window.Android;
  const app = $('#app');
  if (DEMO) document.body.classList.add('demo');

  const FLY_MODES = { 1: 'Returning home', 2: 'Follow me', 3: 'Orbit', 4: 'Waypoint mission', 9: 'Cruise', 11: 'Comet' };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } }
  };
  const cfg = { mode: store.get('stickMode', 2), sens: store.get('stickSens', 0.55), layer: store.get('layer', 'sat'), sticks: true };

  let S = null;                  // latest state from the app
  let view = 'fly';              // 'fly' or 'plan'
  let main = 'video';            // fly view: 'video' or 'map' in the big area
  let connectDismissed = false, wasConnected = false, lastLogV = -1;

  // ---------------- helpers ----------------
  const icon = id => `<svg class="i"><use href="#${id}"/></svg>`;
  const setText = (el, v) => { if (el && el.textContent !== v) el.textContent = v; };
  const setClass = (el, c, on) => { if (el) el.classList.toggle(c, !!on); };
  const isNum = v => typeof v === 'number' && isFinite(v);
  const f1 = v => !isNum(v) ? '--' : (Math.abs(v) < 0.05 ? '0.0' : v.toFixed(1));
  const f0 = v => !isNum(v) ? '--' : String(Math.round(v));
  const mmss = s => { s = Math.max(0, Math.floor(s || 0)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
  const fmtDist = m => m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m';
  const CARD = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

  function haversine(a, b) {
    const r = 6371000, p1 = a[0] * Math.PI / 180, p2 = b[0] * Math.PI / 180;
    const dp = p2 - p1, dl = (b[1] - a[1]) * Math.PI / 180;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * r * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  }
  function haptic() { try { api.haptic(); } catch (e) { /* not available */ } }
  function toast(msg, kind) {
    const box = $('#toasts');
    const t = document.createElement('div');
    t.className = 'toast ' + (kind || '');
    t.textContent = msg;
    box.appendChild(t);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, kind === 'bad' ? 4000 : 2400);
  }
  /** Sends a command; shows the reason when the app refuses it. */
  function run(name, okMsg) {
    let r = '';
    try { r = api.command(name) || ''; } catch (e) { r = String(e); }
    if (r) toast(r, 'bad'); else if (okMsg) toast(okMsg, 'ok');
    return !r;
  }
  function ready(needDrone) {
    if (!S || !S.link.connected) { toast('Connect the remote first.', 'bad'); connectDismissed = false; return false; }
    if (S.link.pcControl) { toast('PC control is on. Turn it off in Settings to fly from the app.', 'bad'); return false; }
    if (needDrone && !S.d.fresh) { toast('No live data from the drone. Is it switched on?', 'bad'); return false; }
    return true;
  }

  // ---------------- slide to confirm (top centre) ----------------
  let confirmCb = null, drag = null, knobX = 0;
  const slider = $('#cf-slider'), knob = $('#cf-knob'), fill = $('#cf-fill');
  const maxX = () => slider.clientWidth - 44;
  function setKnob(x) {
    knobX = Math.max(0, Math.min(maxX(), x));
    knob.style.transform = `translateX(${knobX}px)`;
    fill.style.width = (knobX + 44) + 'px';
  }
  knob.addEventListener('pointerdown', e => { drag = { x: e.clientX - knobX }; slider.classList.remove('anim'); knob.setPointerCapture(e.pointerId); });
  knob.addEventListener('pointermove', e => { if (drag) setKnob(e.clientX - drag.x); });
  function endDrag() {
    if (!drag) return;
    drag = null;
    if (knobX >= maxX() - 6) {
      const cb = confirmCb;
      haptic();
      closeConfirm();
      if (cb) cb();
    } else {
      slider.classList.add('anim');
      setKnob(0);
    }
  }
  knob.addEventListener('pointerup', endDrag);
  knob.addEventListener('pointercancel', endDrag);
  function confirmAction(o) {
    $('#cf-title').textContent = o.title;
    $('#cf-text').textContent = o.text;
    const ic = $('#cf-icon');
    ic.className = 'cf-ic ' + (o.kind === 'ok' ? '' : o.kind);
    ic.innerHTML = icon(o.icon);
    slider.className = 'slider ' + (o.kind === 'ok' ? '' : o.kind);
    $('#cf-label').textContent = o.label || 'Slide to confirm';
    confirmCb = o.action;
    $('#confirm').classList.add('show');
    app.classList.add('confirming');
    requestAnimationFrame(() => setKnob(0));
  }
  function closeConfirm() { $('#confirm').classList.remove('show'); app.classList.remove('confirming'); confirmCb = null; }
  $('#cf-cancel').onclick = closeConfirm;

  // ---------------- flight actions ----------------
  function actMotors() {
    if (!ready(true)) return;
    if (S.d.armed !== 0) { toast('The motors are already running. To stop them on the ground, hold the remote\'s left stick down for 2 s.'); return; }
    confirmAction({
      title: 'Start the motors?', icon: 'i-prop', kind: 'warn', label: 'Slide to start motors',
      text: 'The propellers spin. Keep people and hands clear. The drone stays on the ground until you climb or take off.',
      action: () => run('unlock', 'Start motors sent')
    });
  }
  function actFly() {
    if (!ready(true)) return;
    const d = S.d;
    if (d.armed === 2) {
      confirmAction({ title: 'Land now?', icon: 'i-land', kind: 'warn', label: 'Slide to land',
        text: 'The drone descends and lands where it is. Make sure the area below it is clear.', action: () => run('land', 'Landing') });
    } else {
      confirmAction({ title: 'Take off?', icon: 'i-takeoff', kind: 'ok', label: 'Slide to take off',
        text: (d.armed === 0 ? 'Starts the motors, then takes off, ' : 'Takes off, ') + 'climbs a little and hovers. ' +
          (d.indoor === 1 ? 'Attitude mode: it does not hold position against wind.' : 'GPS mode: it holds its position.'),
        action: () => run('takeoff', 'Take off sent') });
    }
  }
  function rthReason(d) {
    if (d.armed !== 2) return 'Return to home works in flight only.';
    if (d.indoor === 1) return 'Return to home needs GPS mode: hold the GPS button on the remote for 3 s.';
    if (!d.gpsFine) return 'Return to home needs a GPS fix.';
    return '';
  }
  function actRth() {
    if (!ready(true)) return;
    const why = rthReason(S.d);
    if (why) { toast(why, 'bad'); return; }
    confirmAction({ title: 'Return to home?', icon: 'i-home', kind: 'warn', label: 'Slide to return home',
      text: `Climbs to the return height, flies back to the take-off point (${fmtDist(S.d.dist || 0)} away) and lands.`,
      action: () => run('rth', 'Returning home') });
  }
  function actStop() { if (ready(false)) run('cancel', 'Automatic flight stopped. The drone hovers.'); }
  // flight mode sheet: GPS / attitude, normal / sport, and the automatic actions
  function openModes() { $('#modesheet').classList.add('show'); renderModes(S); }
  function closeModes() { $('#modesheet').classList.remove('show'); }
  $('#pill-state').onclick = () => ($('#modesheet').classList.contains('show') ? closeModes() : openModes());
  $('#mode-x').onclick = closeModes;
  function renderModes(s) {
    if (!s || !$('#modesheet').classList.contains('show')) return;
    const d = s.d, live = s.link.connected && d.fresh;
    const pos = live ? String(d.indoor) : '';
    $$('#mode-pos button').forEach(b => setClass(b, 'on', b.dataset.v === pos));
    $$('#mode-speed button').forEach(b => setClass(b, 'on', live && b.dataset.v === String(d.sport || 0)));
    setText($('#mode-now'), !live ? 'Not connected to the drone' : d.indoor === 1
      ? 'Attitude: holds altitude only, drifts with wind. Indoor use.'
      : `GPS: holds position. ${d.gpsNum} satellites${d.gpsFine ? ', fix OK' : ', no fix yet'}.`);
  }
  // Tested 2026-11-25: the drone takes these two only from the remote's own buttons, so the app
  // shows the current state and says which button to press.
  $$('#mode-pos button').forEach(b => {
    b.onclick = () => toast(b.dataset.v === '1'
      ? 'Attitude mode: hold the GPS button on the remote for 3 s (rear light flashes blue).'
      : 'GPS mode: hold the GPS button on the remote for 3 s again. Needs open sky.');
  });
  $$('#mode-speed button').forEach(b => {
    b.onclick = () => toast('Speed: press the speed button on the remote.');
  });
  $('#mode-rth').onclick = () => { closeModes(); actRth(); };
  $('#mode-stop').onclick = () => { closeModes(); actStop(); };
  $('#mode-land').onclick = () => {
    closeModes();
    if (S && S.d.armed === 2) actFly(); else toast('The drone is not flying.', 'bad');
  };

  $('#btn-fly').onclick = actFly;
  $('#btn-rth').onclick = actRth;
  $('#btn-stop').onclick = actStop;
  $('#set-motors').onclick = () => { closeSettings(); actMotors(); };

  // ---------------- camera ----------------
  $('#btn-shutter').onclick = () => {
    if (!S || !S.link.connected) { toast('Connect the remote first.', 'bad'); return; }
    if (!run('photo')) return;  // full-quality photo on the drone's SD card
    let r = '';
    try { r = api.snapshot() || ''; } catch (e) { r = String(e); }  // copy of the live picture into the phone gallery
    const f = $('#flash');
    f.classList.remove('go'); void f.offsetWidth; f.classList.add('go');
    haptic();
    if (r) toast(r + ' The drone still saved it on its SD card.', 'bad');
  };
  // "Saved to Gallery · Open" chip after each photo or video saved on the phone
  let lastMediaAt = null, savedTimer = null;
  function renderMedia(s) {
    const m = s.media;
    if (!m) return;
    if (lastMediaAt === null) { lastMediaAt = m.at; return; }
    if (m.at === lastMediaAt) return;
    lastMediaAt = m.at;
    setText($('#saved-text'), (m.mime.indexOf('video') === 0 ? 'Video' : 'Photo') + ' saved to Gallery');
    $('#saved').classList.add('show');
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => $('#saved').classList.remove('show'), 7000);
  }
  function openLast() {
    let r = '';
    try { r = api.openLast() || ''; } catch (e) { r = String(e); }
    if (r) toast(r, 'bad');
  }
  $('#saved').onclick = () => { $('#saved').classList.remove('show'); openLast(); };
  $('#set-open').onclick = openLast;
  $('#btn-record').onclick = () => {
    if (!S || !S.link.connected) { toast('Connect the remote first.', 'bad'); return; }
    if (run('record')) haptic();
  };
  /** Press = one step; hold = keep repeating (gimbal tilt steps, drone turn while held). */
  function holdButton(sel, name, every) {
    const b = $(sel);
    let timer = null;
    const stop = () => { if (timer) { clearInterval(timer); timer = null; } b.classList.remove('held'); };
    b.addEventListener('pointerdown', e => {
      if (!S || !S.link.connected) { toast('Connect the remote first.', 'bad'); return; }
      b.setPointerCapture(e.pointerId);
      b.classList.add('held');
      if (!run(name)) { stop(); return; }
      timer = setInterval(() => { if (!run(name)) stop(); }, every);
    });
    b.addEventListener('pointerup', stop);
    b.addEventListener('pointercancel', stop);
    b.addEventListener('lostpointercapture', stop);
  }
  holdButton('#g-up', 'gimbalUp', 250);
  holdButton('#g-down', 'gimbalDown', 250);
  holdButton('#g-left', 'yawLeft', 300);
  holdButton('#g-right', 'yawRight', 300);

  // ---------------- virtual thumb sticks ----------------
  const stick = { l: [0, 0], r: [0, 0], active: 0 };
  let lastStickSend = 0, keepAlive = null;
  function shape(v) { const a = Math.min(1, Math.abs(v)), e = 0.35; return Math.sign(v) * (e * a * a * a + (1 - e) * a) * cfg.sens; }
  function axes() {  // yaw, throttle, roll, pitch
    const L = stick.l, R = stick.r;
    return cfg.mode === 1 ? [L[0], R[1], R[0], L[1]] : [L[0], L[1], R[0], R[1]];
  }
  function sendSticks(force) {
    const now = performance.now();
    if (!force && now - lastStickSend < 50) return;
    lastStickSend = now;
    const a = axes().map(shape);
    try { api.sticks(a[0], a[1], a[2], a[3]); } catch (e) { /* demo */ }
  }
  function makeStick(el, key) {
    const k = $('.knob', el);
    let id = null, cx = 0, cy = 0, r = 1;
    function move(e) {
      let dx = e.clientX - cx, dy = e.clientY - cy;
      const d = Math.hypot(dx, dy);
      if (d > r) { dx *= r / d; dy *= r / d; }
      k.style.transform = `translate(${dx}px, ${dy}px)`;
      stick[key] = [dx / r, -dy / r];
      sendSticks(false);
    }
    el.addEventListener('pointerdown', e => {
      if (id !== null) return;
      id = e.pointerId;
      el.setPointerCapture(id);
      const b = el.getBoundingClientRect();
      cx = b.left + b.width / 2; cy = b.top + b.height / 2; r = b.width / 2 - k.offsetWidth / 2;
      el.classList.add('active');
      stick.active++;
      if (stick.active === 1) keepAlive = setInterval(() => sendSticks(true), 150);
      haptic();
      move(e);
    });
    el.addEventListener('pointermove', e => { if (e.pointerId === id) move(e); });
    const end = e => {
      if (e.pointerId !== id) return;
      id = null;
      el.classList.remove('active');
      k.style.transform = '';
      stick[key] = [0, 0];
      stick.active = Math.max(0, stick.active - 1);
      sendSticks(true);
      if (!stick.active) { clearInterval(keepAlive); keepAlive = null; setTimeout(() => sendSticks(true), 60); }
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('lostpointercapture', end);
  }
  makeStick($('#stick-l'), 'l');
  makeStick($('#stick-r'), 'r');
  function labelSticks() {
    const set = (sel, t, b, l, r) => { const s = $(sel); $('.ax.t', s).textContent = t; $('.ax.b', s).textContent = b; $('.ax.l', s).textContent = l; $('.ax.r', s).textContent = r; };
    if (cfg.mode === 1) { set('#stick-l', 'FWD', 'BACK', '⟲', '⟳'); set('#stick-r', 'UP', 'DOWN', 'L', 'R'); }
    else { set('#stick-l', 'UP', 'DOWN', '⟲', '⟳'); set('#stick-r', 'FWD', 'BACK', 'L', 'R'); }
  }
  function setSticksShown(on, tell) {
    cfg.sticks = on;
    setClass(app, 'sticks-on', on);
    setClass($('#btn-sticks'), 'on', on);
    if (tell) { try { api.set('sticks', on ? '1' : '0'); } catch (e) { /* demo */ } }
  }
  $('#btn-sticks').onclick = () => { setSticksShown(!cfg.sticks, true); toast(cfg.sticks ? 'Virtual sticks on' : 'Virtual sticks off'); };

  // ---------------- heading tape & horizon ----------------
  const PX_DEG = 2.3;
  (function buildTape() {
    const strip = $('#tape-strip');
    let html = '';
    for (let a = -360; a <= 720; a += 10) {
      const deg = ((a % 360) + 360) % 360;
      html += `<i class="tk${deg % 30 === 0 ? ' mid' : ''}" style="left:${a * PX_DEG}px"></i>`;
      if (deg % 30 === 0) {
        const lab = { 0: 'N', 90: 'E', 180: 'S', 270: 'W' }[deg];
        html += `<span class="tl${lab ? ' cardinal' : ''}" style="left:${a * PX_DEG}px">${lab || deg}</span>`;
      }
    }
    strip.innerHTML = html;
    let lad = '<div class="hl"></div>';
    for (const p of [-20, -10, 10, 20]) lad += `<div class="lad" style="top:${-p * 3.2}px"></div>`;
    $('#horizon').innerHTML = lad;
  })();

  // ---------------- map ----------------
  let map, layers, curLayer, droneMk, homeMk, meMk, trail, homeLine, distMk, planLine, targetMk, scale;
  let trailPts = [], follow = true, firstFix = true, centeredOnPhone = false;

  function initMap() {
    map = L.map('map', { zoomControl: false, attributionControl: true, worldCopyJump: true, zoomSnap: 0.5 });
    layers = {
      sat: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 20, maxNativeZoom: 19, attribution: 'Imagery &copy; Esri' }),
      street: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 20, maxNativeZoom: 19, attribution: '&copy; OpenStreetMap contributors' })
    };
    setLayer(cfg.layer);
    const v = store.get('view', null);
    if (v && !DEMO) map.setView([v.lat, v.lon], v.z); else map.setView([-2.5, 118], 4);
    droneMk = L.marker([0, 0], { interactive: false, zIndexOffset: 1000,
      icon: L.divIcon({ className: 'mk-drone', html: '<svg viewBox="0 0 40 40"><use href="#i-arrow"/></svg>', iconSize: [36, 36], iconAnchor: [18, 18] }) });
    homeMk = L.marker([0, 0], { interactive: false, icon: L.divIcon({ className: '', html: '<div class="mk-home">H</div>', iconSize: [24, 24], iconAnchor: [12, 12] }) });
    meMk = L.marker([0, 0], { interactive: false, icon: L.divIcon({ className: '', html: '<div class="mk-me"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }) });
    targetMk = L.marker([0, 0], { interactive: false, icon: L.divIcon({ className: '', html: '<div class="mk-target"></div>', iconSize: [30, 30], iconAnchor: [15, 15] }) });
    trail = L.polyline([], { color: '#2ee6a6', weight: 3, opacity: 0.85 }).addTo(map);
    homeLine = L.polyline([], { color: '#ffb547', weight: 2, opacity: 0.9, dashArray: '4 6' }).addTo(map);
    distMk = L.marker([0, 0], { interactive: false, icon: L.divIcon({ className: 'dist-label', html: '', iconSize: null }) });
    planLine = L.polyline([], { color: '#ffffff', weight: 2.5, opacity: 0.9, dashArray: '7 7' }).addTo(map);
    scale = L.control.scale({ imperial: false, position: 'bottomleft' });
    map.on('click', onMapClick);
    map.on('dragstart', () => { follow = false; setClass($('#mt-follow'), 'active', false); hideMenu(); });
    map.on('moveend', () => { const c = map.getCenter(); store.set('view', { lat: c.lat, lon: c.lng, z: map.getZoom() }); });
    setInteractive(false);
  }
  function setLayer(name) {
    if (!layers[name]) name = 'sat';
    if (curLayer) map.removeLayer(curLayer);
    curLayer = layers[name].addTo(map);
    curLayer.bringToBack();
    cfg.layer = name;
    store.set('layer', name);
    $$('#set-layer button').forEach(b => setClass(b, 'on', b.dataset.v === name));
  }
  function toggleLayer() { setLayer(cfg.layer === 'sat' ? 'street' : 'sat'); }
  function setInteractive(on) {
    ['dragging', 'touchZoom', 'doubleClickZoom', 'scrollWheelZoom', 'boxZoom', 'keyboard'].forEach(k => { if (map[k]) { if (on) map[k].enable(); else map[k].disable(); } });
  }

  /** Puts the map and the native video where the current view wants them. */
  function layout() {
    if (!map) return;
    const mw = $('#mapwrap');
    const r = $('#pip').getBoundingClientRect();
    if (view === 'fly' && main === 'video') {
      Object.assign(mw.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', clipPath: '' });
      videoRect(0, 0, 0, 0);
    } else if (view === 'fly') {
      const W = innerWidth, H = innerHeight, x = r.left, y = r.top, w = r.width, h = r.height, k = 13;
      mw.style.clipPath = `path(evenodd, "M0 0H${W}V${H}H0Z M${x + k} ${y}H${x + w - k}A${k} ${k} 0 0 1 ${x + w} ${y + k}V${y + h - k}` +
        `A${k} ${k} 0 0 1 ${x + w - k} ${y + h}H${x + k}A${k} ${k} 0 0 1 ${x} ${y + h - k}V${y + k}A${k} ${k} 0 0 1 ${x + k} ${y}Z")`;
      videoRect(x, y, w, h);
    } else {
      mw.style.clipPath = '';
      videoRect(0, 0, 0, 0);
    }
    map.invalidateSize({ pan: false });
  }
  let lastRect = '';
  function videoRect(x, y, w, h) {
    const key = [x, y, w, h].map(Math.round).join(',');
    if (key === lastRect) return;
    lastRect = key;
    try { api.videoRect(x, y, w, h); } catch (e) { /* demo */ }
  }
  window.addEventListener('resize', layout);

  function setMain(m) {
    main = m;
    setClass(app, 'main-map', m === 'map');
    setClass(app, 'main-video', m === 'video');
    setInteractive(m === 'map');
    $('#pip-tag').innerHTML = m === 'map' ? icon('i-video') + 'VIDEO' : icon('i-map') + 'MAP';
    hideMenu();
    follow = true;
    setClass($('#mt-follow'), 'active', true);
    layout();
    centerOnDrone(m === 'map' ? 17 : null);
  }
  function setView(v) {
    view = v;
    setClass(app, 'view-plan', v === 'plan');
    setClass(app, 'view-fly', v === 'fly');
    $('#btn-plan').innerHTML = v === 'plan' ? icon('i-video') + '<span>Fly</span>' : icon('i-route') + '<span>Plan</span>';
    hideMenu();
    if (v === 'plan') {
      setInteractive(true);
      scale.addTo(map);
      layout();
      fitPlan();
    } else {
      scale.remove();
      setInteractive(main === 'map');
      layout();
      follow = true;
      centerOnDrone(null);
    }
    drawPlan();
  }
  $('#pip').onclick = () => setMain(main === 'video' ? 'map' : 'video');
  $('#btn-plan').onclick = () => setView(view === 'plan' ? 'fly' : 'plan');

  function centerOnDrone(zoom) {
    if (S && S.d.hasFix) map.setView([S.d.lat, S.d.lon], zoom ? Math.max(map.getZoom(), zoom) : map.getZoom(), { animate: false });
  }
  function centerOnMe() {
    if (S && S.phone) { follow = false; setClass($('#mt-follow'), 'active', false); map.setView([S.phone.lat, S.phone.lon], Math.max(map.getZoom(), 17)); }
    else toast('Phone position not available (location permission or GPS off).');
  }
  $('#mt-follow').onclick = () => {
    follow = true;
    setClass($('#mt-follow'), 'active', true);
    if (S && S.d.hasFix) centerOnDrone(17); else toast('No drone position yet.');
  };
  $('#mt-me').onclick = centerOnMe;
  $('#mt-layer').onclick = toggleLayer;
  $('#pt-drone').onclick = () => { if (S && S.d.hasFix) centerOnDrone(17); else toast('No drone position yet.'); };
  $('#pt-me').onclick = centerOnMe;
  $('#pt-layer').onclick = toggleLayer;
  $('#pt-fit').onclick = fitPlan;
  $$('#set-layer button').forEach(b => { b.onclick = () => setLayer(b.dataset.v); });

  // tap on the big map (fly view): fly here
  let menuLL = null;
  function onMapClick(e) {
    if (view === 'plan') { addWaypoint(e.latlng); return; }
    if (main !== 'map') return;
    menuLL = e.latlng;
    const p = map.latLngToContainerPoint(e.latlng);
    const m = $('#mapmenu');
    m.style.left = Math.max(90, Math.min(innerWidth - 90, p.x)) + 'px';
    m.style.top = p.y + 'px';
    setClass(m, 'below', p.y < 150);  // near the top edge: open below the point
    const d = S && S.d.hasFix ? haversine([S.d.lat, S.d.lon], [e.latlng.lat, e.latlng.lng]) : null;
    setText($('#mm-title'), d == null ? 'THIS POINT' : `${fmtDist(d)} FROM THE DRONE`);
    m.classList.add('show');
    targetMk.setLatLng(e.latlng).addTo(map);
  }
  function hideMenu() { $('#mapmenu').classList.remove('show'); if (targetMk && map && map.hasLayer(targetMk) && !flyHereActive()) targetMk.remove(); }
  function flyHereActive() { return S && S.mission && S.mission.total === 1 && (S.mission.state === 'uploading' || S.mission.state === 'running'); }
  $('#mm-x').onclick = hideMenu;
  $('#mm-go').onclick = () => {
    const ll = menuLL;
    $('#mapmenu').classList.remove('show');
    if (!ll || !ready(true)) { hideMenu(); return; }
    const why = missionReason(S.d);
    if (why) { toast(why, 'bad'); hideMenu(); return; }
    const alt = Math.max(5, Math.min(120, Math.round(S.d.alt || 0)));
    const d = haversine([S.d.lat, S.d.lon], [ll.lat, ll.lng]);
    confirmAction({ title: 'Fly to this point?', icon: 'i-flag', kind: 'warn', label: 'Slide to fly there',
      text: `${fmtDist(d)} away, at the current altitude (${alt} m), ${plan.speed} m/s. Tap Stop to end it at any time.`,
      action: () => sendMission([[+ll.lat.toFixed(7), +ll.lng.toFixed(7)]], alt, plan.speed, 0) });
  };

  function renderMap(s) {
    const d = s.d;
    const el = droneMk.getElement(), svg = el && el.querySelector('svg');
    if (svg) svg.style.transform = `rotate(${d.yaw || 0}deg)`;
    if (!(d.hasFix && d.fresh)) {
      // No GPS fix on the drone (e.g. indoors): show it at the phone's position, marked "NO GPS".
      if (s.link.connected && d.fresh && s.phone) {
        if (!map.hasLayer(droneMk)) droneMk.addTo(map);
        droneMk.setLatLng([s.phone.lat, s.phone.lon]);
        setClass(droneMk.getElement(), 'ghost', true);
      } else if (map.hasLayer(droneMk) && !s.link.connected) {
        droneMk.remove();
      }
    }
    if (d.hasFix && d.fresh) {
      const ll = [d.lat, d.lon];
      if (!map.hasLayer(droneMk)) droneMk.addTo(map);
      droneMk.setLatLng(ll);
      setClass(droneMk.getElement(), 'ghost', false);
      const last = trailPts[trailPts.length - 1];
      if (!last || haversine(last, ll) > 1.5) { trailPts.push(ll); if (trailPts.length > 800) trailPts.shift(); trail.setLatLngs(trailPts); }
      if (firstFix) { map.setView(ll, view === 'plan' ? map.getZoom() : 17, { animate: false }); firstFix = false; }
      else if (follow && view === 'fly') map.panTo(ll, { animate: false });
    } else if (s.phone && firstFix && !centeredOnPhone) {
      map.setView([s.phone.lat, s.phone.lon], 16, { animate: false });
      centeredOnPhone = true;
    }
    if (s.home) {
      if (!map.hasLayer(homeMk)) homeMk.addTo(map);
      homeMk.setLatLng([s.home.lat, s.home.lon]);
      if (d.hasFix && (main === 'map' || view === 'plan')) {
        homeLine.setLatLngs([[s.home.lat, s.home.lon], [d.lat, d.lon]]);
        distMk.setLatLng([(s.home.lat + d.lat) / 2, (s.home.lon + d.lon) / 2]);
        if (!map.hasLayer(distMk)) distMk.addTo(map);
        const el = distMk.getElement();
        if (el) el.textContent = fmtDist(haversine([s.home.lat, s.home.lon], [d.lat, d.lon]));
      } else { homeLine.setLatLngs([]); if (map.hasLayer(distMk)) distMk.remove(); }
    }
    if (s.phone) { if (!map.hasLayer(meMk)) meMk.addTo(map); meMk.setLatLng([s.phone.lat, s.phone.lon]); }
    updatePlanLine();
  }

  // ---------------- place search (OpenStreetMap Nominatim) ----------------
  let searchTimer = null, searchPin = null, results = [];
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function openSearch() {
    if (view === 'fly' && main !== 'map') setMain('map');
    $('#search').classList.add('show');
    setTimeout(() => $('#s-input').focus(), 60);
  }
  function closeSearch() { $('#search').classList.remove('show'); $('#s-input').blur(); }
  $('#mt-search').onclick = openSearch;
  $('#pt-search').onclick = openSearch;
  $('#s-x').onclick = closeSearch;
  $('#s-input').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(doSearch, 500); });
  $('#s-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(searchTimer); doSearch(); }
    if (e.key === 'Escape') closeSearch();
  });
  function showResults(list) {
    results = list;
    $('#s-results').innerHTML = list.length
      ? list.map((r, i) => `<button class="sr" data-i="${i}">${esc(r.name)}<small>${esc(r.detail)}</small></button>`).join('')
      : '<div class="sr muted">No places found</div>';
  }
  async function doSearch() {
    const q = $('#s-input').value.trim();
    if (q.length < 2) { $('#s-results').innerHTML = ''; return; }
    const m = q.match(/^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$/);
    if (m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180) {
      showResults([{ name: 'Coordinates', detail: `${+m[1]}, ${+m[2]}`, lat: +m[1], lon: +m[2] }]);
      return;
    }
    $('#s-results').innerHTML = '<div class="sr muted">Searching…</div>';
    try {
      const r = await fetch('https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&q=' + encodeURIComponent(q),
        { headers: { Accept: 'application/json' } });
      const list = await r.json();
      if ($('#s-input').value.trim() !== q) return;  // a newer search is on its way
      showResults(list.map(x => ({ name: x.name || x.display_name.split(',')[0], detail: x.display_name, lat: +x.lat, lon: +x.lon, bbox: x.boundingbox })));
    } catch (e) {
      $('#s-results').innerHTML = '<div class="sr muted">Search needs internet on the phone</div>';
    }
  }
  $('#s-results').addEventListener('click', e => {
    const row = e.target.closest('[data-i]');
    if (!row) return;
    const r = results[+row.dataset.i];
    follow = false;
    setClass($('#mt-follow'), 'active', false);
    if (r.bbox) map.fitBounds([[+r.bbox[0], +r.bbox[2]], [+r.bbox[1], +r.bbox[3]]], { maxZoom: 18 });
    else map.setView([r.lat, r.lon], 17);
    if (searchPin) searchPin.remove();
    searchPin = L.marker([r.lat, r.lon], { interactive: false,
      icon: L.divIcon({ className: '', html: '<div class="mk-search"></div>', iconSize: [18, 18], iconAnchor: [9, 22] }) }).addTo(map);
    closeSearch();
  });

  // ---------------- mission plan ----------------
  const plan = { pts: store.get('plan', []), alt: store.get('plan.alt', 30), speed: store.get('plan.speed', 4), hover: store.get('plan.hover', 2), sel: -1, markers: [] };
  function savePlan() { store.set('plan', plan.pts); store.set('plan.alt', plan.alt); store.set('plan.speed', plan.speed); store.set('plan.hover', plan.hover); }
  function addWaypoint(ll) {
    if (plan.pts.length >= 32) { toast('At most 32 waypoints.', 'bad'); return; }
    plan.pts.push([+ll.lat.toFixed(7), +ll.lng.toFixed(7)]);
    plan.sel = -1;
    haptic();
    drawPlan();
    savePlan();
  }
  function drawPlan() {
    if (!map) return;
    plan.markers.forEach(m => m.remove());
    const editable = view === 'plan';
    plan.markers = plan.pts.map((p, i) => {
      const m = L.marker(p, { draggable: editable, interactive: editable, zIndexOffset: 500,
        icon: L.divIcon({ className: '', html: `<div class="mk-wp${i === plan.sel ? ' sel' : ''}"><b>${i + 1}</b></div>`, iconSize: [26, 26], iconAnchor: [13, 31] }) });
      m.on('drag', e => { const ll = e.target.getLatLng(); plan.pts[i] = [+ll.lat.toFixed(7), +ll.lng.toFixed(7)]; updatePlanLine(); updatePlanInfo(); });
      m.on('dragend', savePlan);
      m.on('click', () => selectWaypoint(plan.sel === i ? -1 : i));
      return m.addTo(map);
    });
    updatePlanLine();
    updatePlanInfo();
  }
  function selectWaypoint(i) {
    plan.sel = i;
    drawPlan();
    if (i >= 0) map.panTo(plan.pts[i]);
  }
  function planStart() {
    if (S && S.d.hasFix) return [S.d.lat, S.d.lon];
    if (S && S.home) return [S.home.lat, S.home.lon];
    return null;
  }
  function updatePlanLine() {
    if (!planLine) return;
    const st = planStart();
    planLine.setLatLngs(plan.pts.length ? (st ? [st].concat(plan.pts) : plan.pts) : []);
  }
  function legs() {
    const st = planStart();
    return plan.pts.map((p, i) => (i === 0 ? (st ? haversine(st, p) : 0) : haversine(plan.pts[i - 1], p)));
  }
  function updatePlanInfo() {
    const n = plan.pts.length, lg = legs();
    const total = lg.reduce((a, b) => a + b, 0);
    setText($('#p-count'), n === 1 ? '1 waypoint' : `${n} waypoints`);
    const t = n ? total / plan.speed + plan.hover * n : 0;
    const st = planStart();
    const far = st && n ? Math.max(...plan.pts.map(p => haversine(st, p))) : 0;
    setText($('#p-sum'), `${fmtDist(total)}  ·  ${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')} min  ·  max ${fmtDist(far)} away`);
    $('#wplist').innerHTML = n ? plan.pts.map((p, i) =>
      `<div class="wp${i === plan.sel ? ' sel' : ''}" data-i="${i}"><span class="n">${i + 1}</span><span class="d num">${i === 0 ? 'from start' : 'leg'} ${fmtDist(lg[i])}</span>` +
      `<button data-del="${i}" title="Delete">${icon('i-close').replace('class="i"', 'class="i sm"')}</button></div>`).join('')
      : '<div class="wp-empty">Tap the map to add waypoints</div>';
    setClass($('#p-fly'), 'off', !n);
  }
  $('#wplist').addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (del) { plan.pts.splice(+del.dataset.del, 1); plan.sel = -1; drawPlan(); savePlan(); return; }
    const row = e.target.closest('.wp');
    if (row) selectWaypoint(+row.dataset.i === plan.sel ? -1 : +row.dataset.i);
  });
  function fitPlan() {
    const pts = plan.pts.slice();
    const st = planStart();
    if (st) pts.push(st);
    if (pts.length > 1) map.fitBounds(pts, { paddingTopLeft: [70, 60], paddingBottomRight: [290, 30], maxZoom: 18 });
    else if (pts.length === 1) map.setView(pts[0], 17);
  }
  function bindRange(id, key) {
    const el = $(id);
    const show = () => { el.style.setProperty('--p', ((el.value - el.min) / (el.max - el.min) * 100) + '%'); setText($(id + '-v'), String(el.value)); };
    el.value = plan[key];
    show();
    el.addEventListener('input', () => { plan[key] = +el.value; show(); updatePlanInfo(); savePlan(); });
  }
  bindRange('#p-alt', 'alt');
  function bindStepper(id, key, min, max) {
    const box = $(id), val = $('b', box);
    const show = () => setText(val, String(plan[key]));
    $$('button', box).forEach(b => {
      b.onclick = () => { plan[key] = Math.max(min, Math.min(max, plan[key] + +b.dataset.d)); show(); updatePlanInfo(); savePlan(); haptic(); };
    });
    show();
  }
  bindStepper('#p-speed', 'speed', 1, 10);
  bindStepper('#p-hover', 'hover', 0, 30);
  $('#pt-undo').onclick = () => { if (plan.sel >= 0) plan.pts.splice(plan.sel, 1); else plan.pts.pop(); plan.sel = -1; drawPlan(); savePlan(); };
  $('#pt-clear').onclick = () => {
    if (!plan.pts.length) return;
    plan.pts = []; plan.sel = -1; drawPlan(); savePlan();
    try { api.clearMission(); } catch (e) { /* demo */ }
    toast('Waypoints cleared');
  };
  function missionReason(d) {
    if (d.armed !== 2) return 'Take off first: missions are flown from the air.';
    if (d.indoor === 1) return 'Missions need GPS mode: hold the GPS button on the remote for 3 s.';
    if (!d.gpsFine) return 'Waiting for a GPS fix.';
    return '';
  }
  function sendMission(points, alt, speed, hover) {
    let r = '';
    try { r = api.mission(JSON.stringify({ points, alt, speed, hover })) || ''; } catch (e) { r = String(e); }
    if (r) toast(r, 'bad'); else toast('Uploading waypoints…', 'ok');
  }
  $('#p-fly').onclick = () => {
    if (!plan.pts.length) { toast('Tap the map to add waypoints first.', 'bad'); return; }
    if (!ready(true)) return;
    const why = missionReason(S.d);
    if (why) { toast(why, 'bad'); return; }
    const n = plan.pts.length, total = legs().reduce((a, b) => a + b, 0);
    confirmAction({ title: `Fly ${n} waypoint${n > 1 ? 's' : ''}?`, icon: 'i-flag', kind: 'warn', label: 'Slide to start the mission',
      text: `At ${plan.alt} m and ${plan.speed} m/s, ${fmtDist(total)} in total. Tap Stop at any time to end it and fly yourself.`,
      action: () => sendMission(plan.pts, plan.alt, plan.speed, plan.hover) });
  };
  function renderMission(s) {
    const m = s.mission;
    const show = m.state !== 'idle' || !!m.msg;
    const cls = 'mstatus' + (show ? ' show' : '') + (m.state === 'failed' || m.state === 'cancelled' ? ' bad' : (m.state === 'running' || /finished/i.test(m.msg) ? ' ok' : ''));
    const msg = m.state === 'uploading' ? `Uploading waypoints ${m.acked}/${m.total}…` : m.msg;
    $('#p-status').className = cls;
    setText($('#p-msg'), msg);
    $('#p-bar').style.display = m.state === 'uploading' ? '' : 'none';
    $('#p-bar i').style.width = (m.total ? m.acked / m.total * 100 : 0) + '%';
    const fm = $('#flymission');
    fm.className = cls;
    fm.id = 'flymission';
    setText($('#fm-msg'), msg);
  }

  // ---------------- settings ----------------
  function openSettings() {
    let c = {};
    try { c = JSON.parse(api.settings()); } catch (e) { /* demo */ }
    $$('#settings .toggle').forEach(t => setClass(t, 'on', c[t.dataset.key]));
    setText($('#set-addr'), `${c.addresses || '-'}  ·  TCP port ${c.port || 9000}`);
    setText($('#set-version'), c.version ? 'v' + c.version : '');
    lastLogV = -1;
    $('#settings').classList.add('show');
    $('#scrim').classList.add('show');
  }
  function closeSettings() { $('#settings').classList.remove('show'); $('#scrim').classList.remove('show'); }
  $('#btn-settings').onclick = openSettings;
  $('#set-close').onclick = closeSettings;
  $('#scrim').onclick = closeSettings;
  $$('#settings .toggle').forEach(t => {
    t.onclick = () => {
      const key = t.dataset.key, on = !t.classList.contains('on');
      if (key === 'pcControl' && on) {
        confirmAction({ title: 'Give flight control to the PC?', icon: 'i-warn', kind: 'bad', label: 'Slide to allow PC control',
          text: 'The computer on the link (port 9000) sends the flight commands and this app stops sending its own. For testing with the propellers off, or for the UGV link.',
          action: () => { setClass(t, 'on', true); api.set('pcControl', '1'); } });
        return;
      }
      setClass(t, 'on', on);
      if (key === 'sticks') setSticksShown(on, true); else api.set(key, on ? '1' : '0');
    };
  });
  function bindSeg(id, key, cast, apply) {
    const btns = $$(id + ' button');
    const mark = () => btns.forEach(b => setClass(b, 'on', cast(b.dataset.v) === cfg[key]));
    btns.forEach(b => { b.onclick = () => { cfg[key] = cast(b.dataset.v); apply(); mark(); }; });
    mark();
  }
  bindSeg('#set-mode', 'mode', Number, () => { store.set('stickMode', cfg.mode); labelSticks(); });
  bindSeg('#set-sens', 'sens', Number, () => store.set('stickSens', cfg.sens));
  $('#set-beep').onclick = () => run('beep', 'Beeping');
  function meter(id, v) {
    const el = $(id);
    const f = isNum(v) ? (v - 127) / 127 : 0;
    el.style.left = (f < 0 ? 50 + f * 50 : 50) + '%';
    el.style.width = Math.abs(f) * 50 + '%';
  }

  // ---------------- connect card ----------------
  $('#cn-connect').onclick = () => {
    if (!S || !S.link.present) { toast('Plug the remote into this phone first.', 'bad'); return; }
    if (!S.link.freshAttach) { toast('Unplug the remote and plug it in again, then tap Connect.', 'bad'); return; }
    api.connect();
  };
  $('#cn-skip').onclick = () => { connectDismissed = true; setView('plan'); };
  function renderConnect(s) {
    const l = s.link;
    if (wasConnected && !l.connected) connectDismissed = false;
    wasConnected = !!l.connected;
    const show = !l.connected && !connectDismissed;
    setClass($('#connect'), 'show', show);
    if (!show) return;
    setClass($('#cs-1'), 'done', l.present);
    setClass($('#cs-2'), 'done', l.present);
    setClass($('#cs-2'), 'now', !l.present);
    setClass($('#cs-3'), 'now', l.present && l.freshAttach);
    setClass($('#cn-note'), 'show', l.present && !l.freshAttach);
    setClass($('#cn-connect'), 'off', !(l.present && l.freshAttach));
  }

  // ---------------- warnings ----------------
  let lastWarnKey = '';
  function warnings(s) {
    const w = [], d = s.d;
    if (!s.link.connected) return w;
    if (s.link.pcControl) w.push(['PC control is on', 'warn']);
    if (!d.fresh) { w.push(['No signal from the drone', 'bad']); return w; }
    if (d.lowBat >= 2) w.push(['Battery critical: land now', 'bad']); else if (d.lowBat === 1) w.push(['Low battery', 'warn']);
    if (d.emergRth > 0 && d.emergRth < 255) w.push([`Emergency return home in ${d.emergRth} s`, 'bad']);
    if (d.lowRth > 0 && d.lowRth < 255) w.push([`Low battery: returning home in ${d.lowRth} s`, 'bad']);
    if (d.oaWarn) w.push(['Obstacle ahead', 'bad']);
    if (d.tempOver) w.push(['Overheating', 'bad']);
    if (d.currOver) w.push(['Motor overload', 'bad']);
    if (d.gale) w.push(['Strong wind', 'warn']);
    if (d.magCal || d.magErr) w.push(['Compass needs calibration', 'warn']);
    if (d.hLimit) w.push(['Height limit reached', 'warn']);
    if (d.dLimit) w.push(['Distance limit reached', 'warn']);
    if (d.forbid) w.push(['Take-off not allowed here', 'warn']);
    if (d.indoor === 0 && !d.gpsFine && d.armed !== 2) w.push([`Waiting for GPS (${d.gpsNum} satellites)`, 'warn']);
    if (d.remotePct >= 0 && d.remotePct < 20) w.push([`Remote battery ${d.remotePct}%`, 'warn']);
    return w;
  }
  function renderWarnings(s) {
    const w = warnings(s), key = w.map(x => x.join(':')).join('|');
    if (key === lastWarnKey) return;
    lastWarnKey = key;
    $('#warnings').innerHTML = w.map(([t, k]) => `<div class="chip ${k === 'bad' ? 'bad' : ''}"><svg class="i"><use href="#i-warn"/></svg>${t}</div>`).join('');
  }

  // ---------------- main render ----------------
  let lastFly = '';
  function render(s) {
    const d = s.d, l = s.link, live = l.connected && d.fresh;
    if (s.insets) {
      const st = document.documentElement.style;
      st.setProperty('--il', s.insets.l + 'px'); st.setProperty('--ir', s.insets.r + 'px');
      st.setProperty('--it', s.insets.t + 'px'); st.setProperty('--ib', s.insets.b + 'px');
    }
    let txt, cls;
    if (!l.connected) { txt = l.present ? 'Remote plugged in' : 'Remote not connected'; cls = l.present ? 'warn' : 'bad'; }
    else if (!d.fresh) { txt = s.handshake === 'relay' || s.handshake === 'version' ? 'Connecting to drone…' : 'No drone signal'; cls = 'bad'; }
    else {
      txt = FLY_MODES[d.flyMode] || (d.armed === 2 ? 'Flying' : d.armed === 1 ? 'Motors on' : 'Ready');
      const m = d.indoor === 1 ? 'ATTI' : d.indoor === 0 ? 'GPS' : '';
      if (m) txt += ' · ' + m;
      cls = 'ok';
    }
    setText($('#state-text'), txt);
    $('#state-dot').className = 'dot ' + cls;

    setText($('#v-gps'), live ? String(d.gpsNum) : '--');
    $('#st-gps').className = 'stat ' + (!live ? '' : d.gpsFine ? 'ok' : d.gpsNum >= 6 ? 'warn' : 'bad');
    const pct = live && d.batPct >= 0 ? d.batPct : null;
    setText($('#v-bat'), pct == null ? '--%' : pct + '%');
    setText($('#v-batv'), live && d.batV > 0 ? d.batV.toFixed(1) + 'V' : '--.-V');
    $('#bat-fill').style.width = (pct == null ? 0 : Math.max(4, Math.min(100, pct))) + '%';
    $('#st-bat').className = 'stat ' + (pct == null ? '' : pct >= 50 ? 'ok' : pct >= 25 ? 'warn' : 'bad');
    const rc = l.connected && d.remotePct >= 0 ? d.remotePct : null;
    setText($('#v-rc'), rc == null ? '--%' : rc + '%');
    $('#st-rc').className = 'stat ' + (rc == null ? '' : rc >= 43 ? 'ok' : rc >= 26 ? 'warn' : 'bad');
    setText($('#v-time'), mmss(d.flightS));

    setText($('#t-alt'), live ? f1(d.alt) : '--');
    setText($('#t-dist'), live ? f0(d.dist) : '--');
    setText($('#t-hs'), live ? f1(d.hs) : '--');
    setText($('#t-vs'), live ? (d.vs > 0.05 ? '+' : '') + f1(d.vs) : '--');
    const hdg = ((d.yaw % 360) + 360) % 360;
    $('#tape-strip').style.transform = `translateX(${-(live ? hdg : 0) * PX_DEG}px)`;
    setText($('#tape-box'), live ? String(Math.round(hdg) % 360).padStart(3, '0') + '° ' + CARD[Math.round(hdg / 45) % 8] : '---');
    $('#horizon').style.transform = live ? `rotate(${-d.roll}deg) translateY(${Math.max(-60, Math.min(60, d.pitch * 3.2))}px)` : '';

    const flyKey = live && d.armed === 2 ? 'land' : 'takeoff';
    if (flyKey !== lastFly) {
      lastFly = flyKey;
      const b = $('#btn-fly');
      b.className = 'act ' + (flyKey === 'land' ? 'land' : 'primary');
      b.innerHTML = `<div class="c">${icon(flyKey === 'land' ? 'i-land' : 'i-takeoff')}</div><span>${flyKey === 'land' ? 'Land' : 'Take off'}</span>`;
    }
    setClass($('#btn-fly'), 'off', !live);
    setClass($('#btn-rth'), 'off', !live || !!rthReason(d));
    setClass($('#btn-stop'), 'off', !l.connected);

    setClass($('#btn-record'), 'on', s.cam.rec);
    setText($('#rec-time'), s.cam.rec ? mmss(s.cam.recS) : '');
    const vid = s.video || {};
    $('#novideo').style.display = vid.active ? 'none' : '';
    setText($('#nv-text'), !l.connected ? 'No video' : !l.videoOn ? 'Video off (Settings)' : 'Waiting for video…');
    setText($('#set-vinfo'), vid.active ? `${vid.codec} · ${vid.w}×${vid.h} · ${vid.fps} fps` : 'From the drone camera, H.264 or H.265.');
    setText($('#set-fc'), (d.fc || 'not answered yet') + (s.handshake ? `  ·  handshake: ${s.handshake}` : ''));
    meter('#rx-roll', d.stRoll); meter('#rx-pitch', d.stPitch); meter('#rx-thr', d.stThr); meter('#rx-yaw', d.stYaw);
    setText($('#g-pos'), live && d.gimbalPos >= 0 ? `TILT ${d.gimbalPos}` : 'GIMBAL');

    renderWarnings(s);
    renderConnect(s);
    renderMission(s);
    renderModes(s);
    renderMedia(s);
    if (map) renderMap(s);
    if ($('#settings').classList.contains('show') && s.logV !== lastLogV) {
      lastLogV = s.logV;
      const box = $('#log');
      box.textContent = api.log();
      box.scrollTop = box.scrollHeight;
    }
  }

  function poll() {
    let s;
    try { s = JSON.parse(api.state()); } catch (e) { console.error('state: ' + e); return; }
    S = s;
    try { render(s); } catch (e) { console.error('render: ' + (e && e.stack || e)); }
  }

  window.uiToast = toast;  // used by the Raspberry Pi app's Qt bridge to show messages
  window.onBack = function () {
    if ($('#confirm').classList.contains('show')) { closeConfirm(); return true; }
    if ($('#modesheet').classList.contains('show')) { closeModes(); return true; }
    if ($('#search').classList.contains('show')) { closeSearch(); return true; }
    if ($('#settings').classList.contains('show')) { closeSettings(); return true; }
    if ($('#mapmenu').classList.contains('show')) { hideMenu(); return true; }
    if (view === 'plan') { setView('fly'); return true; }
    if (main === 'map') { setMain('video'); return true; }
    return false;
  };

  // ---------------- start ----------------
  try {
    const c = JSON.parse(api.settings());
    setSticksShown(DEMO ? query.get('sticks') !== '0' : !!c.sticks, false);
  } catch (e) { setSticksShown(true, false); }
  labelSticks();
  try { initMap(); } catch (e) { console.error('map: ' + e); }
  poll();
  setInterval(poll, 100);
  setTimeout(layout, 0);
  setInterval(layout, 1000);

  if (DEMO) {
    if (query.get('plan') === 'demo') {
      plan.pts = [[-6.17415, 106.82595], [-6.17345, 106.82790], [-6.17440, 106.82965], [-6.17610, 106.82925], [-6.17675, 106.82730]];
      plan.sel = query.get('sel') ? +query.get('sel') : -1;
    }
    if (query.get('main') === 'map') setMain('map');
    if (query.get('view') === 'plan') setView('plan'); else drawPlan();
    if (query.get('panel') === 'settings') setTimeout(openSettings, 50);
    if (query.get('modes')) setTimeout(openModes, 300);
    if (query.get('search')) setTimeout(() => { openSearch(); $('#s-input').value = query.get('search'); doSearch(); }, 400);
    const c = query.get('confirm');
    const acts = { motors: actMotors, fly: actFly, rth: actRth, mission: () => $('#p-fly').onclick() };
    if (acts[c]) setTimeout(acts[c], 450);
    if (query.get('menu')) setTimeout(() => onMapClick({ latlng: L.latLng(-6.17330, 106.82850) }), 600);
    if (query.get('touch')) setTimeout(() => {
      $('#stick-l').classList.add('active'); $('.knob', $('#stick-l')).style.transform = 'translate(-14px, -30px)';
      $('#stick-r').classList.add('active'); $('.knob', $('#stick-r')).style.transform = 'translate(22px, -18px)';
    }, 300);
  } else {
    drawPlan();
  }

  // ---------------- demo (desktop preview) ----------------
  function demoApi() {
    const t0 = Date.now(), base = [-6.1754, 106.8272];
    const link = query.get('demo') || '';
    const warn = query.get('warn') === '1';
    const indoor = query.get('indoor') === '1' ? 1 : 0;
    let armed = query.has('armed') ? +query.get('armed') : 2;
    let rec = query.get('rec') === '1', recT = Date.now() - 83000, logV = 1, up = 0;
    let logText = '08:12:01  Ready. Plug in the remote, then tap Connect.\n08:12:09  Remote plugged in. Tap Connect.\n' +
      '08:12:11  Permission granted.\n08:12:11  Connected to vison android.usbaoa. Unplug the remote to disconnect.\n' +
      '08:12:12  Relay answered the startup handshake.\n08:12:12  Flight controller: HF-SH-F22N7R406.024.0509\n' +
      '08:12:13  Video: H.265 1280x720 (c2.mtk.hevc.decoder).\n08:13:40  Motors started.\n08:13:44  Take off: sent.\n';
    let mission = { state: 'idle', msg: '', total: 0, acked: 0 };
    let savedAt = 0, savedMime = 'image/jpeg';
    if (query.get('saved')) setTimeout(() => { savedAt = Date.now(); }, 600);
    const ms = query.get('mission');
    if (ms === 'uploading') mission = { state: 'uploading', msg: '', total: 5, acked: 2 };
    if (ms === 'running') mission = { state: 'running', msg: 'All waypoints confirmed. Mission running.', total: 5, acked: 5 };
    if (ms === 'failed') mission = { state: 'failed', msg: 'The drone did not confirm waypoint 3.', total: 5, acked: 2 };
    return {
      state() {
        const t = (Date.now() - t0) / 1000, a = t / 24, flying = armed === 2;
        if (mission.state === 'uploading' && ms !== 'uploading') {
          up += 0.1;
          mission.acked = Math.min(mission.total, Math.floor(up * 4));
          if (mission.acked >= mission.total) { mission.state = 'running'; mission.msg = 'All waypoints confirmed. Mission running.'; }
        }
        return JSON.stringify({
          link: { connected: link ? 0 : 1, present: link === 'connect' ? 0 : 1, freshAttach: link === 'plugged' ? 1 : 0, rx: 0, pc: '', pcControl: 0, videoOn: 1 },
          d: {
            fresh: 1, age: 40, roll: flying ? 6 * Math.sin(t / 1.7) : 0.4, pitch: flying ? 4 * Math.cos(t / 2.3) : -0.6, yaw: (a * 57.3 + 64) % 360, armed,
            flyMode: mission.state === 'running' ? 4 : 0, indoor, gpsNum: indoor ? 0 : 14, gpsFine: indoor ? 0 : 1, hasFix: indoor ? 0 : 1,
            gimbalPos: 27,
            lat: base[0] + (flying ? 0.0011 * Math.sin(a) : -0.0004), lon: base[1] + (flying ? 0.0016 * Math.cos(a) : -0.0009),
            alt: flying ? 42.3 + Math.sin(t / 5) : 0, dist: flying ? 128 + 30 * Math.sin(a) : 0,
            hs: flying ? 5.2 + 0.4 * Math.sin(t) : 0, vs: flying ? 0.4 * Math.sin(t / 3) : 0,
            batV: warn ? 10.6 : 11.6, batPct: warn ? 21 : 76, lowBat: warn ? 1 : 0, remotePct: warn ? 15 : 64,
            magErr: 0, magCal: warn ? 1 : 0, oaWarn: 0, hLimit: 0, dLimit: 0, forbid: 0, gale: warn ? 1 : 0, tempOver: 0, currOver: 0,
            emergRth: 255, lowRth: 255, flightS: armed ? 312 + t : 0, fc: 'HF-SH-F22N7R406.024.0509',
            stRoll: 150, stPitch: 170, stThr: 127, stYaw: 110
          },
          home: indoor ? null : { lat: base[0] - 0.0004, lon: base[1] - 0.0009 },
          mission, cam: { rec: rec ? 1 : 0, recS: rec ? (Date.now() - recT) / 1000 : 0 }, handshake: 'ok',
          media: { uri: savedAt ? 'demo' : '', mime: savedMime, at: savedAt, rec: rec ? 1 : 0 },
          video: { codec: 'H.265', w: 1280, h: 720, fps: 30, active: query.get('novideo') ? 0 : 1, dropped: 0 },
          phone: { lat: base[0] - 0.0006, lon: base[1] - 0.0013, acc: 4 }, insets: { l: 0, t: 0, r: 0, b: 0 }, logV
        });
      },
      snapshot() { setTimeout(() => { savedMime = 'image/jpeg'; savedAt = Date.now(); }, 300); return ''; },
      openLast() { return ''; },
      command(n) {
        if (n === 'record') { if (rec) { savedMime = 'video/mp4'; savedAt = Date.now(); } rec = !rec; recT = Date.now(); }
        if (n === 'land') armed = 1;
        if (n === 'takeoff') armed = 2;
        if (n === 'cancel' && mission.state !== 'idle') mission = { state: 'cancelled', msg: 'Mission cancelled.', total: 0, acked: 0 };
        logText += 'Demo command: ' + n + '\n';
        logV++;
        return '';
      },
      mission(j) { mission = { state: 'uploading', msg: '', total: JSON.parse(j).points.length, acked: 0 }; up = 0; return ''; },
      sticks() {},
      clearMission() { if (mission.state !== 'uploading') mission = { state: 'idle', msg: '', total: 0, acked: 0 }; },
      connect() {},
      log() { return logText; },
      settings() { return JSON.stringify({ video: 1, fill: 0, pcControl: 0, pcVideo: 0, sticks: 1, stickPriority: 0, port: 9000, addresses: '192.168.1.23 (Wi-Fi)', version: '1.0 demo' }); },
      set() {},
      videoRect() {},
      haptic() {}
    };
  }
})();
