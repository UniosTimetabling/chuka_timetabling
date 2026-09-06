  // Toggle year body open/close
  function toggleYear(header) {
    const body = header.nextElementSibling;
    const chevron = header.querySelector('.year-chevron');
    const isOpen = body.classList.contains('open');
    body.classList.toggle('open', !isOpen);
    chevron.classList.toggle('open', !isOpen);
  }

  // Open first year by default on load
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.year-header').forEach(function (h, i) {
      if (i === 0) {
        h.nextElementSibling.classList.add('open');
        h.querySelector('.year-chevron').classList.add('open');
      }
    });
  });

  // Switch semester tabs
  function switchSem(tabEl, panelId) {
    const tabs = tabEl.parentElement.querySelectorAll('.sem-tab');
    tabs.forEach(t => t.classList.remove('active'));
    tabEl.classList.add('active');

    // Hide all panels in this year-body
    const yearBody = tabEl.closest('.year-body');
    yearBody.querySelectorAll('.sem-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.add('active');
  }

  // Toggle raw report text
  function toggleReport(btn) {
    const box = btn.nextElementSibling;
    const visible = box.style.display !== 'none';
    box.style.display = visible ? 'none' : 'block';
    btn.innerHTML = visible
      ? '<i class="fas fa-file-alt"></i> Show Full Report'
      : '<i class="fas fa-eye-slash"></i> Hide Report';
  }
