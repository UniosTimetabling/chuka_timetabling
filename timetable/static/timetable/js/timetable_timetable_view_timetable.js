/*
 * timetable_timetable_view_timetable.js
 */

document.addEventListener('DOMContentLoaded', function () {

  var el = function (id) { return document.getElementById(id); };

  var currentProgram = null;
  var currentType    = 'main';
  var currentYear    = null;
  var lastData       = null;

  // Inline style show/hide — beats any stylesheet
  function show(id, displayVal) { var e = el(id); if (e) e.style.display = displayVal || 'block'; }
  function hide(id)              { var e = el(id); if (e) e.style.display = 'none'; }

  // Enforce initial state
  hide('viewer-controls');
  hide('print-actions');
  show('form-container');

  // "See Timetable" button
  el('see-btn').addEventListener('click', function () {
    var year = el('student-year').value;
    var prog = el('program-select').value;
    if (!prog || !year) { alert('Please select both program and year'); return; }
    currentProgram = prog;
    currentYear    = year;
    currentType    = 'main';
    loadTimetable();
  });

  // Tab buttons
  el('minimal-btn').onclick  = function () { currentType = 'minimal';  switchBtns(); loadTimetable(); };
  el('main-btn').onclick     = function () { currentType = 'main';     switchBtns(); loadTimetable(); };
  el('exam-btn').onclick     = function () { currentType = 'exam';     switchBtns(); loadTimetable(); };
  el('lab-btn').onclick      = function () { currentType = 'lab';      switchBtns(); loadTimetable(); };
  el('lab-exam-btn').onclick = function () { currentType = 'lab_exam'; switchBtns(); loadTimetable(); };
  el('back-btn').onclick     = function () { location.reload(); };
  el('clear-btn').onclick    = function () { location.reload(); };

  function switchBtns() {
    var map = { minimal:'minimal-btn', main:'main-btn', exam:'exam-btn', lab:'lab-btn', lab_exam:'lab-exam-btn' };
    Object.keys(map).forEach(function (type) {
      var btn = el(map[type]);
      if (btn) btn.classList.toggle('btn-active', currentType === type);
    });
  }

  function loadTimetable() {
    if (!currentProgram || !currentYear) return;
    fetch('/timetable/' + currentProgram + '/' + currentType + '/?year=' + currentYear)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        lastData = d;
        renderGrid(d);
        show('viewer-controls');
        hide('form-container');
        show('print-actions', 'flex');
        el('program-summary').innerHTML =
          'Viewing <b>' + (d.timetable_type || currentType).toUpperCase() +
          '</b> timetable for <b>' + (d.program_name || '') + '</b> \u2014 Year ' + currentYear;
        switchBtns();
      })
      .catch(function (err) {
        console.error('Error loading timetable:', err);
        alert('Error loading timetable. Please try again.');
      });
  }

  function renderGrid(resp) {
    var c = el('timetable-container');
    c.innerHTML = '';
    if (!resp.has_entries) {
      c.innerHTML = '<div class="notice">No timetable entries found for the selected criteria.</div>';
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
      var venues = [], times = [];
      dayEntries.forEach(function (e) {
        if (venues.indexOf(e.venue) === -1) venues.push(e.venue);
        var t = e.start + '-' + e.end;
        if (times.indexOf(t) === -1) times.push(t);
      });
      times.sort();
      html += '<h3>' + day + '</h3><div class="table-container"><table><thead><tr><th>Venue \\ Time</th>';
      times.forEach(function (t) { html += '<th>' + t + '</th>'; });
      html += '</tr></thead><tbody>';
      venues.forEach(function (v) {
        html += '<tr><td>' + v + '</td>';
        times.forEach(function (t) {
          var e = null;
          for (var i = 0; i < dayEntries.length; i++) {
            if (dayEntries[i].venue === v && (dayEntries[i].start + '-' + dayEntries[i].end) === t) { e = dayEntries[i]; break; }
          }
          html += e ? '<td>' + e.course_code + '<br>' + e.course_name + '<br>' + e.lecturer + '</td>' : '<td>-</td>';
        });
        html += '</tr>';
      });
      html += '</tbody></table></div>';
    });
    c.innerHTML = html;
  }

  el('download-img-btn').addEventListener('click', function () {
    if (!lastData) { alert('Please load a timetable first'); return; }
    var btn = el('download-img-btn'), orig = btn.innerHTML;
    btn.innerHTML = '<span>\u23F3</span> Generating...'; btn.disabled = true;
    html2canvas(el('timetable-container'), { backgroundColor: '#0e1525', scale: 2, useCORS: true, scrollY: -window.scrollY })
      .then(function (canvas) {
        var a = document.createElement('a');
        a.download = (lastData.program_name || 'timetable') + '_' + currentType + '.png';
        a.href = canvas.toDataURL(); a.click();
        btn.innerHTML = orig; btn.disabled = false;
      }).catch(function (err) {
        console.error(err); alert('Error generating image.');
        btn.innerHTML = orig; btn.disabled = false;
      });
  });

  el('print-btn').addEventListener('click', function () { window.print(); });

}); // end DOMContentLoaded