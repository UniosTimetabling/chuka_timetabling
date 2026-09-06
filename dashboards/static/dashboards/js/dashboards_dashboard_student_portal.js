/*
 * dashboards_dashboard_student_portal.js
 * Extracted inline JS from: dashboards/templates/dashboard/student_portal.html
 * NOTE: May contain Django template vars - render through Django
 */

(function(){
  function loadBg(el, src, fallback, onFail){
    var img=new Image();
    img.onload=function(){el.style.backgroundImage='url('+src+')';};
    img.onerror=function(){ if(fallback){var i2=new Image();i2.onload=function(){el.style.backgroundImage='url('+fallback+')';};i2.onerror=function(){if(onFail)onFail();};i2.src=fallback;}else{if(onFail)onFail();} };
    img.src=src;
  }
  document.querySelectorAll('.js-slide').forEach(function(s){
    loadBg(s, s.dataset.src, s.dataset.fallback, function(){ s.classList.add('hero-no-image'); });
  });
})();