/**
 * motivation_ticker.js
 * ──────────────────────────────────────────────────────────────
 * Scrolling motivational ticker for Chuka University Timetabling
 * System. Bilingual (English + French), time-of-day aware quotes
 * covering: motivational speeches, motivational words, promises,
 * and time-related wisdom.
 *
 * Usage: auto-initialises on DOMContentLoaded.
 * Requires: #tickerTrack  (div to populate)
 *           #tickerToggle / #tickerToggleIcon  (pause button)
 *           CSS: .motivation-ticker-track (animation defined in website.css)
 * ──────────────────────────────────────────────────────────────
 */

(function () {
  'use strict';

  /* ── 1. Quote bank ─────────────────────────────────────────── */

  const QUOTES = {

    /* Morning (05:00 – 11:59) */
    morning: [
      { lang:'EN', quote: 'The secret of getting ahead is getting started.', author: 'Mark Twain' },
      { lang:'EN', quote: 'Every morning you have two choices: continue to sleep with your dreams, or wake up and chase them.', author: 'Unknown' },
      { lang:'EN', quote: 'Your future is created by what you do today, not tomorrow.', author: 'Robert Kiyosaki' },
      { lang:'EN', quote: 'Rise up, start fresh — see the bright opportunity in each new day.', author: 'Unknown' },
      { lang:'EN', quote: 'Success is not final, failure is not fatal — it is the courage to continue that counts.', author: 'Winston Churchill' },
      { lang:'FR', quote: 'Chaque matin apporte de nouvelles forces et de nouvelles pensées.', author: 'Eleanor Roosevelt' },
      { lang:'FR', quote: 'Le succès c\'est tomber sept fois et se relever huit.', author: 'Proverbe japonais' },
      { lang:'FR', quote: 'La discipline est le pont entre les objectifs et leurs réalisations.', author: 'Jim Rohn' },
      { lang:'EN', quote: 'Today is a new beginning — your timetable is your roadmap to greatness.', author: 'Chuka University' },
      { lang:'FR', quote: 'Commence là où tu es. Utilise ce que tu as. Fais ce que tu peux.', author: 'Arthur Ashe' },
    ],

    /* Midday (12:00 – 13:59) */
    midday: [
      { lang:'EN', quote: 'Keep going. Everything you need will come to you at the perfect time.', author: 'Unknown' },
      { lang:'EN', quote: 'Hard work beats talent when talent doesn\'t work hard.', author: 'Tim Notke' },
      { lang:'EN', quote: 'Believe you can and you\'re halfway there.', author: 'Theodore Roosevelt' },
      { lang:'FR', quote: 'Ne remettez jamais à demain ce que vous pouvez faire aujourd\'hui.', author: 'Benjamin Franklin' },
      { lang:'EN', quote: 'Excellence is not a destination — it is a continuous journey that never ends.', author: 'Brian Tracy' },
      { lang:'FR', quote: 'Celui qui déplace des montagnes commence par enlever de petites pierres.', author: 'Confucius' },
      { lang:'EN', quote: 'Time management is life management. Guard your schedule fiercely.', author: 'Robin Sharma' },
      { lang:'EN', quote: 'You are never too old to set another goal or to dream a new dream.', author: 'C.S. Lewis' },
      { lang:'FR', quote: 'Le talent sans travail n\'est que du potentiel. Mets-le en action.', author: 'Simon Mwangi' },
      { lang:'EN', quote: 'Promise yourself to be so strong that nothing can disturb your peace of mind.', author: 'Christian D. Larson' },
    ],

    /* Afternoon (14:00 – 17:59) */
    afternoon: [
      { lang:'EN', quote: 'Don\'t watch the clock — do what it does, keep going.', author: 'Sam Levenson' },
      { lang:'EN', quote: 'The afternoon knows what the morning never suspected.', author: 'Robert Frost' },
      { lang:'FR', quote: 'Persévérez. La persévérance est tout pour atteindre la gloire.', author: 'Voltaire' },
      { lang:'EN', quote: 'An investment in knowledge pays the best interest.', author: 'Benjamin Franklin' },
      { lang:'EN', quote: 'Education is the most powerful weapon you can use to change the world.', author: 'Nelson Mandela' },
      { lang:'FR', quote: 'L\'éducation est l\'arme la plus puissante pour changer le monde.', author: 'Nelson Mandela' },
      { lang:'EN', quote: 'Push yourself, because no one else is going to do it for you.', author: 'Unknown' },
      { lang:'EN', quote: 'Great things never come from comfort zones.', author: 'Neil Strauss' },
      { lang:'FR', quote: 'Chaque heure du travail d\'aujourd\'hui est une semence pour demain.', author: 'Proverbe Africain' },
      { lang:'EN', quote: 'You promised yourself a better future — every lecture, every exam is a step closer.', author: 'Chuka University' },
    ],

    /* Evening (18:00 – 20:59) */
    evening: [
      { lang:'EN', quote: 'The evening of a well-spent day brings its own reward.', author: 'Leonardo da Vinci' },
      { lang:'EN', quote: 'Reflect on the day — every small win matters.', author: 'Unknown' },
      { lang:'FR', quote: 'Celui qui travaille avec ses mains est un travailleur. Celui qui travaille avec ses mains et sa tête est un artisan. Celui qui travaille avec ses mains, sa tête et son cœur est un artiste.', author: 'Saint François d\'Assise' },
      { lang:'EN', quote: 'Success usually comes to those who are too busy looking for it.', author: 'Henry David Thoreau' },
      { lang:'EN', quote: 'Act as if what you do makes a difference. It does.', author: 'William James' },
      { lang:'FR', quote: 'Soyez le changement que vous voulez voir dans le monde.', author: 'Mahatma Gandhi' },
      { lang:'EN', quote: 'Learning is not attained by chance, it must be sought for with ardour.', author: 'Abigail Adams' },
      { lang:'EN', quote: 'Knowledge is power. Guard it, grow it, share it.', author: 'Francis Bacon' },
      { lang:'FR', quote: 'On n\'apprend pas pour l\'école mais pour la vie.', author: 'Sénèque' },
      { lang:'EN', quote: 'Every hour you dedicate today writes your story of tomorrow.', author: 'Chuka University' },
    ],

    /* Night (21:00 – 04:59) */
    night: [
      { lang:'EN', quote: 'The night is always darkest before the dawn. Keep studying.', author: 'Thomas Fuller' },
      { lang:'EN', quote: 'While others sleep, scholars dream of solutions.', author: 'Unknown' },
      { lang:'FR', quote: 'Dormez, mais que votre esprit reste éveillé pour les grandes idées.', author: 'Victor Hugo' },
      { lang:'EN', quote: 'Rest is not idleness — it is preparation for tomorrow\'s excellence.', author: 'John Lubbock' },
      { lang:'EN', quote: 'A rested mind is a prepared mind. Tomorrow, you conquer.', author: 'Chuka University' },
      { lang:'FR', quote: 'La nuit porte conseil. Reposez-vous et agissez demain avec clarté.', author: 'Proverbe Français' },
      { lang:'EN', quote: 'Great achievements require great dedication — and great rest.', author: 'Unknown' },
      { lang:'EN', quote: 'Stars can\'t shine without darkness. Your resilience is your light.', author: 'D.H. Sidebottom' },
      { lang:'FR', quote: 'La patience est amère, mais ses fruits sont doux.', author: 'Jean-Jacques Rousseau' },
      { lang:'EN', quote: 'Every promise you made to yourself begins with one more hour of effort.', author: 'Chuka University' },
    ],

    /* All-day extras — shuffled in regardless of time */
    allday: [
      { lang:'EN', quote: 'Time is the most valuable thing a man can spend.', author: 'Theophrastus' },
      { lang:'EN', quote: 'Lost time is never found again.', author: 'Benjamin Franklin' },
      { lang:'FR', quote: 'Le temps perdu ne se retrouve jamais.', author: 'Benjamin Franklin' },
      { lang:'EN', quote: 'Make each day your masterpiece.', author: 'John Wooden' },
      { lang:'EN', quote: 'Strive not to be a success, but rather to be of value.', author: 'Albert Einstein' },
      { lang:'FR', quote: 'Ne cherchez pas à réussir mais à avoir de la valeur.', author: 'Albert Einstein' },
      { lang:'EN', quote: 'The key to success is to start before you are ready.', author: 'Marie Forleo' },
      { lang:'EN', quote: 'Do something today that your future self will thank you for.', author: 'Sean Patrick Flanery' },
      { lang:'FR', quote: 'Fais aujourd\'hui ce que ton futur moi te remerciera d\'avoir fait.', author: 'Inconnu' },
      { lang:'EN', quote: 'Your timetable is not a cage — it is a launchpad.', author: 'Chuka University' },
      { lang:'EN', quote: 'Discipline is choosing between what you want now and what you want most.', author: 'Abraham Lincoln' },
      { lang:'FR', quote: 'La discipline, c\'est choisir entre ce que tu veux maintenant et ce que tu veux le plus.', author: 'Abraham Lincoln' },
      { lang:'EN', quote: 'Knowledge grows when shared. Collaborate, innovate, excel.', author: 'Chuka University' },
      { lang:'EN', quote: 'The timetable was built to serve you — use every slot with intention.', author: 'Chuka Directorate' },
      { lang:'FR', quote: 'Chaque minute bien utilisée est une victoire sur le temps.', author: 'Inconnu' },
      { lang:'EN', quote: 'I solemnly promise: I will show up. I will work hard. I will not quit.', author: 'Student\'s Pledge' },
      { lang:'FR', quote: 'Je promets de me lever, de travailler dur et de ne jamais abandonner.', author: 'Serment de l\'étudiant' },
      { lang:'EN', quote: 'Your degree is a promise you made to yourself. Keep it.', author: 'Unknown' },
      { lang:'EN', quote: 'Chuka University — shaping minds, building futures, one schedule at a time.', author: 'Chuka University' },
      { lang:'FR', quote: 'L\'Université Chuka — forger des esprits, bâtir des avenirs.', author: 'Université Chuka' },
    ],
  };

  /* ── 2. Time-of-day selector ───────────────────────────────── */

  function getTimeSlot() {
    var h = new Date().getHours();
    if (h >= 5  && h < 12) return 'morning';
    if (h >= 12 && h < 14) return 'midday';
    if (h >= 14 && h < 18) return 'afternoon';
    if (h >= 18 && h < 21) return 'evening';
    return 'night';
  }

  /* ── 3. Build combined, shuffled quote list ─────────────────── */

  function shuffle(arr) {
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function buildQuoteList() {
    var slot   = getTimeSlot();
    var themed = shuffle(QUOTES[slot] || []);
    var extra  = shuffle(QUOTES.allday);
    /* Interleave: themed, extra, themed, extra … */
    var merged = [];
    var max = Math.max(themed.length, extra.length);
    for (var i = 0; i < max; i++) {
      if (i < themed.length) merged.push(themed[i]);
      if (i < extra.length)  merged.push(extra[i]);
    }
    return merged;
  }

  /* ── 4. Render ticker items ─────────────────────────────────── */

  function buildItemHTML(q) {
    var langBadge = q.lang === 'FR'
      ? '<span style="background:rgba(255,255,255,.15);border-radius:3px;padding:1px 5px;font-size:.68rem;font-style:normal;font-weight:700;letter-spacing:.05em;margin-right:4px;">FR</span>'
      : '';
    return '<span class="ticker-item">' +
           langBadge +
           '<span class="ticker-quote">' + escHtml(q.quote) + '</span>' +
           '<span class="ticker-author">— ' + escHtml(q.author) + '</span>' +
           '<span class="ticker-sep" aria-hidden="true">✦</span>' +
           '</span>';
  }

  function escHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* ── 5. Init ─────────────────────────────────────────────────── */

  function init() {
    var track  = document.getElementById('tickerTrack');
    var btn    = document.getElementById('tickerToggle');
    var btnIco = document.getElementById('tickerToggleIcon');
    if (!track) return;

    var quotes = buildQuoteList();
    /* Need enough content width for seamless loop — duplicate once */
    var html = quotes.map(buildItemHTML).join('');
    /* Double it: CSS animation moves by -50% for a seamless infinite loop */
    track.innerHTML = html + html;

    /* Pause / resume */
    if (btn && btnIco) {
      var paused = false;
      btn.addEventListener('click', function () {
        paused = !paused;
        track.style.animationPlayState = paused ? 'paused' : 'running';
        btnIco.className = paused ? 'fas fa-play' : 'fas fa-pause';
        btn.setAttribute('aria-label', paused ? 'Resume motivational ticker' : 'Pause motivational ticker');
      });
    }

    /* Accessibility: reduce motion */
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      track.style.animation = 'none';
      /* Show just the first 3 quotes statically */
      track.innerHTML = quotes.slice(0, 3).map(buildItemHTML).join('');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
