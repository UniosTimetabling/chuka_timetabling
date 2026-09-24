  const csrftoken = document.querySelector('[name=csrfmiddlewaretoken]').value;
  let currentMode          = 'class';
  let currentSchedulerMode = 'balanced';
  let isScheduling         = false;
  let progressInterval;

  // URLs are injected by the template (campus/auto_scheduler.html) into
  // window.CAMPUS_AUTO_URLS, because {% url %} tags are never rendered
  // inside a static .js file — Django only processes template tags in
  // templates, not in files served via {% static %}. Using {% url %}
  // directly in this file made the browser fetch the literal, URL-encoded
  // text "{% url '...' %}" and get a 404 HTML page back instead of JSON.
  const urls = window.CAMPUS_AUTO_URLS;

  /* ── INIT ── */
  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('configForm').addEventListener('submit', updateConfig);
    initTabs();
    loadSavedState();
    setInterval(loadStatus, 30000);
  });

  /* ── TABS ── */
  function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(`${btn.dataset.tab}-tab`).classList.add('active');
      });
    });
  }

  /* ── STATE ── */
  function loadSavedState() {
    const m = localStorage.getItem('campus_scheduler_mode');
    if (m === 'class' || m === 'exam') switchMode(m);
    const t = localStorage.getItem('campus_scheduler_type');
    if (t) { currentSchedulerMode = t; updateModeButtons(); syncInlineSelect(); }
  }
  function saveState() {
    localStorage.setItem('campus_scheduler_mode', currentMode);
    localStorage.setItem('campus_scheduler_type', currentSchedulerMode);
  }

  /* ── MODE SWITCH (class / exam) ── */
  window.switchMode = function (mode) {
    currentMode = mode;
    document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('.mode-toggle-btn.' + mode + '-btn').classList.add('active');

    const isClass = mode === 'class';
    document.getElementById('classModeSelector').style.display = isClass ? 'flex' : 'none';
    document.getElementById('examModeSelector').style.display  = isClass ? 'none' : 'flex';

    document.getElementById('runBtnText').textContent   = isClass ? 'Run Class Scheduler'    : 'Run Exam Scheduler';
    document.getElementById('clearBtnText').textContent = isClass ? 'Clear Class Drafts'     : 'Clear Exam Drafts';
    const pb = document.getElementById('publishBtnText');
    if (pb) pb.textContent = isClass ? 'Publish Class Timetable' : 'Publish Exam Timetable';

    document.getElementById('statusInfoText').textContent = isClass
      ? 'Currently in Class Scheduling Mode'
      : 'Currently in Exam Scheduling Mode';

    /* switch to the matching tab */
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const matchTab = document.querySelector(`.tab-btn[data-tab="${mode}"]`);
    if (matchTab) matchTab.classList.add('active');
    const matchContent = document.getElementById(`${mode}-tab`);
    if (matchContent) matchContent.classList.add('active');

    updateModeButtons();
    saveState();
  };

  /* ── DISTRIBUTION MODE (balanced / compact / spread) ── */
  function updateModeButtons() {
    const sel = currentMode === 'class' ? '#classModeSelector' : '#examModeSelector';
    document.querySelectorAll(`${sel} .mode-seg-btn`).forEach(b => b.classList.remove('active'));
    document.querySelector(`${sel} .mode-seg-btn[data-mode="${currentSchedulerMode}"]`)?.classList.add('active');
    syncInlineSelect();
  }
  window.selectMode = function (mode) { currentSchedulerMode = mode; updateModeButtons(); saveState(); };
  window.selectModeFromSelect = function (mode) { currentSchedulerMode = mode; updateModeButtons(); saveState(); };
  function syncInlineSelect() {
    const sel = document.getElementById('modeSelectInline');
    if (sel) sel.value = currentSchedulerMode;
  }

  /* ── CONFIG SAVE ── */
  async function updateConfig(e) {
    e.preventDefault();
    const payload = {
      start_date:          document.getElementById('startDate').value,
      end_date:            document.getElementById('endDate').value,
      day_start_time:      document.getElementById('dayStartTime').value,
      day_end_time:        document.getElementById('dayEndTime').value,
      class_slot_size:     document.getElementById('classSlotSize').value,
      exam_slot_size:      document.getElementById('examSlotSize').value,
      exam_break_duration: document.getElementById('examBreakDuration').value
    };
    showMsg('Writing config parameters…', 'info');
    try {
      const r = await fetch(urls.updateConfig, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
        body: JSON.stringify(payload)
      });
      const d = await r.json();
      showMsg(d.success ? d.message : (d.error || 'Error saving'), d.success ? 'success' : 'error');
    } catch (err) { showMsg('Network error', 'error'); }
  }

  /* ── RUN SCHEDULER ── */
  window.runScheduler = function () {
    if (!confirm(`Replace all existing ${currentMode} drafts and re-run the scheduler. Continue?`)) return;

    isScheduling = true;
    document.getElementById('modalTitle').textContent = `${currentMode === 'class' ? 'Class' : 'Exam'} Scheduling Processing Engine`;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressBar').textContent = '0%';
    document.getElementById('progressBar').className   = 'prog-fill stage-yellow';
    document.getElementById('schedulerProgressModal').classList.add('show');
    document.getElementById('consoleOutput').innerHTML = '<div>[SYSTEM] Starting scheduler…</div>';

    clearInterval(progressInterval);

    fetch(currentMode === 'class' ? urls.runClass : urls.runExam, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
      body: JSON.stringify({ mode: currentSchedulerMode })
    })
    .then(r => r.json())
    .then(d => {
      if (d.task_id) {
        /* If backend returns a task ID, poll for progress */
        progressInterval = setInterval(() => pollProgress(d.task_id), 1000);
      } else {
        /* Synchronous response */
        completeScheduling(d);
      }
    })
    .catch(e => completeScheduling({ success: false, error: e.message }));
  };

  function pollProgress(taskId) {
    if (!isScheduling) return;
    fetch(urls.status + '?task_id=' + taskId)
      .then(r => r.json())
      .then(d => {
        const pct    = typeof d.progress === 'number' ? d.progress : 0;
        const status = d.status || '';

        if (['completed', 'partial', 'error', 'cancelled'].includes(status) || pct >= 100) {
          completeScheduling(d);
          return;
        }

        const bar = document.getElementById('progressBar');
        bar.style.width = pct + '%';
        bar.textContent = pct + '%';
        document.getElementById('progressPercentage').textContent = pct + '%';

        if (pct < 35) {
          bar.className = 'prog-fill stage-yellow';
          document.getElementById('batchInfo').textContent = 'Phase 1: Analyzing rules & resolving allocation slots…';
        } else if (pct <= 85) {
          bar.className = 'prog-fill stage-blue';
          document.getElementById('batchInfo').textContent = 'Phase 2: Executing bulk allocation & processing course matrices…';
        } else {
          bar.className = 'prog-fill stage-green';
          document.getElementById('batchInfo').textContent = 'Phase 3: Final optimization & conflict checks…';
        }

        document.getElementById('scheduledCount').textContent = d.scheduled_count || 0;
        document.getElementById('remainingCount').textContent = d.remaining_count || 0;
        document.getElementById('currentBatch').textContent   = `${d.current_batch || 0}/${d.total_batches || 0}`;
        document.getElementById('currentAction').textContent  = d.current_action  || 'Processing…';

        if (d.log_entries) {
          const box = document.getElementById('consoleOutput');
          box.innerHTML = d.log_entries.slice(-10).map(l => {
            const text = (l && typeof l === 'object') ? (l.message || JSON.stringify(l)) : String(l);
            return `<div>${text}</div>`;
          }).join('');
          box.scrollTop = box.scrollHeight;
        }
      })
      .catch(() => {});
  }

  function completeScheduling(data) {
    clearInterval(progressInterval);
    isScheduling = false;

    const bar = document.getElementById('progressBar');
    bar.style.width = '100%';
    bar.textContent = '100%';
    bar.className   = 'prog-fill stage-green';
    document.getElementById('progressPercentage').textContent = '100%';
    document.getElementById('batchInfo').textContent = 'Execution Sequence Terminated.';
    document.getElementById('currentAction').textContent = data.message || data.error || 'Done';

    setTimeout(() => {
      document.getElementById('schedulerProgressModal').classList.remove('show');
      if (data.success) {
        showResult(data.message, 'success');
        if (data.conflict_list?.length) {
          const cDiv = document.getElementById('conflicts');
          cDiv.style.display = 'block';
          cDiv.innerHTML = `<strong>Could not schedule:</strong><div style="margin-top:6px">` +
            data.conflict_list.map(c => `<div class="unsched-item"><i class="fas fa-times me-1"></i>${c}</div>`).join('') +
            '</div>';
        }
        setTimeout(() => location.reload(), 1800);
      } else {
        showResult(data.error || 'Scheduler error.', 'error');
      }
    }, 1800);
  }

  function cancelScheduling() {
    if (!confirm('Break processing workflow execution?')) return;
    isScheduling = false;
    clearInterval(progressInterval);
    completeScheduling({ success: false, error: 'Engine halted by operator.' });
  }

  /* ── PUBLISH ── */
  window.publishTimetable = function () {
    if (!confirm(`Publish ${currentMode} timetable? Existing approved entries will be replaced.`)) return;
    const overlay = document.getElementById('pubOverlay');
    const spinner = document.getElementById('pubSpinner');
    const check   = document.getElementById('pubCheck');
    const title   = document.getElementById('pubTitle');
    const msg     = document.getElementById('pubMsg');

    spinner.style.display = '';
    check.style.display   = 'none';
    title.textContent     = `Publishing ${currentMode} timetable…`;
    msg.textContent       = 'Moving drafts to approved records…';
    overlay.classList.add('show');

    fetch(currentMode === 'class' ? urls.publishClass : urls.publishExam, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken }
    })
    .then(r => r.json())
    .then(d => {
      if (d.success) {
        spinner.style.display = 'none';
        check.style.display   = '';
        title.textContent     = 'Published Successfully!';
        setTimeout(() => { overlay.classList.remove('show'); location.reload(); }, 1200);
      } else { throw new Error(d.error || 'Error'); }
    })
    .catch(err => {
      spinner.style.display = 'none';
      title.textContent     = 'Publish Error';
      msg.textContent       = err.message;
      setTimeout(() => overlay.classList.remove('show'), 3500);
    });
  };

  /* ── CLEAR DRAFTS ── */
  window.clearDrafts = function () {
    if (!confirm(`Clear all ${currentMode} drafts?`)) return;
    fetch(urls.clearDrafts, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
      body: JSON.stringify({ target: currentMode })
    })
    .then(r => r.json())
    .then(d => {
      if (d.success) { showResult(d.message, 'success'); setTimeout(() => location.reload(), 1500); }
      else showResult(d.error || 'Error', 'error');
    })
    .catch(() => showResult('Network error', 'error'));
  };

  /* ── STATUS POLL ── */
  window.loadStatus = function () {
    fetch(urls.status).then(r => r.json()).catch(() => {});
  };

  /* ── UI HELPERS ── */
  function showResult(message, type) {
    const el = document.getElementById('result');
    el.textContent = message;
    el.className   = `result-banner ${type}`;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 5000);
  }

  function showMsg(text, type) {
    const el = document.getElementById('configMessage');
    el.textContent     = `[${type.toUpperCase()}] ${text}`;
    el.style.display   = 'inline-block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
  }
