// Yún Panel: talks to the board through ubus over HTTP (/ubus), using the
// "yun" rpcd object (/usr/share/rpcd/ucode/yun.uc) and the standard
// session and system objects.
'use strict';

(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // --- ubus JSON-RPC -------------------------------------------------------

  const NULL_SESSION = '00000000000000000000000000000000';
  const api = {
    session: sessionStorage.getItem('yun-session') || NULL_SESSION,
    id: 0,

    async call(object, method, params = {}) {
      if (window.YunMock) return window.YunMock.call(object, method, params);
      const res = await fetch('/ubus', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0', id: ++this.id, method: 'call',
          params: [this.session, object, method, params],
        }),
      });
      const msg = await res.json();
      if (msg.error) {
        const err = new Error(msg.error.message || 'ubus error');
        err.code = msg.error.code;
        throw err;
      }
      const [status, data] = msg.result;
      if (status === 6) {           // UBUS_STATUS_PERMISSION_DENIED
        const err = new Error('Signed out');
        err.code = 'auth';
        throw err;
      }
      if (status !== 0) throw new Error(`${object}.${method} failed (${status})`);
      if (data && data.error) throw new Error(data.error);
      return data || {};
    },

    async login(password) {
      const r = await this.call('session', 'login', { username: 'root', password, timeout: 3600 });
      this.session = r.ubus_rpc_session;
      sessionStorage.setItem('yun-session', this.session);
    },

    logout() {
      this.call('session', 'destroy').catch(() => {});
      this.session = NULL_SESSION;
      sessionStorage.removeItem('yun-session');
    },

    // Write a file to the board through cgi-io.
    upload(path, file, onProgress) {
      if (window.YunMock) return window.YunMock.upload(path, file, onProgress);
      return new Promise((resolve, reject) => {
        const form = new FormData();
        form.append('sessionid', this.session);
        form.append('filename', path);
        form.append('filemode', '0600');
        form.append('filedata', file);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/cgi-bin/cgi-upload');
        xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
        xhr.onload = () => (xhr.status === 200 ? resolve() : reject(new Error(`Upload failed (${xhr.status})`)));
        xhr.onerror = () => reject(new Error('Upload failed'));
        xhr.send(form);
      });
    },
  };

  // --- Helpers -------------------------------------------------------------

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c != null) e.append(c);
    return e;
  }

  function toast(text, bad = false) {
    const t = el('div', { class: 'toast' + (bad ? ' bad' : '') }, text);
    $('#toasts').append(t);
    setTimeout(() => t.remove(), 4500);
  }

  function bytes(n) {
    if (n == null) return '–';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
  }

  function duration(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24, m = Math.floor(s / 60) % 60;
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function setKV(dl, pairs) {
    dl.replaceChildren(...pairs.filter(([, v]) => v != null && v !== '')
      .flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, String(v))]));
  }

  function setBadge(node, text, kind) {
    node.hidden = false;
    node.textContent = text;
    node.className = node.className.replace(/\bis-\w+/g, '').trim() + (kind ? ` is-${kind}` : '');
  }

  function bars(quality) {
    // quality 0..100 -> 0..4 bars
    const n = quality == null ? 0 : Math.max(1, Math.min(4, Math.ceil(quality / 25)));
    return el('div', { class: 'signal', 'aria-label': `Signal ${quality ?? 0}%` },
      ...[1, 2, 3, 4].map((i) => el('i', { class: i <= n ? 'on' : '' })));
  }

  const ENCRYPTION = {
    none: 'Open', psk: 'WPA', psk2: 'WPA2', 'psk-mixed': 'WPA/WPA2',
    sae: 'WPA3', 'sae-mixed': 'WPA2/WPA3', owe: 'OWE',
  };

  // --- State ---------------------------------------------------------------

  let status = null;
  const memHistory = [];
  let pollTimer = null;
  let clockTimer = null;
  let clockOffset = null;   // board time minus this computer's time, in ms
  let pollSentAt = 0;       // when the last status request was sent
  let dataTimer = null;
  let lastData = {};

  // --- Views ---------------------------------------------------------------

  function showView(name) {
    if (!$(`.view[data-view="${name}"]`)) name = 'overview';
    $$('.view').forEach((v) => (v.hidden = v.dataset.view !== name));
    $$('.nav-link').forEach((a) => a.classList.toggle('active', a.dataset.view === name));
    clearInterval(dataTimer);
    if (name === 'bridge') {
      refreshData();
      dataTimer = setInterval(refreshData, 2000);
    }
    if (name === 'network' && !$('#scan-list').children.length) scan();
  }

  function renderStatus(s) {
    status = s;
    const host = s.hostname || 'Arduino';
    document.title = `${host} · Yún Panel`;
    $('#board-name').textContent = host;
    syncClock(s.time, pollSentAt);

    const pill = $('#conn-pill');
    pill.className = 'pill is-ok';
    pill.lastElementChild.textContent = 'Online';

    $('#ov-uptime').textContent = duration(s.uptime || 0);
    $('#ov-firmware').textContent = s.firmware?.version || '–';
    $('#ov-firmware').title = s.firmware?.description || '';
    $('#ov-bridge').textContent = s.bridge?.running ? 'Running' : 'Not running';
    $('#mcu-bridge').textContent = s.bridge?.running ? 'Running' : 'Not running';
    const load = s.load || [];
    $('#ov-load').textContent = load.length ? load[0].toFixed(2) : '–';
    $('#ov-load-more').textContent = load.length > 2 ? `5 min ${load[1].toFixed(2)} · 15 min ${load[2].toFixed(2)}` : '';

    const chips = [];
    if (s.wifi?.ipv4) chips.push(['Wi-Fi', s.wifi.ipv4]);
    if (s.ethernet?.ipv4) chips.push(['Ethernet', s.ethernet.ipv4]);
    chips.push(['mDNS', `${host}.local`]);
    $('#ov-addresses').replaceChildren(...chips.map(([k, v]) => el('span', { class: 'chip' }, k, ' ', el('b', {}, v))));

    // Wi-Fi
    const w = s.wifi || {};
    const modeText = { client: 'Client', ap: 'Setup mode', fallback: 'Setup mode (fallback)' }[w.mode] || '–';
    setBadge($('#wifi-badge'), w.mode === 'client' ? (w.connected ? 'Connected' : 'Connecting') : modeText,
      w.mode === 'client' ? (w.connected ? 'ok' : 'warn') : 'warn');
    $('#wifi-ssid').textContent = w.ssid || '–';
    $('#wifi-detail').textContent = w.mode === 'client'
      ? [w.signal != null && `${w.signal} dBm`, w.channel && `channel ${w.channel}`, ENCRYPTION[w.encryption]].filter(Boolean).join(' · ')
      : `Open network at 192.168.240.1${w.clients != null ? ` · ${w.clients} connected` : ''}`;
    $('#wifi-signal').replaceWith(Object.assign(bars(w.mode === 'client' ? w.quality : 100), { id: 'wifi-signal' }));
    setKV($('#wifi-kv'), [
      ['Mode', modeText], ['Address', w.ipv4], ['MAC address', w.mac],
      ['Received', w.rx_bytes != null && bytes(w.rx_bytes)], ['Sent', w.tx_bytes != null && bytes(w.tx_bytes)],
    ]);
    $('#ap-ssid').textContent = w.ap_ssid || 'Arduino Yun';

    // Ethernet
    const e = s.ethernet || {};
    setBadge($('#eth-badge'), e.carrier ? (e.ipv4 ? 'Connected' : 'Cable in') : 'No cable', e.carrier ? (e.ipv4 ? 'ok' : 'warn') : '');
    const ethPairs = [
      ['Address', e.ipv4], ['Gateway', e.gateway], ['MAC address', e.mac],
      ['Received', e.rx_bytes != null && bytes(e.rx_bytes)], ['Sent', e.tx_bytes != null && bytes(e.tx_bytes)],
    ];
    setKV($('#eth-kv'), ethPairs);
    setKV($('#eth-kv-2'), ethPairs);

    // Memory
    const m = s.memory || {};
    if (m.total) {
      const used = m.total - (m.available ?? m.free);
      const pct = Math.round((used / m.total) * 100);
      $('#mem-text').textContent = `${bytes(used)} of ${bytes(m.total)}`;
      $('#mem-bar').style.width = pct + '%';
      $('#mem-bar').parentElement.className = 'meter' + (pct > 90 ? ' bad' : pct > 75 ? ' warn' : '');
      memHistory.push(pct);
      if (memHistory.length > 60) memHistory.shift();
      drawSpark($('#mem-spark'), memHistory);
    }

    // Storage
    $('#storage-list').replaceChildren(...(s.storage || []).map((d) => {
      const pct = d.total ? Math.round((d.used / d.total) * 100) : 0;
      return el('div', { class: 'storage-item' },
        el('div', { class: 'row' }, el('span', {}, d.name), el('span', { class: 'muted' }, `${bytes(d.used)} / ${bytes(d.total)}`)),
        el('div', { class: 'meter' + (pct > 90 ? ' bad' : pct > 75 ? ' warn' : '') }, el('span', { style: `width:${pct}%` })));
    }));

    // REST endpoints
    const base = `http://${location.host}`;
    $('#rest-list').replaceChildren(...['/arduino/digital/13/1', '/data/get', '/mailbox/hello']
      .map((p) => el('li', {}, el('code', {}, base + p))));

    // Settings (don't overwrite while editing)
    if (!$('#board-form').contains(document.activeElement)) {
      $('#set-hostname').value = host;
      $('#set-rest').checked = !!s.rest_secure;
      if (s.zonename) $('#set-zone').value = s.zonename;
    }
    setKV($('#fw-kv'), [
      ['Version', s.firmware?.version], ['OpenWrt', s.firmware?.openwrt], ['Kernel', s.firmware?.kernel],
    ]);
  }

  // The header clock. Status arrives every 3 s with the board's time in whole
  // seconds, so one reading only pins the board's clock to within a second.
  // Each reading taken when the reply arrives is a lower bound on the offset
  // (the board's clock can only have moved on since), and one taken when the
  // request was sent, plus a second, is an upper bound. Keep the highest lower
  // bound, and start again only if a reading falls outside those bounds (the
  // board's clock was changed). Then tick on the board's second boundaries.
  function syncClock(time, sentAt) {
    if (!time) { clockOffset = null; return; }
    const lower = time * 1000 - Date.now();
    const upper = time * 1000 + 1000 - sentAt;
    if (clockOffset == null || clockOffset > upper || clockOffset < lower - 2000) clockOffset = lower;
    else clockOffset = Math.max(clockOffset, lower);
    tickClock();
  }

  function renderClock() {
    const time = clockOffset == null ? null : new Date(Date.now() + clockOffset).toLocaleString();
    $('#board-sub').textContent = [status?.model, time].filter(Boolean).join(' · ');
  }

  function tickClock() {
    clearTimeout(clockTimer);
    renderClock();
    if (clockOffset == null) return;
    const ms = (Date.now() + clockOffset) % 1000;
    clockTimer = setTimeout(tickClock, 1000 - ms + 15);
  }

  function drawSpark(canvas, values) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || values.length < 2) return;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    const step = w / (values.length - 1);
    const y = (v) => h - 2 - (v / 100) * (h - 4);
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = w - (values.length - 1 - i) * step;
      i ? ctx.lineTo(x, y(v)) : ctx.moveTo(x, y(v));
    });
    ctx.strokeStyle = accent; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(w - (values.length - 1) * step, h); ctx.closePath();
    ctx.globalAlpha = 0.12; ctx.fillStyle = accent; ctx.fill();
  }

  async function poll() {
    try {
      pollSentAt = Date.now();
      renderStatus(await api.call('yun', 'status'));
    } catch (err) {
      if (err.code === 'auth') return signedOut();
      const pill = $('#conn-pill');
      pill.className = 'pill is-bad';
      pill.lastElementChild.textContent = 'Unreachable';
    }
  }

  // --- Wi-Fi ---------------------------------------------------------------

  async function scan() {
    const btn = $('#scan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning…';
    try {
      const { results = [] } = await api.call('yun', 'wifi_scan');
      const seen = new Map();
      for (const r of results) {
        if (!r.ssid) continue;
        const prev = seen.get(r.ssid);
        if (!prev || (r.quality ?? 0) > (prev.quality ?? 0)) seen.set(r.ssid, r);
      }
      const list = [...seen.values()].sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0));
      $('#scan-list').replaceChildren(...list.map((r) => el('li', {},
        el('button', { type: 'button', onclick: () => openJoin(r) },
          bars(r.quality),
          el('span', { class: 'name' }, r.ssid),
          status?.wifi?.mode === 'client' && status.wifi.ssid === r.ssid ? el('span', { class: 'current' }, 'Connected') : null,
          el('span', { class: 'muted small' }, ENCRYPTION[r.encryption] || r.encryption || '')))));
      if (!list.length) $('#scan-list').replaceChildren(el('li', { class: 'muted' }, 'No networks found.'));
    } catch (err) {
      toast(`Scan failed: ${err.message}`, true);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Scan';
    }
  }

  function openJoin(r) {
    $('#join-form').hidden = false;
    $('#join-title').textContent = `Join ${r.ssid}`;
    $('#join-ssid').value = r.ssid;
    const enc = r.encryption === 'none' ? 'none' : (r.encryption?.startsWith('sae') ? 'sae-mixed' : 'psk2');
    $('#join-enc').value = enc;
    updateKeyField();
    (enc === 'none' ? $('#join-ssid') : $('#join-key')).focus();
  }

  function updateKeyField() {
    const open = $('#join-enc').value === 'none';
    $('#join-key-field').hidden = open;
    $('#join-key').required = !open;
  }

  async function join(ev) {
    ev.preventDefault();
    const ssid = $('#join-ssid').value.trim();
    const encryption = $('#join-enc').value;
    const key = $('#join-key').value;
    if (!confirm(`Join "${ssid}"? The Yún will leave its current network. Find it again as ${status?.hostname || 'Arduino'}.local once it has joined.`)) return;
    try {
      await api.call('yun', 'wifi_client', { ssid, encryption, key });
      toast(`Joining ${ssid}…`);
      $('#join-form').hidden = true;
    } catch (err) {
      toast(`Couldn't change Wi-Fi: ${err.message}`, true);
    }
  }

  // --- Sketch upload -------------------------------------------------------

  async function flash(file) {
    if (!file) return;
    if (!/\.hex$/i.test(file.name)) return toast('Choose a .hex file (Sketch → Export Compiled Binary in the IDE).', true);
    location.hash = '#sketch';
    const prog = $('#flash-progress');
    const step = $('#flash-step'), pct = $('#flash-pct'), bar = $('#flash-bar');
    prog.hidden = false;
    $('#flash-log-wrap').hidden = true;
    bar.parentElement.className = 'meter';
    const set = (label, p) => {
      step.textContent = label;
      pct.textContent = p == null ? '' : `${Math.round(p * 100)}%`;
      bar.style.width = `${Math.round((p ?? 0) * 100)}%`;
    };
    try {
      set(`Uploading ${file.name}`, 0);
      await api.upload('/tmp/sketch.hex', file, (p) => set(`Uploading ${file.name}`, p * 0.3));
      set('Writing to the microcontroller…', 0.35);
      const tick = setInterval(() => {
        const w = parseFloat(bar.style.width) / 100;
        if (w < 0.95) set('Writing to the microcontroller…', w + 0.02);
      }, 400);
      let r;
      try {
        r = await api.call('yun', 'sketch_flash', {});
      } finally {
        clearInterval(tick);
      }
      $('#flash-log').textContent = r.output || '';
      $('#flash-log-wrap').hidden = !r.output;
      if (r.code === 0) {
        set('Done: the sketch is running', 1);
        toast('Sketch uploaded');
      } else {
        bar.parentElement.className = 'meter bad';
        set(`avrdude failed (exit code ${r.code})`, 1);
        $('#flash-log-wrap').open = true;
      }
    } catch (err) {
      bar.parentElement.className = 'meter bad';
      set(err.message, 1);
    }
  }

  function setupDropzones() {
    for (const zone of $$('[data-drop]')) {
      const input = $('input', zone);
      input.addEventListener('change', () => { flash(input.files[0]); input.value = ''; });
      zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
      zone.addEventListener('dragleave', () => zone.classList.remove('over'));
      zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('over');
        flash(e.dataTransfer.files[0]);
      });
    }
  }

  // --- Bridge data ---------------------------------------------------------

  async function refreshData() {
    try {
      const { values = {} } = await api.call('yun', 'bridge_data');
      const keys = Object.keys(values).sort();
      const body = $('#ds-body');
      if (!keys.length) {
        body.replaceChildren(el('tr', {}, el('td', { colspan: 3, class: 'muted' }, 'No values yet. Call Bridge.put() in your sketch.')));
      } else {
        body.replaceChildren(...keys.map((k) => el('tr', { class: lastData[k] !== undefined && lastData[k] !== values[k] ? 'flash' : '' },
          el('td', {}, k), el('td', {}, String(values[k])),
          el('td', {}, el('button', {
            class: 'btn btn-ghost', type: 'button', 'aria-label': `Delete ${k}`,
            onclick: async () => { await api.call('yun', 'bridge_delete', { key: k }); refreshData(); },
          }, 'Delete')))));
      }
      lastData = values;
    } catch (err) {
      if (err.code === 'auth') signedOut();
    }
  }

  // --- Settings ------------------------------------------------------------

  const ZONES = [
    'UTC', 'Africa/Johannesburg', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/New_York',
    'America/Sao_Paulo', 'America/Toronto', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Shanghai', 'Asia/Singapore',
    'Asia/Tokyo', 'Australia/Adelaide', 'Australia/Brisbane', 'Australia/Melbourne', 'Australia/Perth',
    'Australia/Sydney', 'Europe/Amsterdam', 'Europe/Berlin', 'Europe/London', 'Europe/Madrid', 'Europe/Paris',
    'Europe/Rome', 'Pacific/Auckland',
  ];

  // --- Appearance ----------------------------------------------------------

  function setupTheme() {
    let saved = 'auto';
    try { saved = localStorage.getItem('yun-theme') || 'auto'; } catch (e) { /* no storage */ }
    const pick = $(`#theme-seg input[value="${saved}"]`) || $('#theme-seg input[value="auto"]');
    pick.checked = true;
    $('#theme-seg').addEventListener('change', (ev) => {
      const theme = ev.target.value;
      if (theme === 'auto') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = theme;
      try {
        if (theme === 'auto') localStorage.removeItem('yun-theme');
        else localStorage.setItem('yun-theme', theme);
      } catch (e) { /* not saved, but still applied until the page reloads */ }
      // The memory graph reads the accent colour when it draws.
      drawSpark($('#mem-spark'), memHistory);
    });
  }

  function setupSettings() {
    $('#set-zone').replaceChildren(...ZONES.map((z) => el('option', { value: z }, z.replace(/_/g, ' '))));

    $('#board-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await api.call('yun', 'settings_set', {
          hostname: $('#set-hostname').value.trim(),
          zonename: $('#set-zone').value,
          rest_secure: $('#set-rest').checked,
        });
        toast('Settings saved');
        poll();
      } catch (err) {
        toast(`Couldn't save: ${err.message}`, true);
      }
    });

    $('#pw-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if ($('#pw1').value !== $('#pw2').value) return toast("The passwords don't match", true);
      try {
        await api.call('yun', 'password_set', { password: $('#pw1').value });
        ev.target.reset();
        toast('Password changed');
      } catch (err) {
        toast(`Couldn't change the password: ${err.message}`, true);
      }
    });

    $('#fw-check').addEventListener('click', async () => {
      const btn = $('#fw-check');
      btn.disabled = true;
      try {
        const r = await api.call('yun', 'update_check');
        if (r.available) {
          setBadge($('#fw-badge'), `${r.latest} available`, 'warn');
          $('#fw-apply').hidden = false;
        } else {
          setBadge($('#fw-badge'), 'Up to date', 'ok');
        }
      } catch (err) {
        toast(`Update check failed: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    $('#fw-apply').addEventListener('click', async () => {
      if (!confirm('Download and install the update? The Yún restarts when it is done, and keeps its settings.')) return;
      try {
        await api.call('yun', 'update_apply');
        toast('Installing the update. The Yún will restart in a few minutes.');
      } catch (err) {
        toast(`Update failed: ${err.message}`, true);
      }
    });

    $('#reboot').addEventListener('click', async () => {
      if (!confirm('Restart the Yún?')) return;
      await api.call('system', 'reboot').catch(() => {});
      toast('Restarting…');
    });
  }

  // --- Login ---------------------------------------------------------------

  function signedOut() {
    clearInterval(pollTimer);
    clearTimeout(clockTimer);
    clearInterval(dataTimer);
    api.logout();
    $('#shell').hidden = true;
    $('#login').hidden = false;
    $('#login-password').focus();
  }

  async function signedIn() {
    $('#login').hidden = true;
    $('#shell').hidden = false;
    showView(location.hash.slice(1) || 'overview');
    await poll();
    pollTimer = setInterval(poll, 3000);
  }

  async function start() {
    $('#login-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      $('#login-error').hidden = true;
      try {
        await api.login($('#login-password').value);
        $('#login-password').value = '';
        signedIn();
      } catch (err) {
        $('#login-error').textContent = 'Wrong password';
        $('#login-error').hidden = false;
      }
    });
    $('#logout').addEventListener('click', signedOut);
    window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
    $('#scan-btn').addEventListener('click', scan);
    $('#join-form').addEventListener('submit', join);
    $('#join-cancel').addEventListener('click', () => ($('#join-form').hidden = true));
    $('#join-enc').addEventListener('change', updateKeyField);
    $('#ap-btn').addEventListener('click', async () => {
      if (!confirm('Switch to setup mode? The Yún leaves its Wi-Fi network and starts its own.')) return;
      await api.call('yun', 'wifi_setup_ap').catch((e) => toast(e.message, true));
      toast('Switching to setup mode…');
    });
    $('#reset-mcu').addEventListener('click', async () => {
      await api.call('yun', 'mcu_reset').catch((e) => toast(e.message, true));
      toast('Sketch restarted');
    });
    $('#ds-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      await api.call('yun', 'bridge_put', { key: $('#ds-key').value, value: $('#ds-value').value })
        .catch((e) => toast(e.message, true));
      ev.target.reset();
      refreshData();
    });
    $('#mb-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      await api.call('yun', 'mailbox_send', { message: $('#mb-text').value })
        .then(() => toast('Message sent'), (e) => toast(e.message, true));
      ev.target.reset();
    });
    setupDropzones();
    setupSettings();
    setupTheme();

    $('#app').classList.remove('booting');
    // Reuse a session from this tab if it's still valid.
    if (api.session !== NULL_SESSION) {
      try {
        await api.call('yun', 'status');
        return signedIn();
      } catch (err) { /* fall through to the login form */ }
    }
    signedOut();
  }

  document.addEventListener('DOMContentLoaded', start);
})();
