/**
 * dashboard.js — Shared JS for dashboard/admin panel pages
 * Handles: sidebar toggle, active nav highlighting, CSRF helper, toast notifications
 * Requires: base.css, dashboard.css
 *
 * @module DashboardCore
 * @version 2.0.0
 */

(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════
     SIDEBAR TOGGLE
  ═══════════════════════════════════════════════════ */
  function initSidebar() {
    const sidebar   = document.querySelector('.dash-sidebar');
    const overlay   = document.querySelector('.sidebar-overlay');
    const toggleBtn = document.querySelector('.topbar-toggle');

    if (!sidebar) return;

    function openSidebar() {
      sidebar.classList.add('open');
      overlay && overlay.classList.add('show');
      document.body.style.overflow = 'hidden';
    }

    function closeSidebar() {
      sidebar.classList.remove('open');
      overlay && overlay.classList.remove('show');
      document.body.style.overflow = '';
    }

    toggleBtn && toggleBtn.addEventListener('click', function () {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });

    overlay && overlay.addEventListener('click', closeSidebar);

    // Close on Escape
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeSidebar();
    });

    // Close sidebar when a link is clicked on small screens
    sidebar.querySelectorAll('.sidebar-item').forEach(function (item) {
      item.addEventListener('click', function () {
        if (window.innerWidth < 1024) closeSidebar();
      });
    });
  }

  /* ═══════════════════════════════════════════════════
     ACTIVE NAV HIGHLIGHTING
  ═══════════════════════════════════════════════════ */
  function initActiveNav() {
    const path = window.location.pathname;
    document.querySelectorAll('.sidebar-item[href]').forEach(function (el) {
      if (el.getAttribute('href') === path) {
        el.classList.add('active');
      }
    });
  }

  /* ═══════════════════════════════════════════════════
     CSRF TOKEN HELPER
  ═══════════════════════════════════════════════════ */
  window.getCSRFToken = function () {
    const cookie = document.cookie
      .split(';')
      .map(function (c) { return c.trim(); })
      .find(function (c) { return c.startsWith('csrftoken='); });
    return cookie ? cookie.split('=')[1] : '';
  };

  /**
   * Perform a secure fetch with CSRF token automatically attached.
   * @param {string} url
   * @param {object} options - fetch options
   * @returns {Promise<Response>}
   */
  window.secureFetch = function (url, options) {
    options = options || {};
    options.headers = Object.assign({
      'X-CSRFToken': window.getCSRFToken(),
      'Content-Type': 'application/json',
    }, options.headers || {});
    options.credentials = 'same-origin';
    return fetch(url, options);
  };

  /* ═══════════════════════════════════════════════════
     TOAST NOTIFICATIONS
  ═══════════════════════════════════════════════════ */
  (function setupToasts() {
    var container = document.querySelector('.dash-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'dash-toast-container';
      container.setAttribute('role', 'alert');
      container.setAttribute('aria-live', 'polite');
      Object.assign(container.style, {
        position: 'fixed', bottom: '1.5rem', right: '1.5rem',
        zIndex: '9000', display: 'flex', flexDirection: 'column', gap: '8px',
        pointerEvents: 'none',
      });
      document.body.appendChild(container);
    }

    /**
     * Show a toast notification.
     * @param {string} message
     * @param {'success'|'error'|'warning'|'info'} type
     * @param {number} duration - ms before auto-dismiss
     */
    window.showToast = function (message, type, duration) {
      type     = type || 'info';
      duration = duration || 4000;

      var icons = { success: '✓', error: '✗', warning: '⚠', info: 'ℹ' };
      var colors = {
        success: '#4caf50', error: '#c62828',
        warning: '#f47c20', info: '#1245a8',
      };

      var toast = document.createElement('div');
      toast.style.cssText = [
        'background:#fff', 'border-radius:10px', 'padding:.85rem 1.2rem',
        'box-shadow:0 8px 32px rgba(0,0,0,.18)', 'min-width:280px',
        'display:flex', 'align-items:center', 'gap:10px',
        'font-size:.85rem', 'color:#252825',
        'border-left:4px solid ' + colors[type],
        'animation:toastInDB .3s ease forwards',
        'pointer-events:auto',
      ].join(';');

      toast.innerHTML =
        '<span style="font-weight:700;color:' + colors[type] + '">' + icons[type] + '</span>' +
        '<span style="flex:1">' + message + '</span>' +
        '<button onclick="this.parentNode.remove()" style="background:none;border:none;cursor:pointer;font-size:1.1rem;color:#9a9e9b;padding:0 0 0 8px">×</button>';

      container.appendChild(toast);

      setTimeout(function () {
        toast.style.animation = 'toastOutDB .3s ease forwards';
        setTimeout(function () { toast.remove(); }, 300);
      }, duration);
    };

    // Inject keyframes if not present
    if (!document.getElementById('dashToastKF')) {
      var style = document.createElement('style');
      style.id  = 'dashToastKF';
      style.textContent =
        '@keyframes toastInDB{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}' +
        '@keyframes toastOutDB{from{opacity:1;transform:translateY(0)}to{opacity:0;transform:translateY(12px)}}';
      document.head.appendChild(style);
    }
  }());

  /* ═══════════════════════════════════════════════════
     CONFIRM DIALOG HELPER
  ═══════════════════════════════════════════════════ */
  /**
   * Show a styled confirm dialog.
   * @param {string} message
   * @param {function} onConfirm
   * @param {string} [confirmLabel]
   * @param {'danger'|'primary'} [variant]
   */
  window.showConfirm = function (message, onConfirm, confirmLabel, variant) {
    confirmLabel = confirmLabel || 'Confirm';
    variant      = variant || 'danger';

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;padding:1rem';

    var color = variant === 'danger' ? '#c62828' : '#005a48';

    overlay.innerHTML =
      '<div style="background:#fff;border-radius:16px;padding:2rem;max-width:380px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,.18)">' +
        '<p style="font-size:.95rem;color:#252825;margin-bottom:1.6rem;line-height:1.55">' + message + '</p>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button class="cancel-btn" style="padding:.5rem 1.2rem;border-radius:100px;border:1px solid #dde0dd;background:#f8f9f8;cursor:pointer;font-size:.84rem">Cancel</button>' +
          '<button class="ok-btn" style="padding:.5rem 1.2rem;border-radius:100px;border:none;background:' + color + ';color:#fff;cursor:pointer;font-size:.84rem;font-weight:600">' + confirmLabel + '</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);
    overlay.querySelector('.cancel-btn').addEventListener('click', function () { overlay.remove(); });
    overlay.querySelector('.ok-btn').addEventListener('click', function () { overlay.remove(); onConfirm(); });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
  };

  /* ═══════════════════════════════════════════════════
     AUTO-DISMISS DJANGO MESSAGES
  ═══════════════════════════════════════════════════ */
  function autoDismissMessages() {
    document.querySelectorAll('.dash-message, .alert-auto-dismiss').forEach(function (el) {
      var duration = parseInt(el.dataset.duration || '5000');
      setTimeout(function () {
        el.style.transition = 'opacity .4s';
        el.style.opacity = '0';
        setTimeout(function () { el.remove(); }, 400);
      }, duration);

      var closeBtn = el.querySelector('.alert-close');
      closeBtn && closeBtn.addEventListener('click', function () { el.remove(); });
    });
  }

  /* ═══════════════════════════════════════════════════
     TOPBAR SEARCH STUB (hookable)
  ═══════════════════════════════════════════════════ */
  function initTopbarSearch() {
    var searchInput = document.querySelector('.topbar-search-input');
    if (!searchInput) return;
    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        var q = searchInput.value.trim();
        if (q && typeof window.onTopbarSearch === 'function') window.onTopbarSearch(q);
      }
    });
  }

  /* ═══════════════════════════════════════════════════
     INIT
  ═══════════════════════════════════════════════════ */
  document.addEventListener('DOMContentLoaded', function () {
    initSidebar();
    initActiveNav();
    autoDismissMessages();
    initTopbarSearch();
  });

}());
