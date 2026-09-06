/*
 * course_allocation_course_allocation_base.js
 * Extracted inline JS from: course_allocation/templates/course_allocation/base.html
 * NOTE: May contain Django template vars - render through Django
 */

(function() {
  "use strict";

  // Safe text escaper for message fallbacks
  function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe.replace(/[&<>'"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c];
    });
  }

  /* ── Navigation Toggle ── */
  const navToggle  = document.getElementById('navToggle');
  const navClose   = document.getElementById('navClose');
  const sideNav    = document.getElementById('sideNav');
  const navOverlay = document.getElementById('navOverlay');

  function openNav() {
    sideNav.classList.add('active');
    navOverlay.classList.add('active');
    navToggle.classList.add('is-open');
    document.body.style.overflow = 'hidden';
  }
  function closeNav() {
    sideNav.classList.remove('active');
    navOverlay.classList.remove('active');
    navToggle.classList.remove('is-open');
    document.body.style.overflow = '';
  }
  navToggle.addEventListener('click', () => sideNav.classList.contains('active') ? closeNav() : openNav());
  navClose.addEventListener('click', closeNav);
  navOverlay.addEventListener('click', closeNav);
  document.addEventListener('keydown', e => e.key === 'Escape' && closeNav());

  // Mark active nav link
  const currentPath = window.location.pathname;
  document.querySelectorAll('.nav-item').forEach(a => {
    if (a.getAttribute('href') === currentPath) a.classList.add('active-link');
  });

  /* ── Search Bar ── */
  const searchToggle = document.getElementById('searchToggle');
  const searchBar    = document.getElementById('searchBar');
  const searchInput  = document.getElementById('searchInput');
  const clearSearchBtn = document.getElementById('clearSearchBtn');

  searchToggle.addEventListener('click', () => {
    const open = searchBar.classList.toggle('open');
    if (open) { searchBar.style.display = 'flex'; searchInput.focus(); }
    else       { searchBar.style.display = 'none'; clearSearch(); }
  });

  function clearSearch() {
    searchInput.value = '';
    document.querySelectorAll('mark.search-hit').forEach(m => m.outerHTML = m.innerHTML);
    document.querySelectorAll('[data-searchable]').forEach(row => row.style.display = '');
    if (typeof window.onSearchClear === 'function') window.onSearchClear();
  }
  clearSearchBtn.addEventListener('click', clearSearch);

  function debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  searchInput.addEventListener('input', debounce(function() {
    const q = this.value.toLowerCase().trim();
    if (typeof window.onSearch === 'function') { window.onSearch(q); return; }
    // Default: highlight & filter [data-searchable] rows
    document.querySelectorAll('[data-searchable]').forEach(row => {
      document.querySelectorAll('mark.search-hit', row).forEach(m => m.outerHTML = m.innerHTML);
      if (!q) { row.style.display = ''; return; }
      const text = row.textContent.toLowerCase();
      if (text.includes(q)) {
        row.style.display = '';
        row.querySelectorAll('td').forEach(cell => {
          if (cell.textContent.toLowerCase().includes(q)) {
            const rx = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
            cell.innerHTML = cell.textContent.replace(rx, '<mark class="search-hit">$1</mark>');
          }
        });
      } else {
        row.style.display = 'none';
      }
    });
  }, 280));

  /* ── Notifications ── */
  const NOTIF_API  = "/api/notifications/";
  const NOTIF_READ = "/api/notifications/read/";
  const bell       = document.getElementById('notifBell');
  const badge      = document.getElementById('notifBadge');
  const overlay    = document.getElementById('notifOverlay');
  const nmClose    = document.getElementById('nmClose');
  const nmMarkAll  = document.getElementById('nmMarkAll');
  const nmRefresh  = document.getElementById('nmRefresh');
  const nmLoading  = document.getElementById('nmLoading');
  const nmEmpty    = document.getElementById('nmEmpty');
  const nmAlerts   = document.getElementById('nmAlerts');
  const nmSubmissions = document.getElementById('nmSubmissions');

  let _notifications = [];
  let _submissions   = [];
  let _activeTab     = 'alerts';

  function setBadge(n) {
    badge.textContent = n > 99 ? '99+' : n;
    badge.classList.toggle('hidden', n === 0);
  }

  function renderAlerts(notifs) {
    nmAlerts.innerHTML = '';
    if (!notifs || notifs.length === 0) {
      nmEmpty.classList.remove('hidden');
      return;
    }
    nmEmpty.classList.add('hidden');
    notifs.forEach(n => {
      const div = document.createElement('div');
      div.className = `nm-item ${n.is_read ? '' : 'unread'} sev-${n.severity || 'info'}`;
      div.dataset.id = n.id;
      const msgHtml = (n.html && n.html.length) ? n.html : escapeHtml(n.message);
      div.innerHTML = `
        <div class="nm-dot ${n.is_read ? 'read' : 'unread'}"></div>
        <div class="nm-ibody">
          <div class="nm-msg">${msgHtml}</div>
          <div class="nm-meta">
            <span class="nm-time">${n.created_at || ''}</span>
            <button class="nm-dismiss" data-id="${n.id}" title="Dismiss">✕</button>
          </div>
        </div>`;
      div.addEventListener('click', e => {
        if (e.target.classList.contains('nm-dismiss')) return;
        // If the message contains an anchor, follow the first link in a new tab
        const firstLink = div.querySelector('.nm-msg a');
        if (firstLink) {
          window.open(firstLink.href, '_blank', 'noopener');
        }
        markRead(n.id);
      });
      div.querySelector('.nm-dismiss').addEventListener('click', e => {
        e.stopPropagation(); dismissNotif(n.id);
      });
      nmAlerts.appendChild(div);
    });
  }

  function renderSubmissions(subs) {
    nmSubmissions.innerHTML = '';
    if (!subs || subs.length === 0) {
      nmSubmissions.innerHTML = '<div class="nm-empty"><div class="nm-empty-icon">📤</div>No submission status available.</div>';
      return;
    }
    const sec = document.createElement('div');
    sec.className = 'nm-sub-sec';
    sec.innerHTML = '<div class="nm-sub-title">Submission Status</div><div class="nm-sub-list"></div>';
    const list = sec.querySelector('.nm-sub-list');
    subs.forEach(s => {
      const item = document.createElement('div');
      item.className = `nm-sub-item ${s.submitted ? 'ok' : 'no'}`;
      item.innerHTML = `<span>${s.submitted ? '✅' : '⏳'}</span><span>${s.label}</span>`;
      list.appendChild(item);
    });
    nmSubmissions.appendChild(sec);
  }

  async function loadNotifications() {
    nmLoading.style.display = 'block';
    nmEmpty.classList.add('hidden');
    nmAlerts.style.display = 'none';
    nmSubmissions.style.display = 'none';
    try {
      const res = await fetch(NOTIF_API, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const data = await res.json();
      _notifications = data.notifications || [];
      _submissions   = data.submissions   || [];
      setBadge(_notifications.filter(n => !n.is_read).length);
      nmLoading.style.display = 'none';
      showTab(_activeTab);
    } catch(e) {
      nmLoading.innerHTML = '<div class="nm-empty-icon">⚠️</div>Could not load notifications.';
    }
  }

  function showTab(tab) {
    _activeTab = tab;
    document.querySelectorAll('.nm-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    nmAlerts.style.display      = tab === 'alerts'      ? 'block' : 'none';
    nmSubmissions.style.display = tab === 'submissions' ? 'block' : 'none';
    if (tab === 'alerts')      renderAlerts(_notifications);
    if (tab === 'submissions') renderSubmissions(_submissions);
  }

  async function markRead(id) {
    const csrftoken = document.cookie.split('; ').find(r => r.startsWith('csrftoken='))?.split('=')[1];
    try {
      await fetch(NOTIF_READ, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrftoken, 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
      });
      const n = _notifications.find(x => x.id == id);
      if (n) { n.is_read = true; }
      setBadge(_notifications.filter(n => !n.is_read).length);
      renderAlerts(_notifications);
    } catch(e) {}
  }

  function dismissNotif(id) {
    _notifications = _notifications.filter(x => x.id != id);
    setBadge(_notifications.filter(n => !n.is_read).length);
    renderAlerts(_notifications);
  }

  bell.addEventListener('click', () => {
    overlay.classList.toggle('open');
    if (overlay.classList.contains('open')) loadNotifications();
  });
  nmClose.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('open'); });
  document.querySelectorAll('.nm-tab').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
  nmMarkAll.addEventListener('click', async () => {
    const csrftoken = document.cookie.split('; ').find(r => r.startsWith('csrftoken='))?.split('=')[1];
    try {
      await fetch(NOTIF_READ, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrftoken, 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json' },
        body: JSON.stringify({ mark_all: true })
      });
      _notifications.forEach(n => n.is_read = true);
      setBadge(0);
      renderAlerts(_notifications);
    } catch(e) {}
  });
  nmRefresh.addEventListener('click', loadNotifications);

  // Poll badge every 60 seconds
  (async function pollBadge() {
    try {
      const res = await fetch(NOTIF_API, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const data = await res.json();
      _notifications = data.notifications || [];
      setBadge(_notifications.filter(n => !n.is_read).length);
    } catch(e) {}
    setTimeout(pollBadge, 60000);
  })();

  // Expose helpers
  window.closeNav = closeNav;
})();