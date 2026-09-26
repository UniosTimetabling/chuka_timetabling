// mobile_api/static/mobile_api/js/mobile_analytics.js
// -----------------------------------------------------------------------
// "Share the App" card on the Mobile Analytics dashboard: renders the QR
// code, wires copy / print, and lets staff edit the download link.
//
// The QR is generated fully client-side (vendor/qrcode.js, MIT, no network
// call) so it keeps working on a university-LAN-only deployment.
//
// Editing: POSTs to /api/mobile/admin/app-link/save/. The server stores it
// in SiteSettings.mobile_app_share_url, which the mobile app reads from
// GET /api/mobile/app-link/ — so one edit here changes the QR below AND
// what every device shares, with no new app release.
// -----------------------------------------------------------------------
(function () {
  var mount = document.getElementById('ma-share-qr');
  if (!mount) return;

  var url = mount.getAttribute('data-url') || '';
  var linkEl = document.getElementById('ma-share-link');
  var badgeEl = document.getElementById('ma-share-badge');

  function renderQr() {
    if (!url) { mount.innerHTML = ''; return; }
    try {
      // typeNumber 0 = smallest version that fits; 'M' = ~15% error correction.
      var qr = qrcode(0, 'M');
      qr.addData(url);
      qr.make();
      mount.innerHTML = qr.createSvgTag(6, 4);
    } catch (e) {
      mount.innerHTML = '<p style="font-size:.75rem;color:#94a3b8;">QR code unavailable.</p>';
    }
  }
  renderQr();

  // ── Copy ────────────────────────────────────────────────────────────
  var copyBtn = document.getElementById('ma-share-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      var restore = copyBtn.textContent;
      var done = function () {
        copyBtn.textContent = 'Copied!';
        setTimeout(function () { copyBtn.textContent = restore; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(function () { fallbackCopy(url, done); });
      } else {
        fallbackCopy(url, done);
      }
    });
  }

  function fallbackCopy(text, done) {
    var tmp = document.createElement('textarea');
    tmp.value = text;
    tmp.style.position = 'fixed';
    tmp.style.opacity = '0';
    document.body.appendChild(tmp);
    tmp.focus();
    tmp.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(tmp);
    done();
  }

  var printBtn = document.getElementById('ma-share-print');
  if (printBtn) printBtn.addEventListener('click', function () { window.print(); });

  // ── Edit link ───────────────────────────────────────────────────────
  var editBox = document.getElementById('ma-share-edit');
  var toggleBtn = document.getElementById('ma-share-edit-toggle');
  var input = document.getElementById('ma-share-input');
  var saveBtn = document.getElementById('ma-share-save');
  var resetBtn = document.getElementById('ma-share-reset');
  var cancelBtn = document.getElementById('ma-share-cancel');
  var msg = document.getElementById('ma-share-msg');
  if (!editBox || !toggleBtn || !input) return;

  var savedCustom = input.value;

  function setMsg(text, kind) {
    msg.textContent = text || '';
    msg.className = 'ma-share-msg' + (kind ? ' ' + kind : '');
  }
  function openEdit() { editBox.hidden = false; input.focus(); setMsg(''); }
  function closeEdit() { editBox.hidden = true; input.value = savedCustom; setMsg(''); }

  toggleBtn.addEventListener('click', function () { editBox.hidden ? openEdit() : closeEdit(); });
  cancelBtn.addEventListener('click', closeEdit);

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function badgeText(d) {
    if (d.source === 'custom') return 'Custom link';
    if (d.source === 'apk') return 'Direct APK' + (d.version ? ' · v' + d.version : '');
    return 'Play Store (default)';
  }

  function save(newValue) {
    saveBtn.disabled = resetBtn.disabled = true;
    setMsg('Saving…');
    var body = new URLSearchParams();
    body.append('url', newValue);
    fetch('/api/mobile/admin/app-link/save/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString()
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.d.ok) throw new Error((res.d && res.d.error) || 'Could not save.');
        url = res.d.url;
        savedCustom = res.d.customUrl || '';
        input.value = savedCustom;
        mount.setAttribute('data-url', url);
        linkEl.textContent = url;
        badgeEl.textContent = badgeText(res.d);
        renderQr();
        setMsg('Saved — the app will use this link.', 'ok');
        setTimeout(function () { if (!editBox.hidden) { editBox.hidden = true; setMsg(''); } }, 1800);
      })
      .catch(function (e) { setMsg(e.message || 'Could not save.', 'err'); })
      .then(function () { saveBtn.disabled = resetBtn.disabled = false; });
  }

  saveBtn.addEventListener('click', function () {
    var v = input.value.trim();
    if (!v) { setMsg('Enter a link, or press “Use default”.', 'err'); return; }
    save(v);
  });
  resetBtn.addEventListener('click', function () { input.value = ''; save(''); });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') saveBtn.click(); });
})();
