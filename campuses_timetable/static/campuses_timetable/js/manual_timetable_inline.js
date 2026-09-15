  // ── Toast helper that pushes into base.html's existing #toastContainer
  //    (class="panel-toast-container"), so there is one toast system, not two. ──
  if (typeof window.toast !== 'function') {
    window.toast = function (msg, type, dur) {
      type = type || 'info';
      dur = dur || 4500;
      var icons = { success: 'check-circle-fill', warning: 'exclamation-triangle-fill', error: 'x-circle-fill', info: 'info-circle-fill' };
      var container = document.getElementById('toastContainer');
      if (!container) return;
      var el = document.createElement('div');
      el.className = 'toast ' + type;
      el.innerHTML = '<i class="bi bi-' + (icons[type] || icons.info) + '"></i>' + msg +
        '<button class="toast-close" onclick="this.parentElement.remove()">×</button>';
      container.appendChild(el);
      setTimeout(function () {
        el.style.animation = 'toastOut 0.25s ease forwards';
        setTimeout(function () { el.remove(); }, 260);
      }, dur);
    };
  }
