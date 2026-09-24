/* sudo_base.js - fixed to match HTML IDs */
(function() {
  const sidebar = document.getElementById('sudoSidebar');
  const menuBtn = document.getElementById('sudoMenuBtn');
  
  // Create overlay if not present
  let overlay = document.querySelector('.sudo-sidebar-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'sudo-sidebar-overlay';
    document.body.appendChild(overlay);
  }

  function openMenu() {
    sidebar.classList.add('open');
    overlay.classList.add('show');
    menuBtn.setAttribute('aria-expanded', 'true');
  }
  function closeMenu() {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
    menuBtn.setAttribute('aria-expanded', 'false');
  }

  if (menuBtn) menuBtn.addEventListener('click', function() {
    sidebar.classList.contains('open') ? closeMenu() : openMenu();
  });
  if (overlay) overlay.addEventListener('click', closeMenu);

  window.addEventListener('resize', function() {
    if (window.innerWidth >= 1024) closeMenu();
  });
})();
