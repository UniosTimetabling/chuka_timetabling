/**
 * panel.js — Shared JS for timetable/allocation panel pages
 * Handles: header scrolling, sub-nav active state, toasts, modals, CSRF, loading overlay
 *
 * @module PanelCore
 * @version 2.0.0
 */

(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════
     PAGE LOAD TRANSITION
  ═══════════════════════════════════════════════════ */
  function initPageTransition() {
    var overlay = document.getElementById('pageTransition');
    if (!overlay) return;

    // Hide overlay on load
    window.addEventListener('load', function () {
      overlay.classList.remove('show');
    });

    // Show on link clicks (excluding external, download, hash links)
    document.addEventListener('click', function (e) {
      var link = e.target.closest('a[href]');
      if (!link) return;
      var href = link.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('mailto:') ||
          href.startsWith('javascript:') || link.hasAttribute('download') ||
          link.target === '_blank') return;
      if (link.origin !== location.origin) return;
      // Don't show for AJAX endpoints
      if (href.includes('/api/') || href.includes('/ajax/')) return;
      overlay.classList.add('show');
    });
  }

  /* ═══════════════════════════════════════════════════
     SUBNAV ACTIVE STATE
  ═══════════════════════════════════════════════════ */
  function initSubnav() {
    var path = window.location.pathname;
    document.querySelectorAll('.panel-subnav-tab[href]').forEach(function (tab) {
      if (tab.getAttribute('href') === path) {
        tab.classList.add('active');
      }
    });
  }

  /* ═══════════════════════════════════════════════════
     CSRF HELPER
  ═══════════════════════════════════════════════════ */
  window.getPanelCSRF = function () {
    var cookie = document.cookie
      .split(';')
      .map(function (c) { return c.trim(); })
      .find(function (c) { return c.startsWith('csrftoken='); });
    return cookie ? cookie.split('=')[1] : (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
  };

  /**
   * Secure fetch for panel pages.
   * @param {string} url
   * @param {RequestInit} [opts]
   * @returns {Promise<Response>}
   */
  window.panelFetch = function (url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.headers = Object.assign({
      'X-CSRFToken': window.getPanelCSRF(),
      'Content-Type': 'application/json',
    }, opts.headers || {});
    return fetch(url, opts);
  };

  /* ═══════════════════════════════════════════════════
     TOAST NOTIFICATIONS
  ═══════════════════════════════════════════════════ */
  (function setupPanelToasts() {
    var container = document.querySelector('.panel-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'panel-toast-container';
      container.setAttribute('role', 'alert');
      container.setAttribute('aria-live', 'polite');
      document.body.appendChild(container);
    }

    var colorMap = {
      success: '#4caf50', error: '#c62828', warning: '#f47c20', info: '#0277bd',
    };
    var iconMap = { success: '✓', error: '✗', warning: '⚠', info: 'ℹ' };

    /**
     * Show a panel toast.
     * @param {string} msg
     * @param {'success'|'error'|'warning'|'info'} [type]
     * @param {number} [duration]
     */
    window.panelToast = function (msg, type, duration) {
      type     = type || 'info';
      duration = duration || 4500;
      var color = colorMap[type] || colorMap.info;
      var icon  = iconMap[type]  || iconMap.info;

      var t = document.createElement('div');
      t.className = 'panel-toast ' + type;
      t.innerHTML =
        '<span style="font-weight:700;color:' + color + ';flex-shrink:0">' + icon + '</span>' +
        '<span style="flex:1">' + msg + '</span>' +
        '<button onclick="this.parentNode.remove()" style="background:none;border:none;cursor:pointer;font-size:1.1rem;color:#697aa3;padding:0 0 0 8px;line-height:1">×</button>';
      container.appendChild(t);

      setTimeout(function () {
        t.style.transition = 'opacity .3s';
        t.style.opacity = '0';
        setTimeout(function () { t.remove(); }, 300);
      }, duration);
    };
  }());

  /* ═══════════════════════════════════════════════════
     LOADING OVERLAY
  ═══════════════════════════════════════════════════ */
  /**
   * Show/hide a full-page loading overlay.
   * @param {boolean} show
   * @param {string} [message]
   */
  window.setPanelLoading = function (show, message) {
    var id = 'panelGlobalLoading';
    var existing = document.getElementById(id);
    if (show) {
      if (existing) return;
      var el = document.createElement('div');
      el.id = id;
      el.style.cssText = 'position:fixed;inset:0;background:rgba(244,247,251,.8);z-index:8000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px';
      el.innerHTML =
        '<div style="width:44px;height:44px;border:4px solid #dde6f5;border-top:4px solid #f47c20;border-radius:50%;animation:panelSpin .7s linear infinite"></div>' +
        '<p style="font-size:.87rem;color:#3a4a6b;font-weight:500">' + (message || 'Loading…') + '</p>';
      document.body.appendChild(el);
    } else {
      existing && existing.remove();
    }
  };

  /* ═══════════════════════════════════════════════════
     MODAL HELPER
  ═══════════════════════════════════════════════════ */
  /**
   * Open a modal element by id.
   * @param {string} id
   */
  window.openPanelModal = function (id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    var focusable = modal.querySelector('input, button, select, textarea');
    focusable && focusable.focus();
  };

  /**
   * Close a modal element by id.
   * @param {string} id
   */
  window.closePanelModal = function (id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modal.style.display = 'none';
    document.body.style.overflow = '';
  };

  // Auto-close modals on overlay click
  document.addEventListener('click', function (e) {
    if (e.target.classList.contains('panel-modal-overlay')) {
      e.target.style.display = 'none';
      document.body.style.overflow = '';
    }
  });

  /* ═══════════════════════════════════════════════════
     CONFIRM DIALOG
  ═══════════════════════════════════════════════════ */
  /**
   * Panel-styled confirm dialog.
   * @param {string} message
   * @param {function} onConfirm
   * @param {string} [btnLabel]
   * @param {'danger'|'primary'} [variant]
   */
  window.panelConfirm = function (message, onConfirm, btnLabel, variant) {
    btnLabel = btnLabel || 'Confirm';
    variant  = variant || 'danger';
    var color = variant === 'danger' ? '#c62828' : '#f47c20';

    var ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;padding:1rem';
    ov.innerHTML =
      '<div style="background:#fff;border-radius:16px;padding:2rem;max-width:400px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,.2)">' +
        '<p style="font-size:.94rem;color:#0d1b3e;margin-bottom:1.6rem;line-height:1.55">' + message + '</p>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button class="c-cancel" style="padding:.5rem 1.2rem;border-radius:6px;border:1px solid #dde6f5;background:#f4f7fb;cursor:pointer;font-size:.84rem;color:#3a4a6b">Cancel</button>' +
          '<button class="c-ok" style="padding:.5rem 1.2rem;border-radius:6px;border:none;background:' + color + ';color:#fff;cursor:pointer;font-size:.84rem;font-weight:600">' + btnLabel + '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    ov.querySelector('.c-cancel').addEventListener('click', function () { ov.remove(); });
    ov.querySelector('.c-ok').addEventListener('click', function () { ov.remove(); onConfirm(); });
    ov.addEventListener('click', function (e) { if (e.target === ov) ov.remove(); });
  };

  /* ═══════════════════════════════════════════════════
     FORM VALIDATION HELPERS
  ═══════════════════════════════════════════════════ */
  /**
   * Show/clear a validation error on a form field.
   * @param {HTMLElement} field
   * @param {string|null} message - null to clear error
   */
  window.setFieldError = function (field, message) {
    var existing = field.parentNode.querySelector('.field-error-msg');
    if (message) {
      field.style.borderColor = '#c62828';
      if (!existing) {
        var el = document.createElement('p');
        el.className = 'field-error-msg';
        el.style.cssText = 'font-size:.74rem;color:#c62828;margin-top:3px';
        field.parentNode.appendChild(el);
        existing = el;
      }
      existing.textContent = message;
    } else {
      field.style.borderColor = '';
      existing && existing.remove();
    }
  };

  /* ═══════════════════════════════════════════════════
     SEARCH / FILTER TABLE
  ═══════════════════════════════════════════════════ */
  /**
   * Attach a live-search filter to a table.
   * @param {HTMLInputElement} input
   * @param {HTMLTableElement} table
   * @param {number[]} [cols] - column indices to search (default: all)
   */
  window.attachTableSearch = function (input, table, cols) {
    if (!input || !table) return;
    input.addEventListener('input', function () {
      var q = input.value.toLowerCase().trim();
      var rows = table.querySelectorAll('tbody tr');
      rows.forEach(function (row) {
        var cells = cols
          ? cols.map(function (i) { return row.cells[i]; })
          : Array.from(row.cells);
        var text = cells.map(function (c) { return c ? c.textContent.toLowerCase() : ''; }).join(' ');
        row.style.display = (!q || text.includes(q)) ? '' : 'none';
      });
    });
  };

  /* ═══════════════════════════════════════════════════
     INIT
  ═══════════════════════════════════════════════════ */
  document.addEventListener('DOMContentLoaded', function () {
    initPageTransition();
    initSubnav();
  });

}());
