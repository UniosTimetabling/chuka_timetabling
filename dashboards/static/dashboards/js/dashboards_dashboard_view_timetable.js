/*
 * dashboards_dashboard_view_timetable.js
 */

document.addEventListener('DOMContentLoaded', function () {

  const el = id => document.getElementById(id);

  let currentProgram = null;
  let currentType    = 'main';
  let currentYear    = null;
  let lastData       = null;

  // ── Show / hide helpers — inline style beats every stylesheet ──────────────
  function show(id, displayVal) {
    var e = el(id); if (e) e.style.display = displayVal || 'block';
  }
  function hide(id) {
    var e = el(id); if (e) e.style.display = 'none';
  }

  // ── Enforce correct initial state immediately ──────────────────────────────
  hide('viewer-controls');
  hide('print-actions');
  show('form-container');

  // ── "View Timetable" button ────────────────────────────────────────────────
  el('see-btn').addEventListener('click', function () {
    var year = el('student-year').value;
    var prog = el('program-select').value;
    if (!prog || !year) { alert('Please select both programme and year'); return; }
    currentProgram = prog;
    currentYear    = year;
    currentType    = 'main';
    loadTimetable();
  });

  // ── Tab buttons ────────────────────────────────────────────────────────────
  el('minimal-btn').onclick  = function () { currentType = 'minimal';  switchBtns(); loadTimetable(); };
  el('main-btn').onclick     = function () { currentType = 'main';     switchBtns(); loadTimetable(); };
  el('exam-btn').onclick     = function () { currentType = 'exam';     switchBtns(); loadTimetable(); };
  el('lab-btn').onclick      = function () { currentType = 'lab';      switchBtns(); loadTimetable(); };
  el('lab-exam-btn').onclick = function () { currentType = 'lab_exam'; switchBtns(); loadTimetable(); };

  // ── Back / Clear ───────────────────────────────────────────────────────────
  el('back-btn').onclick  = function () { location.reload(); };
  el('clear-btn').onclick = function () { location.reload(); };

  // ── Highlight active tab ───────────────────────────────────────────────────
  function switchBtns() {
    var map = { 'minimal':'minimal-btn','main':'main-btn','exam':'exam-btn','lab':'lab-btn','lab_exam':'lab-exam-btn' };
    Object.keys(map).forEach(function (type) {
      var btn = el(map[type]);
      if (btn) btn.classList.toggle('btn-active', currentType === type);
    });
  }

  // ── Fetch and render ───────────────────────────────────────────────────────
  function loadTimetable() {
    if (!currentProgram || !currentYear) return;
    var btn      = el('see-btn');
    var origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading\u2026';
    btn.disabled  = true;

    fetch('/timetable/' + currentProgram + '/' + currentType + '/?year=' + currentYear)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        lastData = d;
        renderGrid(d);
        // Show controls and print bar; hide selection form
        show('viewer-controls');
        hide('form-container');
        show('print-actions', 'flex');
        var pn = d.program_name || 'Unknown Programme';
        var tt = d.timetable_type || currentType;
        el('program-summary').innerHTML =
          '<i class="fas fa-info-circle"></i> Viewing <strong>' + tt.toUpperCase() +
          '</strong> timetable for <strong>' + pn + '</strong> \u2014 Year ' + currentYear;
        switchBtns();
        btn.innerHTML = origHtml;
        btn.disabled  = false;
      })
      .catch(function (err) {
        console.error(err);
        alert('Error loading timetable. Please try again.');
        btn.innerHTML = origHtml;
        btn.disabled  = false;
      });
  }

  // ── Render grid ────────────────────────────────────────────────────────────
  function renderGrid(resp) {
    var c = el('timetable-container');
    c.innerHTML = '';
    if (!resp.has_entries) {
      c.innerHTML = '<div class="tv-empty"><i class="fas fa-calendar-times"></i>' +
        'No timetable entries found for the selected criteria.' +
        '<br><small>Try selecting a different year or timetable type.</small></div>';
      return;
    }
    var isDateBased = (currentType === 'exam' || currentType === 'lab_exam');
    var dayOrder    = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    var keys = Object.keys(resp.data).sort(function (a, b) {
      if (isDateBased) return new Date(a.split(' ')[0]) - new Date(b.split(' ')[0]);
      return dayOrder.indexOf(a) - dayOrder.indexOf(b);
    });

    var html = '';
    keys.forEach(function (day) {
      var dayEntries = resp.data[day];
      if (!dayEntries || dayEntries.length === 0) return;
      var venues = [];
      dayEntries.forEach(function (e) { if (venues.indexOf(e.venue) === -1) venues.push(e.venue); });
      var times = [];
      dayEntries.forEach(function (e) { var t = e.start + '\u2013' + e.end; if (times.indexOf(t) === -1) times.push(t); });
      times.sort();

      html += '<div class="tv-day-head"><i class="fas fa-calendar-day" style="color:var(--cu-gold);margin-right:10px;"></i>' + day + '</div>';
      html += '<div class="tv-table-wrap"><table><thead><tr><th>Venue / Time</th>';
      times.forEach(function (t) { html += '<th>' + t + '</th>'; });
      html += '</tr></thead><tbody>';
      venues.forEach(function (v) {
        html += '<tr><td>' + v + '</td>';
        times.forEach(function (t) {
          var e = null;
          for (var i = 0; i < dayEntries.length; i++) {
            if (dayEntries[i].venue === v && (dayEntries[i].start + '\u2013' + dayEntries[i].end) === t) { e = dayEntries[i]; break; }
          }
          html += e
            ? '<td><strong>' + e.course_code + '</strong><br>' +
              '<span style="font-size:.85rem;color:var(--cu-text);">' + e.course_name + '</span><br>' +
              '<span style="font-size:.82rem;color:var(--cu-green);">' + e.lecturer + '</span></td>'
            : '<td>-</td>';
        });
        html += '</tr>';
      });
      html += '</tbody></table></div>';
    });
    c.innerHTML = html;
  }

  // ── Save image ─────────────────────────────────────────────────────────────
  el('download-img-btn').addEventListener('click', function () {
    if (!lastData) { alert('Please load a timetable first'); return; }
    var btn  = el('download-img-btn');
    var orig = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating\u2026';
    btn.disabled  = true;
    var pn = lastData.program_name || 'Timetable';
    var tt = lastData.timetable_type || currentType;
    html2canvas(el('timetable-container'), {
      backgroundColor: '#ffffff', scale: 2, useCORS: true, scrollY: -window.scrollY
    }).then(function (canvas) {
      var a = document.createElement('a');
      a.download = pn.replace(/\s+/g, '_') + '_' + tt + '.png';
      a.href = canvas.toDataURL();
      a.click();
      btn.innerHTML = orig; btn.disabled = false;
    }).catch(function (err) {
      console.error(err); alert('Error generating image.');
      btn.innerHTML = orig; btn.disabled = false;
    });
  });

  // ── Print ──────────────────────────────────────────────────────────────────
  el('print-btn').addEventListener('click', function () { window.print(); });

  // ── Bot label fade ─────────────────────────────────────────────────────────
  setTimeout(function () { var l = el('botFabLabel'); if (l) l.style.opacity = '0'; }, 5000);

  // ── Floating bot ───────────────────────────────────────────────────────────
  var botOpen = false;
  window.toggleBot = function () {
    botOpen = !botOpen;
    el('botPanel').classList.toggle('open', botOpen);
    el('botFabLabel').style.display = botOpen ? 'none' : 'block';
    if (botOpen) el('botInput').focus();
  };
  window.botQuick = function (msg) { addBotUserMsg(msg); botRespond(msg); };
  window.sendBot  = function () {
    var inp = el('botInput'), msg = inp.value.trim();
    if (!msg) return; inp.value = ''; addBotUserMsg(msg); botRespond(msg);
  };
  function addBotUserMsg(msg) {
    var body = el('botPanelBody'), d = document.createElement('div');
    d.className = 'bp-msg user'; d.textContent = msg;
    body.appendChild(d); body.scrollTop = body.scrollHeight;
  }
  function botRespond(msg) {
    var body = el('botPanelBody');
    var typing = document.createElement('div'); typing.className = 'bp-msg bot';
    typing.innerHTML = '<div class="tdots"><div class="tdot"></div><div class="tdot"></div><div class="tdot"></div></div>';
    body.appendChild(typing); body.scrollTop = body.scrollHeight;
    var m = msg.toLowerCase(), reply = '';
    if (m.includes('how to use') || m.includes('how do i view')) {
      reply = 'Select your <strong>Academic Year</strong> and <strong>Programme</strong>, then click <strong>View Timetable</strong>. Use the tab buttons to switch between Class Rep, Official, Exam, Lab, and Lab Exam timetables.';
    } else if (m.includes('lab exam')) {
      reply = 'Click <strong>Lab Exam Timetable</strong> after loading your programme.';
    } else if (m.includes('lab')) {
      reply = 'Click <strong>Lab Timetable</strong> after loading your programme to see scheduled lab sessions.';
    } else if (m.includes('exam')) {
      reply = 'Click <strong>Exam Timetable</strong> after loading your timetable. Download exam PDFs from the <a href="/portal/">Student Portal</a>.';
    } else if (m.includes('download') || m.includes('save') || m.includes('image')) {
      reply = 'After loading your timetable, click <strong>Save Image</strong> (bottom right) or <strong>Print</strong>.';
    } else if (m.includes('clash') || m.includes('report')) {
      reply = 'Visit the <a href="/feedback/">Feedback page</a> to report a timetable clash.';
    } else if (m.includes('class rep')) {
      reply = 'Class Rep timetables are submitted by your class representative. Select <strong>Class Rep Timetable</strong> after loading your programme.';
    } else {
      reply = 'Try the <a href="/feedback/">Feedback page</a> or the <a href="/portal/">Student Portal</a> for more help.';
    }
    setTimeout(function () { typing.innerHTML = reply; body.scrollTop = body.scrollHeight; }, 900);
  }

  // ── Public alias ───────────────────────────────────────────────────────────
  window.fetchTimetable = function (programId, year, type) {
    currentProgram = programId; currentYear = year; currentType = type;
    switchBtns(); loadTimetable();
  };

}); // end DOMContentLoaded