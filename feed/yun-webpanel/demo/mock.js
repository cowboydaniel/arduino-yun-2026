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
      memory: { total: 60e6, available: avail },
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
        case 'update_apply': return {};
      }
      throw new Error(`no mock for ${object}.${method}`);
    },
    async upload(path, file, onProgress) {
      for (let i = 1; i <= 10; i++) { await wait(80); onProgress(i / 10); }
    },
  };
  sessionStorage.setItem('yun-session', 'demo');
})();
