// A fake board for previewing the panel without a Yun: `python3 serve.py`.
'use strict';
(function () {
  const start = Date.now();
  const data = { temperature: '23.4', humidity: '41', led13: 'on', lastButton: 'pressed' };
  let wifi = {
    mode: 'client', connected: true, ssid: 'Workshop', signal: -58, quality: 72, channel: 6,
    encryption: 'psk2', ipv4: '192.168.1.45', mac: '90:A2:DA:F0:54:D2',
    rx_bytes: 1080000, tx_bytes: 94600, ap_ssid: 'Arduino Yun-90A2DAF054D2',
  };
  let settings = { hostname: 'workbench-yun', zonename: 'Australia/Sydney', rest_secure: true };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const jobs = {};
  let update = null;
  let usbInstalled = false;
  let vpnState = { installed: false, configured: false };
  const OUTPUT = {
    free: '              total        used        free      shared  buff/cache   available\nMem:          55624       24310       18204         92       13110       27870\nSwap:         27808           0       27808\n',
    'df -h': 'Filesystem                Size      Used Available Use% Mounted on\n/dev/root                 5.8M      5.8M         0 100% /rom\ntmpfs                    27.2M    312.0K     26.9M   1% /tmp\n/dev/mtdblock5            5.6M      1.9M      3.7M  34% /overlay\n/dev/sda1                58.9G      1.1G     57.8G   2% /mnt/sda1\n',
    uptime: ' 14:02:11 up 3 days,  5:04,  load average: 0.08, 0.12, 0.09\n',
    'ip -br addr': 'lo               UNKNOWN        127.0.0.1/8\neth0             UP             192.168.1.135/24\nphy0-sta0        UP             192.168.1.45/24\n',
  };

  function status() {
    const up = 3 * 86400 + 5 * 3600 + (Date.now() - start) / 1000;
    const avail = 21e6 + Math.sin(Date.now() / 9000) * 2.5e6 + Math.random() * 6e5;
    wifi.rx_bytes += Math.round(Math.random() * 4000);
    wifi.tx_bytes += Math.round(Math.random() * 900);
    return {
      hostname: settings.hostname, model: 'Arduino Yún', time: Math.floor(Date.now() / 1000),
      uptime: up, load: [0.08 + Math.random() * 0.1, 0.12, 0.09],
      zonename: settings.zonename, rest_secure: settings.rest_secure,
      firmware: { version: 'Yún 2026.1', openwrt: 'OpenWrt 25.12.5', kernel: '6.12.48', description: 'Arduino Yún 2026 on OpenWrt 25.12.5' },
      bridge: { running: true },
      memory: { total: 60e6, available: avail, swap_total: 296e6, swap_free: 291e6 },
      storage: [
        { name: 'Internal flash', used: 1.9e6, total: 5.6e6 },
        { name: 'SD card', used: 1.1e9, total: 61e9 },
      ],
      wifi: { ...wifi },
      ethernet: { carrier: true, ipv4: '192.168.1.135', gateway: '192.168.1.1', mac: '90:A2:DA:F8:54:D2', rx_bytes: 1100000, tx_bytes: 290000 },
    };
  }

  window.YunMock = {
    async call(object, method, params) {
      await wait(120);
      if (object === 'session' && method === 'login') {
        if (params.password !== 'arduino') throw Object.assign(new Error('denied'), { code: 'auth' });
        return { ubus_rpc_session: 'demo' };
      }
      if (object === 'session') return {};
      if (object === 'system') return {};
      switch (method) {
        case 'status': return status();
        case 'wifi_scan':
          await wait(900);
          return { results: [
            { ssid: 'Workshop', quality: 72, encryption: 'psk2' },
            { ssid: 'Workshop-5G', quality: 40, encryption: 'sae-mixed' },
            { ssid: 'Neighbours', quality: 31, encryption: 'psk2' },
            { ssid: 'Printer-Direct', quality: 55, encryption: 'none' },
            { ssid: 'Cafe Guest', quality: 18, encryption: 'none' },
            { ssid: 'eduroam', quality: 48, encryption: 'wpa3-mixed' },
          ] };
        case 'wifi_client': wifi = { ...wifi, ssid: params.ssid, encryption: params.encryption }; return {};
        case 'wifi_setup_ap': return {};
        case 'sketch_flash':
          await wait(2500);
          return { code: 0, output: 'avrdude: AVR device initialized and ready to accept instructions\navrdude: device signature = 0x1e9587 (probably m32u4)\navrdude: writing 28672 bytes flash ...\navrdude: 28672 bytes of flash verified\n\navrdude done.  Thank you.\n' };
        case 'mcu_reset': return {};
        case 'bridge_data':
          data.temperature = (22 + Math.random() * 3).toFixed(1);
          return { values: { ...data } };
        case 'bridge_put': data[params.key] = params.value; return {};
        case 'bridge_delete': delete data[params.key]; return {};
        case 'mailbox_send': return {};
        case 'settings_set': Object.assign(settings, params); return {};
        case 'password_set': return {};
        case 'update_check': await wait(600); return { current: 'Yún 2026.1', latest: 'Yún 2026.2', available: true };
        case 'update_apply': update = Date.now(); return {};
        case 'update_progress': {
          const t = (Date.now() - update) / 1000;
          if (t > 14) throw new Error('offline');
          if (t < 2) return { stage: 'checking', downloaded: 0, total: null, message: 'stopping services to free memory for the download' };
          if (t < 10) return { stage: 'downloading', downloaded: Math.round(11862289 * (t - 2) / 8), total: 11862289, message: 'downloading Yún 2026.4' };
          if (t < 12) return { stage: 'verifying', downloaded: 11862289, total: 11862289, message: 'download verified' };
          return { stage: 'installing', downloaded: 11862289, total: 11862289, message: 'installing (6420 KB free)' };
        }
        case 'shell_start': {
          const id = Math.random().toString(16).slice(2, 18).padEnd(16, '0');
          let out = OUTPUT[params.command] ?? `sh: ${params.command.split(' ')[0]}: not found in the demo\n`;
          let rc = OUTPUT[params.command] ? 0 : 127, cwd = params.cwd;
          const cd = params.command.match(/^cd\s*(.*)$/);
          if (cd) { out = ''; rc = 0; cwd = cd[1] || '/root'; }
          jobs[id] = { out: new TextEncoder().encode(out), rc, cwd, at: Date.now() };
          return { id };
        }
        case 'shell_poll': {
          const j = jobs[params.id];
          if (!j) throw new Error('No such command');
          const chunk = j.out.slice(params.offset, params.offset + 200);
          const offset = params.offset + chunk.length;
          const done = offset >= j.out.length && Date.now() - j.at > 300;
          const res = { output: btoa(String.fromCharCode(...chunk)), offset, done };
          if (done) { Object.assign(res, { rc: j.stopped ? null : j.rc, cwd: j.cwd, truncated: false }); delete jobs[params.id]; }
          return res;
        }
        case 'usb_devices': return { devices: [
          { path: '1-1.4', id: '058f:6366', name: 'Flash Reader', builtin: true, types: ['Storage'], drivers: ['usb-storage'], needs_driver: false, packages: [], speed: 480 },
          { path: '1-1.1', id: '2341:0043', name: 'Arduino Uno', manufacturer: 'Arduino (www.arduino.cc)', builtin: false, types: ['Communications'], drivers: ['cdc_acm'], needs_driver: false, packages: [], speed: 12 },
          { path: '1-1.2', id: '0e8d:7612', name: '802.11ac WLAN', manufacturer: 'MediaTek Inc.', builtin: false, types: ['Vendor specific'], drivers: [], needs_driver: !usbInstalled, packages: usbInstalled ? [] : ['kmod-mt76x2u'], speed: 480 },
          { path: '1-1.3', id: '046d:0825', name: 'USB device 046d:0825', builtin: false, types: ['Video', 'Audio'], drivers: [], needs_driver: true, packages: ['kmod-video-uvc'], speed: 480 },
        ] };
        case 'usb_install': {
          const id = Math.random().toString(16).slice(2, 18).padEnd(16, '0');
          jobs[id] = { out: new TextEncoder().encode(`(1/2) Installing kmod-mt76-core (6.12.94-r1)\n(2/2) Installing ${params.package} (6.12.94-r1)\nOK: 11 MiB in 184 packages\n`), rc: 0, cwd: '/tmp', at: Date.now() + 1500 };
          usbInstalled = true;
          return { id };
        }
        case 'vpn_status': {
          const st = { ...vpnState };
          if (st.configured && st.enabled) Object.assign(st, { up: true, handshake: Math.floor(Date.now() / 1000) - 12, rx_bytes: 48213, tx_bytes: 90112, public_key: 'kQ3d7e0x8Hc1cZ0v9Qe8yYlq2Zb5m3sT0pWf6rN1aUs=' });
          return st;
        }
        case 'vpn_install': {
          const id = Math.random().toString(16).slice(2, 18).padEnd(16, '0');
          jobs[id] = { out: new TextEncoder().encode('(1/5) Installing kmod-udptunnel4\n(4/5) Installing kmod-wireguard\n(5/5) Installing wireguard-tools\nOK: 11 MiB in 188 packages\n'), rc: 0, cwd: '/tmp', at: Date.now() + 1200 };
          vpnState.installed = true;
          return { id };
        }
        case 'vpn_import':
          if (!/\[Peer\]/.test(params.config)) throw new Error('the file must have exactly one [Peer] (the server); it has 0');
          vpnState = { installed: true, configured: true, enabled: true, server: 'vpn.example.com:51820', addresses: ['10.8.0.5/32'], allowed_ips: ['10.8.0.0/24'] };
          return {};
        case 'vpn_set': vpnState.enabled = params.enabled; return {};
        case 'vpn_remove': vpnState = { installed: true, configured: false }; return {};
        case 'shell_stop': if (jobs[params.id]) jobs[params.id].stopped = true; return {};
      }
      throw new Error(`no mock for ${object}.${method}`);
    },
    async upload(path, file, onProgress) {
      for (let i = 1; i <= 10; i++) { await wait(80); onProgress(i / 10); }
    },
  };
  sessionStorage.setItem('yun-session', 'demo');
})();
