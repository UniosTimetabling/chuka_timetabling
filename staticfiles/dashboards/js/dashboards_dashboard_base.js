/*
 * dashboards_dashboard_base.js
 * Extracted inline JS from: dashboards/templates/dashboard/base.html
 * NOTE: May contain Django template vars - render through Django
 */

/* Mobile nav toggle — no framework dependency */
(function(){
  var btn=document.getElementById('navHamburger');
  var menu=document.getElementById('navLinksMenu');
  if(!btn||!menu)return;
  btn.addEventListener('click',function(){
    var open=menu.classList.toggle('nav-open');
    btn.setAttribute('aria-expanded',open);
  });
  document.addEventListener('click',function(e){
    if(!btn.contains(e.target)&&!menu.contains(e.target)){
      menu.classList.remove('nav-open');
      btn.setAttribute('aria-expanded','false');
    }
  });
})();