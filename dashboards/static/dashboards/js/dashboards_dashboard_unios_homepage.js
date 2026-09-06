/*
 * dashboards_dashboard_unios_homepage.js
 * Extracted inline JS from: dashboards/templates/dashboard/unios_homepage.html
 * NOTE: May contain Django template vars - render through Django
 */

(function(){
  function loadBg(el, src, fallback, onFail){
    var img = new Image();
    img.onload  = function(){ el.style.backgroundImage = 'url(' + src + ')'; };
    img.onerror = function(){
      if(fallback){
        var i2 = new Image();
        i2.onload  = function(){ el.style.backgroundImage = 'url(' + fallback + ')'; };
        i2.onerror = function(){ if(onFail) onFail(); };
        i2.src = fallback;
      } else { if(onFail) onFail(); }
    };
    img.src = src;
  }

  /* Hero carousel slides */
  document.querySelectorAll('.js-slide').forEach(function(s){
    loadBg(s, s.dataset.src, s.dataset.fallback, function(){
      s.classList.add('hero-no-image');
      s.style.backgroundImage = 'none';
    });
  });

  /* Static hero */
  var sh = document.querySelector('.js-static-hero');
  if(sh){
    var fb0 = 'https://images.unsplash.com/photo-1562774053-701939374585?auto=format&fit=crop&w=1950&q=80';
    loadBg(sh, fb0, null, function(){ sh.classList.add('hero-no-image'); });
  }

  /* Responsibility section bg */
  var rs = document.querySelector('.js-resp-section');
  if(rs && rs.dataset.bg){
    loadBg(rs, rs.dataset.bg, rs.dataset.fallback, function(){
      rs.style.backgroundImage = 'none';
    });
  }

  /* ── Carousel ── */
  var track = document.getElementById('heroTrack');
  var dots  = document.querySelectorAll('.carousel-dot');
  var total = dots.length;
  if(!track || total < 2) return;
  var cur = 0, timer;
  function goTo(n){
    cur = (n + total) % total;
    track.style.transform = 'translateX(-' + (cur*100) + '%)';
    dots.forEach(function(d,i){
      d.classList.toggle('active', i===cur);
      d.setAttribute('aria-selected', i===cur ? 'true' : 'false');
    });
  }
  function startAuto(){ clearInterval(timer); timer = setInterval(function(){ goTo(cur+1); }, 12000); }
  /* prev/next buttons removed from template */
  dots.forEach(function(d){ d.addEventListener('click', function(){ goTo(parseInt(d.dataset.index,10)); startAuto(); }); });
  var c = document.getElementById('heroCarousel');
  if(c){
    c.setAttribute('tabindex','0');
    c.addEventListener('keydown',function(e){ if(e.key==='ArrowLeft'){goTo(cur-1);startAuto();} if(e.key==='ArrowRight'){goTo(cur+1);startAuto();} });
    c.addEventListener('mouseenter',function(){ clearInterval(timer); });
    c.addEventListener('mouseleave', startAuto);
  }
  var tx=0;
  track.addEventListener('touchstart',function(e){ tx=e.touches[0].clientX; },{passive:true});
  track.addEventListener('touchend',function(e){ var d=tx-e.changedTouches[0].clientX; if(Math.abs(d)>50){ goTo(d>0?cur+1:cur-1); startAuto(); } },{passive:true});
  startAuto();
})();