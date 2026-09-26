document.addEventListener("DOMContentLoaded", function () {

  /* ═══════════════════════════════════════════════════════════
     CONSTANTS & CSRF
  ═══════════════════════════════════════════════════════════ */
  const csrftoken       = document.cookie.split('; ').find(r => r.startsWith('csrftoken='))?.split('=')[1];
  // URLs are injected by the template (campus/panel.html) into
  // window.CAMPUS_PANEL_URLS, because {% url %} tags are never rendered
  // inside a static .js file.
  const apiUrl          = window.CAMPUS_PANEL_URLS.apiUrl;
  const courseCodesUrl  = window.CAMPUS_PANEL_URLS.courseCodesUrl;
  const getCourseNameUrl= window.CAMPUS_PANEL_URLS.getCourseNameUrl;
  const toggleDvcUrl    = window.CAMPUS_PANEL_URLS.toggleDvcUrl;
  const toggleTtUrl     = window.CAMPUS_PANEL_URLS.toggleTtUrl;

  /* ═══════════════════════════════════════════════════════════
     TOAST
  ═══════════════════════════════════════════════════════════ */
  function toast(msg, type='success', duration=3500) {
    const icons = { success:'✅', error:'❌', warning:'⚠️', info:'ℹ️' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span>${icons[type]||''}</span><span>${msg}</span>`;
    document.getElementById('toast-container').appendChild(el);
    setTimeout(() => el.remove(), duration);
  }

  /* ═══════════════════════════════════════════════════════════
     OFFLINE / ONLINE DETECTION
  ═══════════════════════════════════════════════════════════ */
  const offlineBanner = document.getElementById('offline-banner');
  const queueBadge    = document.getElementById('queue-count-badge');

  function updateOnlineUI() {
    const online = navigator.onLine;
    offlineBanner.classList.toggle('visible', !online);
    if (online) { toast('Back online — syncing queued changes…', 'info'); flushQueue(); }
  }
  window.addEventListener('online',  updateOnlineUI);
  window.addEventListener('offline', updateOnlineUI);

  /* ═══════════════════════════════════════════════════════════
     OFFLINE QUEUE  (IndexedDB)
  ═══════════════════════════════════════════════════════════ */
  let DB = null;
  const DB_NAME = 'cod_panel_queue';
  const STORE   = 'pending_ops';

  function openDB() {
    return new Promise((resolve, reject) => {
      if (DB) return resolve(DB);
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(STORE))
          db.createObjectStore(STORE, { keyPath: 'localId', autoIncrement: true });
      };
      req.onsuccess = e => { DB = e.target.result; resolve(DB); };
      req.onerror   = e => reject(e.target.error);
    });
  }

  async function queueOp(payload) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).add({ payload, queuedAt: Date.now() });
      tx.oncomplete = resolve;
      tx.onerror    = e => reject(e.target.error);
    });
  }

  async function getPendingOps() {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = e => resolve(e.target.result);
      req.onerror   = e => reject(e.target.error);
    });
  }

  async function removeOp(localId) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(localId);
      tx.oncomplete = resolve;
      tx.onerror    = e => reject(e.target.error);
    });
  }

  async function updateQueueBadge() {
    const ops = await getPendingOps().catch(() => []);
    const n = ops.length;
    if (n > 0) {
      queueBadge.textContent = ` (${n} pending)`;
      offlineBanner.classList.add('visible');
    } else {
      queueBadge.textContent = '';
      if (navigator.onLine) offlineBanner.classList.remove('visible');
    }
  }

  /* Flush: replay queued ops when back online */
  async function flushQueue() {
    const ops = await getPendingOps().catch(() => []);
    if (!ops.length) return;
    let synced = 0;
    for (const op of ops) {
      try {
        const r = await apiPost(op.payload, /* skipQueue= */ true);
        if (r.status === 'success') {
          await removeOp(op.localId);
          synced++;
          // If the queued op was a create, we can't easily update the temp row
          // so we do a single targeted refresh of the table after all ops flush
        } else {
          console.warn('Sync failed for op', op.localId, r.message);
        }
      } catch (e) {
        console.warn('Sync network error', e);
        break; // still offline, stop trying
      }
    }
    await updateQueueBadge();
    if (synced > 0) {
      toast(`${synced} queued change(s) synced successfully.`, 'success', 4000);
      await refreshAllocationsTable();
    }
  }

  /* ═══════════════════════════════════════════════════════════
     OFFLINE FORM VALIDATION
     Run before queuing so bad data doesn't pollute the queue.
  ═══════════════════════════════════════════════════════════ */
  function validateFormData(fd) {
    const errors = [];
    const code = fd.get('course_code')?.trim();
    const name = fd.get('course_name')?.trim();
    const originDept = fd.get('origin_department_id');
    const program = fd.get('program_id');
    const campus = fd.get('campus_id');
    const students = parseInt(fd.get('number_of_students'), 10);
    const acYear = fd.get('academic_year')?.trim();

    if (!code) errors.push('Course code is required.');

    if (!name) errors.push('Course name is required.');
    if (!originDept) errors.push('Origin department is required.');
    if (!program) errors.push('Program is required.');
    if (!campus) errors.push('Campus is required.');
    if (isNaN(students) || students < 0)
      errors.push('Number of students must be 0 or more.');
    if (acYear && !/^\d{4}\/\d{4}$/.test(acYear))
      errors.push(`Academic year "${acYear}" must be in format 2024/2025.`);

    return errors;
  }

  /* ═══════════════════════════════════════════════════════════
     CORE API HELPER
  ═══════════════════════════════════════════════════════════ */
  async function apiPost(body, skipQueue=false) {
    if (!navigator.onLine && !skipQueue) throw new Error('offline');
    const res = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrftoken },
      body
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /* ═══════════════════════════════════════════════════════════
     DOM HELPERS — build & insert/update table rows
  ═══════════════════════════════════════════════════════════ */

  // Maps for label lookups (populated from <select> options at boot)
  const labelOf = {};
  ['originDepartmentSelect','programSelect','lecturerSelect','campusSelect','teachingCampusSelect','deliveryMode']
    .forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      labelOf[id] = {};
      Array.from(sel.options).forEach(o => { labelOf[id][o.value] = o.text; });
    });

  function makeRowHTML(a) {
    /* a = {id, course_code, course_name, program_id, program_label,
            origin_department_id, origin_dept_label,
            lecturer_id, lecturer_label, campus_id, campus_label,
            number_of_students, delivery_mode} */
    const progLabel   = a.program_label   || labelOf['programSelect']?.[a.program_id]    || '-';
    const originLabel = a.origin_dept_label|| labelOf['originDepartmentSelect']?.[a.origin_department_id] || '-';
    const lecLabel    = a.lecturer_label  || labelOf['lecturerSelect']?.[a.lecturer_id]  || '<em>Unassigned</em>';
    const campusLabel = a.campus_label    || labelOf['campusSelect']?.[a.campus_id]       || '-';
    const campusBadge = campusLabel !== '-'
      ? `<span class="campus-badge">${campusLabel.split('(')[1]?.replace(')','') || campusLabel}</span>`
      : '<em>-</em>';

    return `
      <td><strong>${escHtml(a.course_code)}</strong></td>
      <td>${escHtml(a.course_name)}</td>
      <td>${escHtml(progLabel)}</td>
      <td>${escHtml(originLabel)}</td>
      <td>${lecLabel === '<em>Unassigned</em>' ? lecLabel : escHtml(lecLabel)}</td>
      <td>${campusBadge}</td>
      <td style="text-align:center;font-weight:700;color:#004d26;">${a.number_of_students||0}</td>
      <td>
        <button class="btn-action btn-edit"   onclick="editRow(${a.id})">&#9998; Edit</button>
        <button class="btn-action btn-delete" onclick="deleteRow(${a.id})">&#128465; Delete</button>
      </td>`;
  }

  function escHtml(s) {
    if (s == null) return '-';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function upsertRow(a) {
    const tbody = document.getElementById('allocationsTable');
    let tr = document.getElementById('alloc-' + a.id);
    if (tr) {
      tr.innerHTML = makeRowHTML(a);
    } else {
      tr = document.createElement('tr');
      tr.id = 'alloc-' + a.id;
      tr.innerHTML = makeRowHTML(a);
      // Insert sorted by course_code
      const rows = Array.from(tbody.querySelectorAll('tr[id^="alloc-"]'));
      const after = rows.find(r => r.cells[0]?.textContent.trim() > a.course_code);
      if (after) tbody.insertBefore(tr, after);
      else tbody.appendChild(tr);
    }
    // Remove the "no allocations" placeholder if present
    const placeholder = tbody.querySelector('tr td[colspan]');
    if (placeholder) placeholder.closest('tr').remove();
    // Flash
    tr.classList.remove('row-flash');
    void tr.offsetWidth; // force reflow
    tr.classList.add('row-flash');
    setTimeout(() => tr.classList.remove('row-flash'), 1500);
  }

  function removeRow(id) {
    document.getElementById('alloc-' + id)?.remove();
    // If table is now empty, show placeholder
    const tbody = document.getElementById('allocationsTable');
    if (!tbody.querySelector('tr[id^="alloc-"]')) {
      tbody.innerHTML = "<tr><td colspan='8' style='text-align:center;padding:30px;color:#aaa;'>No allocations yet.</td></tr>";
    }
  }

  function insertTempRow(tempId, fd) {
    const a = {
      id: tempId,
      course_code: fd.get('course_code'),
      course_name: fd.get('course_name'),
      program_id: fd.get('program_id'),
      origin_department_id: fd.get('origin_department_id'),
      lecturer_id: fd.get('lecturer_id'),
      campus_id: fd.get('campus_id'),
      number_of_students: fd.get('number_of_students'),
    };
    const tbody = document.getElementById('allocationsTable');
    let tr = document.getElementById('alloc-' + tempId);
    if (!tr) { tr = document.createElement('tr'); tr.id = 'alloc-' + tempId; tbody.appendChild(tr); }
    tr.innerHTML = makeRowHTML(a);
    tr.style.opacity = '0.5';
    tr.title = 'Pending sync…';
    const placeholder = tbody.querySelector('tr td[colspan]');
    if (placeholder) placeholder.closest('tr').remove();
  }

  /* Full table refresh (used after flush) */
  async function refreshAllocationsTable() {
    try {
      const r = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrftoken },
        body: new URLSearchParams({ action: 'list_allocations' })
      });
      const json = await r.json();
      if (json.status === 'success' && json.allocations) {
        const tbody = document.getElementById('allocationsTable');
        if (json.allocations.length === 0) {
          tbody.innerHTML = "<tr><td colspan='8' style='text-align:center;padding:30px;color:#aaa;'>No allocations yet.</td></tr>";
        } else {
          tbody.innerHTML = json.allocations.map(a => {
            const row = document.createElement('tr');
            row.id = 'alloc-' + a.id;
            row.innerHTML = makeRowHTML(a);
            return row.outerHTML;
          }).join('');
        }
      }
    } catch(e) { /* silent — we'll live with stale DOM until next interaction */ }
  }

  /* ═══════════════════════════════════════════════════════════
     RESET FORM
  ═══════════════════════════════════════════════════════════ */
  function resetForm() {
    const form = document.getElementById('allocationForm');
    form.reset();
    document.getElementById('allocationId').value = '';
    document.getElementById('courseNameHint').textContent = 'Select from list to auto-fill';
    document.getElementById('courseNameHint').style.color = '#666';
    document.querySelector('.btn-save').textContent = '✓ Save Allocation';
    document.querySelector('.btn-save').classList.remove('queued');
    // Undo any field lock left over from editing an origin-only collaboration.
    [
      "courseCode", "courseName", "originDepartmentSelect", "programSelect",
      "campusSelect", "teachingCampusSelect", "deliveryMode",
      "academicYear", "programYear", "allocationSemester", "isSpecialCourse"
    ].forEach(fid => {
      const el = document.getElementById(fid);
      if (el) el.disabled = false;
    });
  }

  /* ═══════════════════════════════════════════════════════════
     NAV
  ═══════════════════════════════════════════════════════════ */
  const navToggle  = document.getElementById('navToggle');
  const navClose   = document.getElementById('navClose');
  const sideNav    = document.getElementById('sideNav');
  const navOverlay = document.getElementById('navOverlay');
  function openNav()  { sideNav.classList.add('active'); navOverlay.classList.add('active'); document.body.style.overflow='hidden'; }
  function closeNav() { sideNav.classList.remove('active'); navOverlay.classList.remove('active'); document.body.style.overflow=''; }
  navToggle.addEventListener('click', openNav);
  navClose.addEventListener('click', closeNav);
  navOverlay.addEventListener('click', closeNav);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNav(); });

  /* ═══════════════════════════════════════════════════════════
     COURSES LOADER
  ═══════════════════════════════════════════════════════════ */
  const programSelect   = document.getElementById("programSelect");
  const courseCodeInput = document.getElementById("courseCode");
  const courseNameInput = document.getElementById("courseName");
  const courseNameHint  = document.getElementById("courseNameHint");
  const datalist        = document.getElementById("courseCodes");

  function loadCourses(programId="") {
    const url = courseCodesUrl + (programId ? `?program_id=${programId}` : '');
    fetch(url).then(r=>r.json()).then(data=>{
      datalist.innerHTML="";
      data.courses.forEach(c=>{
        const o=document.createElement("option"); o.value=c.code;
        o.textContent=`${c.code} - ${c.name}`; datalist.appendChild(o);
      });
    }).catch(err=>console.error("Error fetching courses:",err));
  }
  loadCourses();
  programSelect.addEventListener("change", function(){ loadCourses(this.value); courseNameInput.value=""; });

  courseCodeInput.addEventListener("input", function(){
    const val=this.value.trim();
    const opt=Array.from(datalist.options).find(o=>o.value.toLowerCase()===val.toLowerCase());
    if(opt){ const n=opt.textContent.split(" - ")[1]; if(n){ courseNameInput.value=n; courseNameHint.textContent="✅ Auto-filled"; courseNameHint.style.color="#27ae60"; } }
    else { courseNameInput.value=""; courseNameHint.textContent="Enter new course name"; courseNameHint.style.color="#666"; }
  });

  courseCodeInput.addEventListener("blur", function(){
    const code=this.value.trim(), pid=programSelect.value;
    if(!code) return;
    const params = new URLSearchParams({code});
    if(pid) params.append('program_id', pid);
    fetch(`${getCourseNameUrl}?${params}`)
      .then(r=>r.json())
      .then(data=>{
        if(data.found){ courseNameInput.value=data.name; courseNameHint.textContent="✅ Auto-filled from database"; courseNameHint.style.color="#27ae60"; }
      }).catch(()=>{});
  });

  /* ═══════════════════════════════════════════════════════════
     FORM SUBMIT — no reload, surgical DOM update + offline queue
  ═══════════════════════════════════════════════════════════ */
  document.getElementById("allocationForm").addEventListener("submit", async function(e){
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.set('action', 'create_allocation');

    // Validate first (works offline too)
    const errors = validateFormData(fd);
    if (errors.length) {
      toast(errors[0], 'error', 5000);
      return;
    }

    const saveBtn = document.querySelector('.btn-save');
    const isEdit  = !!document.getElementById('allocationId').value;

    if (!navigator.onLine) {
      // Queue it
      const tempId = 'tmp_' + Date.now();
      if (!isEdit) insertTempRow(tempId, fd);
      await queueOp(new URLSearchParams(fd).toString());
      await updateQueueBadge();
      saveBtn.classList.add('queued');
      saveBtn.textContent = '⏳ Queued (offline)';
      toast('Offline — change queued and will sync when back online.', 'warning', 5000);
      setTimeout(() => { saveBtn.classList.remove('queued'); saveBtn.textContent='✓ Save Allocation'; }, 3000);
      resetForm();
      return;
    }

    saveBtn.classList.add('loading');
    saveBtn.textContent = 'Saving…';

    try {
      const json = await apiPost(new URLSearchParams(fd));
      if (json.status === 'success') {
        // Fetch the saved row's full detail to get labels
        const detailJson = await apiPost(new URLSearchParams({ action:'allocation_detail', id: json.id }));
        if (detailJson.status === 'success') {
          upsertRow(detailJson.allocation);
        }
        toast(isEdit ? 'Allocation updated.' : 'Allocation added.', 'success');
        resetForm();
      } else {
        toast(json.message || 'Error saving allocation.', 'error', 5000);
      }
    } catch(err) {
      if (!navigator.onLine) {
        // Went offline mid-request — queue it
        await queueOp(new URLSearchParams(fd).toString());
        await updateQueueBadge();
        toast('Connection lost — change queued.', 'warning', 5000);
        resetForm();
      } else {
        toast('Network error: ' + err.message, 'error', 5000);
      }
    } finally {
      saveBtn.classList.remove('loading');
      saveBtn.textContent = '✓ Save Allocation';
    }
  });

  /* ═══════════════════════════════════════════════════════════
     EDIT — fetch detail, populate form (no reload)
  ═══════════════════════════════════════════════════════════ */
  window.editRow = async function(id) {
    try {
      const json = await apiPost(new URLSearchParams({ action:'allocation_detail', id }));
      if (json.status === 'success') {
        const a = json.allocation;
        document.getElementById("allocationId").value              = a.id;
        document.getElementById("courseCode").value                = a.course_code;
        document.getElementById("courseName").value                = a.course_name;
        document.getElementById("numStudents").value               = a.number_of_students;
        document.getElementById("originDepartmentSelect").value    = a.origin_department_id;
        document.getElementById("programSelect").value             = a.program_id  || "";
        document.getElementById("lecturerSelect").value            = a.lecturer_id || "";
        document.getElementById("campusSelect").value              = a.campus_id   || "";
        document.getElementById("teachingCampusSelect").value      = a.teaching_campus_id || "";
        document.getElementById("deliveryMode").value              = a.delivery_mode || "PHYSICAL";
        document.getElementById("academicYear").value              = a.academic_year || "";
        document.getElementById("programYear").value               = a.program_year  || "";
        document.getElementById("allocationSemester").value        = a.allocation_semester || "";
        document.getElementById("isSpecialCourse").checked         = !!a.is_special_course;

        // An origin-department collaborator (a.is_true_owner === false) can only
        // assign a lecturer / adjust student count here — every other field is
        // owned by the hosting department and is locked to prevent accidental
        // (or silent) ownership/authoring changes on save. The server enforces
        // this regardless, but locking the fields keeps the UI honest about it.
        const collaboratorOnlyFieldIds = [
          "courseCode", "courseName", "originDepartmentSelect", "programSelect",
          "campusSelect", "teachingCampusSelect", "deliveryMode",
          "academicYear", "programYear", "allocationSemester", "isSpecialCourse"
        ];
        const isCollaboratorOnly = a.is_true_owner === false;
        collaboratorOnlyFieldIds.forEach(fid => {
          const el = document.getElementById(fid);
          if (el) el.disabled = isCollaboratorOnly;
        });

        if (isCollaboratorOnly) {
          courseNameHint.textContent = '🔒 Originated by your department — you can only set the lecturer / student count';
          courseNameHint.style.color = '#e65100';
        } else {
          courseNameHint.textContent = '✅ Editing existing allocation';
          courseNameHint.style.color = '#1565c0';
        }
        document.querySelector('.btn-save').textContent = '✓ Update Allocation';
        document.querySelector('.form-section').scrollIntoView({ behavior:'smooth' });
      } else {
        toast(json.message || 'Error loading allocation.', 'error');
      }
    } catch(err) {
      toast('Could not load allocation — check your connection.', 'error');
    }
  };

  /* ═══════════════════════════════════════════════════════════
     DELETE — remove row instantly, rollback on failure
  ═══════════════════════════════════════════════════════════ */
  window.deleteRow = async function(id) {
    if (!confirm("Delete this allocation?")) return;

    const tr = document.getElementById('alloc-' + id);
    const snapshot = tr ? tr.innerHTML : null;

    // Optimistic remove
    if (tr) { tr.style.opacity = '0.4'; tr.style.pointerEvents = 'none'; }

    try {
      const json = await apiPost(new URLSearchParams({ action:'delete_allocation', id }));
      if (json.status === 'success') {
        removeRow(id);
        toast('Allocation deleted.', 'success');
      } else {
        if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
        toast(json.message || 'Error deleting.', 'error');
      }
    } catch(err) {
      // Rollback
      if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
      toast('Delete failed — check your connection.', 'error');
    }
  };

  /* ═══════════════════════════════════════════════════════════
     REJECTED TABLE
  ═══════════════════════════════════════════════════════════ */
  async function loadRejected() {
    try {
      const json = await apiPost(new URLSearchParams({ action:'list_rejected' }));
      const tbody = document.getElementById("rejectedTable");
      if (json.status==="success" && json.rejected && json.rejected.length) {
        tbody.innerHTML = json.rejected.map(r=>`
          <tr id="rej-${r.id}">
            <td><strong>${escHtml(r.course_code)}</strong></td>
            <td>${escHtml(r.course_name)}</td>
            <td>${escHtml(r.program||'-')}</td>
            <td>${escHtml(r.lecturer||'-')}</td>
            <td>${escHtml(r.campus||'-')}</td>
            <td style="text-align:center;font-weight:600;">${r.number_of_students||0}</td>
            <td>${escHtml(r.reason||'-')}</td>
            <td>
              <button class="btn-action btn-edit"   onclick="restoreRejected(${r.id})">&#9851; Restore</button>
              <button class="btn-action btn-delete" onclick="deleteRejected(${r.id})">&#128465; Delete</button>
            </td>
          </tr>`).join('');
        document.getElementById("rejectedHeader").innerHTML = `❌ Rejected Courses (${json.rejected.length})`;
      } else {
        tbody.innerHTML = "<tr><td colspan='8' style='text-align:center;padding:20px;color:#aaa;'>No rejected courses.</td></tr>";
        document.getElementById("rejectedHeader").innerHTML = "❌ Rejected Courses (0)";
      }
    } catch(e) {
      document.getElementById("rejectedTable").innerHTML =
        "<tr><td colspan='8' style='text-align:center;padding:20px;color:#e53935;'>Failed to load rejected courses.</td></tr>";
    }
  }

  window.restoreRejected = async function(id) {
    const tr = document.getElementById('rej-' + id);
    if (tr) { tr.style.opacity = '0.4'; tr.style.pointerEvents = 'none'; }
    try {
      const json = await apiPost(new URLSearchParams({ action:'restore_rejected', id }));
      if (json.status === 'success') {
        document.getElementById('rej-' + id)?.remove();
        toast('Allocation restored to active list.', 'success');
        // Fetch restored row and add to allocations table
        const detailJson = await apiPost(new URLSearchParams({ action:'allocation_detail', id })).catch(()=>null);
        if (detailJson?.status === 'success') upsertRow(detailJson.allocation);
        // Refresh rejected count label
        await loadRejected();
      } else {
        if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
        toast(json.message || 'Restore failed.', 'error');
      }
    } catch(err) {
      if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
      toast('Restore failed — check your connection.', 'error');
    }
  };

  window.deleteRejected = async function(id) {
    if (!confirm("Permanently delete this rejected allocation?")) return;
    const tr = document.getElementById('rej-' + id);
    if (tr) { tr.style.opacity='0.4'; tr.style.pointerEvents='none'; }
    try {
      const json = await apiPost(new URLSearchParams({ action:'delete_rejected', id }));
      if (json.status === 'success') {
        document.getElementById('rej-' + id)?.remove();
        toast('Rejected allocation deleted.', 'success');
        await loadRejected();
      } else {
        if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
        toast(json.message || 'Delete failed.', 'error');
      }
    } catch(err) {
      if (tr) { tr.style.opacity=''; tr.style.pointerEvents=''; }
      toast('Delete failed — check your connection.', 'error');
    }
  };

  /* ═══════════════════════════════════════════════════════════
     SUBMISSION TOGGLES
  ═══════════════════════════════════════════════════════════ */
  document.getElementById("toggle-dvc").addEventListener("change", async function(){
    try {
      const r = await fetch(toggleDvcUrl);
      const data = await r.json();
      if (data.status === 'success') {
        this.checked = data.allow_submission_to_dvc;
        document.getElementById("dvcBox").classList.toggle("active", data.allow_submission_to_dvc);
        toast(data.allow_submission_to_dvc ? 'DVC submission enabled.' : 'DVC submission disabled.', 'info');
      }
    } catch(e) { toast('Toggle failed — check connection.', 'error'); }
  });

  document.getElementById("toggle-tt").addEventListener("change", async function(){
    try {
      const r = await fetch(toggleTtUrl);
      const data = await r.json();
      if (data.status === 'success') {
        this.checked = data.allow_submission_to_tt;
        document.getElementById("ttBox").classList.toggle("active", data.allow_submission_to_tt);
        toast(data.allow_submission_to_tt ? 'Timetable submission enabled.' : 'Timetable submission disabled.', 'info');
      }
    } catch(e) { toast('Toggle failed — check connection.', 'error'); }
  });

  /* ═══════════════════════════════════════════════════════════
     INIT
  ═══════════════════════════════════════════════════════════ */
  loadRejected();
  updateQueueBadge();
  // If we come back online between page loads, flush on first render
  if (navigator.onLine) flushQueue();

});
