/*
 * dashboards_dashboard_venues_panel.js
 * Extracted inline JS from: dashboards/templates/dashboard/venues_panel.html
 * NOTE: May contain Django template vars - render through Django
 */

// ============================================================
//  UTILITIES
// ============================================================
const csrftoken = document.cookie.split('; ')
  .find(r => r.startsWith('csrftoken='))?.split('=')[1];

function showToast(msg, isError = false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = isError ? 'error show' : 'show';
  setTimeout(() => { t.className = ''; }, 3000);
}

function badgeHtml(val) {
  return val
    ? '<span class="badge badge-yes">Yes</span>'
    : '<span class="badge badge-no">No</span>';
}

function postJson(formData) {
  return fetch(window.location.href, {
    method: 'POST',
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrftoken },
    body: formData
  }).then(r => r.json());
}

// ============================================================
//  TABS
// ============================================================
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  });
});

// ============================================================
//  BUILDINGS
// ============================================================
function resetBuildingForm() {
  document.getElementById('buildingId').value          = '';
  document.getElementById('buildingName').value        = '';
  document.getElementById('buildingCode').value        = '';
  document.getElementById('buildingDescription').value = '';
  document.getElementById('buildingIsWorkshop').checked = false;
  document.getElementById('buildingFormTitle').textContent = 'Add New Building';
  document.getElementById('buildingCancelBtn').classList.add('hidden');
}

function editBuilding(id) {
  const row = document.getElementById('building-' + id);
  if (!row) return;
  document.getElementById('buildingId').value          = id;
  document.getElementById('buildingName').value        = row.dataset.name;
  document.getElementById('buildingCode').value        = row.dataset.code;
  document.getElementById('buildingDescription').value = row.dataset.description;
  document.getElementById('buildingIsWorkshop').checked = row.dataset.workshop === 'true';
  document.getElementById('buildingFormTitle').textContent = 'Edit Building';
  document.getElementById('buildingCancelBtn').classList.remove('hidden');
  document.querySelector('#tab-buildings .form-section').scrollIntoView({ behavior: 'smooth' });
}

function saveBuilding() {
  const id          = document.getElementById('buildingId').value;
  const name        = document.getElementById('buildingName').value.trim();
  const code        = document.getElementById('buildingCode').value.trim().toUpperCase();
  const description = document.getElementById('buildingDescription').value.trim();
  const isWorkshop  = document.getElementById('buildingIsWorkshop').checked;

  if (!name || !code) { showToast('Name and Code are required.', true); return; }

  const fd = new FormData();
  fd.append('action', 'create'); fd.append('model_type', 'building');
  fd.append('name', name); fd.append('code', code);
  fd.append('description', description);
  fd.append('is_workshop', isWorkshop ? 'true' : 'false');
  if (id) fd.append('id', id);

  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast(json.message || 'Error saving building.', true); return; }
    const b = json.building;
    updateBuildingOption(b);
    let tr = document.getElementById('building-' + b.id);
    const rowHtml = `
      <td class="b-name">${b.name}</td>
      <td class="b-code">${b.code}</td>
      <td class="b-description">${b.description || '—'}</td>
      <td class="b-workshop">${badgeHtml(b.is_workshop)}</td>
      <td><div class="action-cell">
        <button class="btn btn-edit"   onclick="editBuilding(${b.id})">✏️ Edit</button>
        <button class="btn btn-delete" onclick="deleteBuilding(${b.id})">🗑 Delete</button>
      </div></td>`;
    if (tr) { tr.innerHTML = rowHtml; }
    else {
      document.getElementById('buildings-empty')?.remove();
      tr = document.createElement('tr');
      tr.id = 'building-' + b.id;
      tr.innerHTML = rowHtml;
      document.getElementById('buildingsTable').appendChild(tr);
    }
    tr.dataset.name = b.name; tr.dataset.code = b.code;
    tr.dataset.description = b.description; tr.dataset.workshop = b.is_workshop;
    resetBuildingForm();
    showToast(id ? 'Building updated ✓' : 'Building added ✓');
  }).catch(() => showToast('Network error.', true));
}

function deleteBuilding(id) {
  if (!confirm('Delete this building? Venues linked to it will have no building assigned.')) return;
  const fd = new FormData();
  fd.append('action', 'delete'); fd.append('model_type', 'building'); fd.append('id', id);
  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast('Error deleting building.', true); return; }
    document.getElementById('building-' + id)?.remove();
    document.querySelector(`#venueBuilding option[value="${id}"]`)?.remove();
    if (!document.getElementById('buildingsTable').querySelector('tr[id]')) {
      const emptyRow = document.createElement('tr');
      emptyRow.id = 'buildings-empty';
      emptyRow.innerHTML = '<td colspan="5" class="empty-state">No buildings added yet.</td>';
      document.getElementById('buildingsTable').appendChild(emptyRow);
    }
    showToast('Building deleted ✓');
  }).catch(() => showToast('Network error.', true));
}

function updateBuildingOption(b) {
  const sel = document.getElementById('venueBuilding');
  let opt = sel.querySelector(`option[value="${b.id}"]`);
  if (opt) { opt.textContent = `${b.name} (${b.code})`; }
  else {
    opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = `${b.name} (${b.code})`;
    sel.appendChild(opt);
  }
}

// ============================================================
//  VENUES
// ============================================================
function resetVenueForm() {
  document.getElementById('venueId').value              = '';
  document.getElementById('venueCode').value            = '';
  document.getElementById('venueBuilding').value        = '';
  document.getElementById('venueCapacity').value        = '';
  document.getElementById('venueExamCapacity').value    = '';
  document.getElementById('venueDescription').value     = '';
  document.getElementById('venueIsWorkshop').checked    = false;
  document.getElementById('venueIsSpecialized').checked = false;
  document.getElementById('venueFormTitle').textContent = 'Add New Venue';
  document.getElementById('venueCancelBtn').classList.add('hidden');
}

function editVenue(id) {
  const row = document.getElementById('venue-' + id);
  if (!row) return;
  document.getElementById('venueId').value              = id;
  document.getElementById('venueCode').value            = row.dataset.code;
  document.getElementById('venueBuilding').value        = row.dataset.buildingId || '';
  document.getElementById('venueCapacity').value        = row.dataset.capacity;
  document.getElementById('venueExamCapacity').value    = row.dataset.examCapacity;
  document.getElementById('venueDescription').value     = row.dataset.description;
  document.getElementById('venueIsWorkshop').checked    = row.dataset.workshop    === 'true';
  document.getElementById('venueIsSpecialized').checked = row.dataset.specialized === 'true';
  document.getElementById('venueFormTitle').textContent = 'Edit Venue';
  document.getElementById('venueCancelBtn').classList.remove('hidden');
  document.querySelector('#tab-venues .form-section').scrollIntoView({ behavior: 'smooth' });
}

function saveVenue() {
  const id            = document.getElementById('venueId').value;
  const code          = document.getElementById('venueCode').value.trim().toUpperCase();
  const buildingId    = document.getElementById('venueBuilding').value;
  const capacity      = document.getElementById('venueCapacity').value;
  const examCapacity  = document.getElementById('venueExamCapacity').value;
  const description   = document.getElementById('venueDescription').value.trim();
  const isWorkshop    = document.getElementById('venueIsWorkshop').checked;
  const isSpecialized = document.getElementById('venueIsSpecialized').checked;

  if (!code) { showToast('Venue Code is required.', true); return; }

  const fd = new FormData();
  fd.append('action', 'create'); fd.append('model_type', 'venue');
  fd.append('code', code); fd.append('building_id', buildingId);
  fd.append('capacity', capacity); fd.append('exam_capacity', examCapacity);
  fd.append('description', description);
  fd.append('is_workshop',    isWorkshop    ? 'true' : 'false');
  fd.append('is_specialized', isSpecialized ? 'true' : 'false');
  if (id) fd.append('id', id);

  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast(json.message || 'Error saving venue.', true); return; }
    const v = json.venue;
    const specBadge = v.is_specialized
      ? '<span class="badge badge-specialized">Yes</span>'
      : '<span class="badge badge-no">No</span>';

    let tr = document.getElementById('venue-' + v.id);
    const rowHtml = `
      <td class="v-code">${v.code}</td>
      <td class="v-building">${v.building_name}</td>
      <td class="v-capacity">${v.capacity || '—'}</td>
      <td class="v-exam-capacity">${v.exam_capacity || '—'}</td>
      <td class="v-workshop">${badgeHtml(v.is_workshop)}</td>
      <td class="v-specialized">${specBadge}</td>
      <td><div class="action-cell">
        <button class="btn btn-edit"   onclick="editVenue(${v.id})">✏️ Edit</button>
        <button class="btn btn-delete" onclick="deleteVenue(${v.id})">🗑 Delete</button>
      </div></td>`;

    if (tr) { tr.innerHTML = rowHtml; }
    else {
      document.getElementById('venues-empty')?.remove();
      tr = document.createElement('tr');
      tr.id = 'venue-' + v.id;
      tr.innerHTML = rowHtml;
      document.getElementById('venuesTable').appendChild(tr);
    }
    tr.dataset.code         = v.code;
    tr.dataset.buildingId   = v.building_id;
    tr.dataset.buildingName = v.building_name;
    tr.dataset.capacity     = v.capacity;
    tr.dataset.examCapacity = v.exam_capacity;
    tr.dataset.description  = v.description;
    tr.dataset.workshop     = v.is_workshop;
    tr.dataset.specialized  = v.is_specialized;

    // Keep specVenues select in sync
    const specVenSel = document.getElementById('specVenues');
    if (specVenSel) {
      let opt = specVenSel.querySelector(`option[value="${v.id}"]`);
      const label = `${v.code}${v.capacity ? ' (cap ' + v.capacity + ')' : ''}${v.building_name !== '—' ? ' — ' + v.building_name : ''}`;
      if (opt) { opt.textContent = label; }
      else {
        opt = document.createElement('option');
        opt.value = v.id; opt.textContent = label;
        specVenSel.appendChild(opt);
      }
    }

    resetVenueForm();
    showToast(id ? 'Venue updated ✓' : 'Venue added ✓');
  }).catch(() => showToast('Network error.', true));
}

function deleteVenue(id) {
  if (!confirm('Are you sure you want to delete this venue?')) return;
  const fd = new FormData();
  fd.append('action', 'delete'); fd.append('model_type', 'venue'); fd.append('id', id);
  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast('Error deleting venue.', true); return; }
    document.getElementById('venue-' + id)?.remove();
    document.querySelector(`#specVenues option[value="${id}"]`)?.remove();
    if (!document.getElementById('venuesTable').querySelector('tr[id]')) {
      const emptyRow = document.createElement('tr');
      emptyRow.id = 'venues-empty';
      emptyRow.innerHTML = '<td colspan="7" class="empty-state">No venues added yet.</td>';
      document.getElementById('venuesTable').appendChild(emptyRow);
    }
    showToast('Venue deleted ✓');
  }).catch(() => showToast('Network error.', true));
}

// ============================================================
//  LAB VENUES
// ============================================================
function resetLabForm() {
  document.getElementById('labId').value          = '';
  document.getElementById('labCode').value        = '';
  document.getElementById('labCapacity').value    = '';
  document.getElementById('labDescription').value = '';
  document.getElementById('labEquipment').value   = '';
  document.getElementById('labFormTitle').textContent = 'Add New Lab Venue';
  document.getElementById('labCancelBtn').classList.add('hidden');
}

function editLab(id) {
  const row = document.getElementById('lab-' + id);
  if (!row) return;
  document.getElementById('labId').value          = id;
  document.getElementById('labCode').value        = row.dataset.code;
  document.getElementById('labCapacity').value    = row.dataset.capacity;
  document.getElementById('labDescription').value = row.dataset.description;
  document.getElementById('labEquipment').value   = row.dataset.equipment;
  document.getElementById('labFormTitle').textContent = 'Edit Lab Venue';
  document.getElementById('labCancelBtn').classList.remove('hidden');
  document.querySelector('#tab-labs .form-section').scrollIntoView({ behavior: 'smooth' });
}

function saveLab() {
  const id          = document.getElementById('labId').value;
  const code        = document.getElementById('labCode').value.trim().toUpperCase();
  const capacity    = document.getElementById('labCapacity').value;
  const description = document.getElementById('labDescription').value.trim();
  const equipment   = document.getElementById('labEquipment').value.trim();

  if (!code) { showToast('Lab Code is required.', true); return; }

  const fd = new FormData();
  fd.append('action', 'create'); fd.append('model_type', 'lab_venue');
  fd.append('code', code); fd.append('capacity', capacity);
  fd.append('description', description); fd.append('equipment', equipment);
  if (id) fd.append('id', id);

  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast(json.message || 'Error saving lab venue.', true); return; }
    const l = json.lab;
    let tr = document.getElementById('lab-' + l.id);
    const rowHtml = `
      <td class="l-code">${l.code}</td>
      <td class="l-capacity">${l.capacity || '—'}</td>
      <td class="l-description">${l.description || '—'}</td>
      <td class="l-equipment">${l.equipment || '—'}</td>
      <td><div class="action-cell">
        <button class="btn btn-edit"   onclick="editLab(${l.id})">✏️ Edit</button>
        <button class="btn btn-delete" onclick="deleteLab(${l.id})">🗑 Delete</button>
      </div></td>`;
    if (tr) { tr.innerHTML = rowHtml; }
    else {
      document.getElementById('labs-empty')?.remove();
      tr = document.createElement('tr');
      tr.id = 'lab-' + l.id;
      tr.innerHTML = rowHtml;
      document.getElementById('labsTable').appendChild(tr);
    }
    tr.dataset.code = l.code; tr.dataset.capacity = l.capacity;
    tr.dataset.description = l.description; tr.dataset.equipment = l.equipment;
    resetLabForm();
    showToast(id ? 'Lab venue updated ✓' : 'Lab venue added ✓');
  }).catch(() => showToast('Network error.', true));
}

function deleteLab(id) {
  if (!confirm('Are you sure you want to delete this lab venue?')) return;
  const fd = new FormData();
  fd.append('action', 'delete'); fd.append('model_type', 'lab_venue'); fd.append('id', id);
  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast('Error deleting lab venue.', true); return; }
    document.getElementById('lab-' + id)?.remove();
    if (!document.getElementById('labsTable').querySelector('tr[id]')) {
      const emptyRow = document.createElement('tr');
      emptyRow.id = 'labs-empty';
      emptyRow.innerHTML = '<td colspan="5" class="empty-state">No lab venues added yet.</td>';
      document.getElementById('labsTable').appendChild(emptyRow);
    }
    showToast('Lab venue deleted ✓');
  }).catch(() => showToast('Network error.', true));
}


// ============================================================
//  SPECIALIZATIONS
// ============================================================
function filterCourseOptions() {
  const deptId    = document.getElementById('filterDept').value;
  const programId = document.getElementById('filterProgram').value;

  // When dept changes, filter the program dropdown too
  Array.from(document.getElementById('filterProgram').options).forEach(opt => {
    if (!opt.value) return; // keep "All Programs" always visible
    opt.style.display = (!deptId || opt.dataset.dept === deptId) ? '' : 'none';
  });
  // If the currently selected program is now hidden, reset it
  const progSel = document.getElementById('filterProgram');
  const selOpt  = progSel.options[progSel.selectedIndex];
  if (selOpt && selOpt.style.display === 'none') {
    progSel.value = '';
  }

  // Filter course options
  const effectiveProg = document.getElementById('filterProgram').value;
  Array.from(document.getElementById('specCourses').options).forEach(opt => {
    const matchDept = !deptId    || opt.dataset.dept    === deptId;
    const matchProg = !effectiveProg || opt.dataset.program === effectiveProg;
    opt.style.display = (matchDept && matchProg) ? '' : 'none';
    // Deselect hidden options so they are not submitted
    if (opt.style.display === 'none') opt.selected = false;
  });
}

function onSpecTypeChange() {
  const isProgram = document.getElementById('specTypeProgram').checked;
  document.getElementById('specProgramPanel').style.display = isProgram ? '' : 'none';
  document.getElementById('specCoursePanel').style.display  = isProgram ? 'none' : '';
  if (isProgram) {
    Array.from(document.getElementById('specCourses').options).forEach(o => o.selected = false);
    // Reset cascade filters
    document.getElementById('filterDept').value    = '';
    document.getElementById('filterProgram').value = '';
    filterCourseOptions();
  } else {
    Array.from(document.getElementById('specPrograms').options).forEach(o => o.selected = false);
  }
}

function _autoName() {
  // Build a name from selected venues + programs/courses
  const venues   = Array.from(document.getElementById('specVenues').selectedOptions).map(o => o.text.split(' ')[0]);
  const isProgram = document.getElementById('specTypeProgram').checked;
  let targets;
  if (isProgram) {
    targets = Array.from(document.getElementById('specPrograms').selectedOptions).map(o => o.text.split(' —')[0].trim());
  } else {
    targets = Array.from(document.getElementById('specCourses').selectedOptions).map(o => o.text.split(' —')[0].trim());
  }
  if (!venues.length && !targets.length) return '';
  const vPart = venues.slice(0, 2).join(', ') + (venues.length > 2 ? ' +' + (venues.length - 2) : '');
  const tPart = targets.slice(0, 2).join(', ') + (targets.length > 2 ? ' +' + (targets.length - 2) : '');
  if (vPart && tPart) return `${vPart} → ${tPart}`;
  return vPart || tPart;
}

function resetSpecForm() {
  document.getElementById('specId').value   = '';
  document.getElementById('specName').value = '';
  Array.from(document.getElementById('specVenues').options).forEach(o   => o.selected = false);
  Array.from(document.getElementById('specPrograms').options).forEach(o => o.selected = false);
  Array.from(document.getElementById('specCourses').options).forEach(o  => { o.selected = false; o.style.display = ''; });
  document.getElementById('filterDept').value    = '';
  document.getElementById('filterProgram').value = '';
  // Reset program filter dropdown visibility
  Array.from(document.getElementById('filterProgram').options).forEach(o => o.style.display = '');
  document.getElementById('specTypeProgram').checked = true;
  onSpecTypeChange();
  document.getElementById('specFormTitle').textContent = 'Add New Specialization Rule';
  document.getElementById('specCancelBtn').classList.add('hidden');
}

function editSpec(id) {
  const fd = new FormData();
  fd.append('action', 'get'); fd.append('model_type', 'specialization'); fd.append('id', id);
  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast('Could not load rule.', true); return; }
    const s = json.spec;
    document.getElementById('specId').value   = s.id;
    document.getElementById('specName').value = s.name;

    const isProgram = s.program_ids.length > 0 || s.course_ids.length === 0;
    document.getElementById('specTypeProgram').checked = isProgram;
    document.getElementById('specTypeCourse').checked  = !isProgram;
    onSpecTypeChange();

    Array.from(document.getElementById('specVenues').options).forEach(o => {
      o.selected = s.venue_ids.includes(parseInt(o.value));
    });
    Array.from(document.getElementById('specPrograms').options).forEach(o => {
      o.selected = s.program_ids.includes(parseInt(o.value));
    });
    Array.from(document.getElementById('specCourses').options).forEach(o => {
      o.selected = s.course_ids.includes(parseInt(o.value));
    });

    document.getElementById('specFormTitle').textContent = 'Edit Rule';
    document.getElementById('specCancelBtn').classList.remove('hidden');
    document.querySelector('#tab-specializations .form-section').scrollIntoView({ behavior: 'smooth' });
  }).catch(() => showToast('Network error.', true));
}

function saveSpec() {
  const id        = document.getElementById('specId').value;
  const isProgram = document.getElementById('specTypeProgram').checked;
  const venueIds  = Array.from(document.getElementById('specVenues').selectedOptions).map(o => o.value);
  const programIds = isProgram  ? Array.from(document.getElementById('specPrograms').selectedOptions).map(o => o.value) : [];
  const courseIds  = !isProgram ? Array.from(document.getElementById('specCourses').selectedOptions).map(o => o.value)  : [];

  if (!venueIds.length)                      { showToast('Select at least one venue.', true);   return; }
  if (isProgram  && !programIds.length)      { showToast('Select at least one program.', true); return; }
  if (!isProgram && !courseIds.length)       { showToast('Select at least one course.', true);  return; }

  // Auto-generate the name
  const name = _autoName() || (isProgram ? 'Program Rule' : 'Course Rule');
  document.getElementById('specName').value = name;

  const fd = new FormData();
  fd.append('action', 'create'); fd.append('model_type', 'specialization');
  fd.append('name', name); fd.append('notes', '');
  venueIds.forEach(v   => fd.append('venue_ids[]',   v));
  programIds.forEach(p => fd.append('program_ids[]', p));
  courseIds.forEach(c  => fd.append('course_ids[]',  c));
  if (id) fd.append('id', id);

  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast(json.message || 'Error saving rule.', true); return; }
    _upsertSpecRow(json.spec);
    json.spec.venue_ids.forEach(vid => {
      const tr = document.getElementById('venue-' + vid);
      if (tr) {
        tr.dataset.specialized = 'true';
        const cell = tr.querySelector('.v-specialized');
        if (cell) cell.innerHTML = '<span class="badge badge-specialized">Yes</span>';
      }
    });
    resetSpecForm();
    showToast(id ? 'Rule updated ✓' : 'Rule created ✓');
  }).catch(() => showToast('Network error.', true));
}

function deleteSpec(id) {
  if (!confirm('Delete this specialization rule?')) return;
  const fd = new FormData();
  fd.append('action', 'delete'); fd.append('model_type', 'specialization'); fd.append('id', id);
  postJson(fd).then(json => {
    if (json.status !== 'success') { showToast('Error deleting rule.', true); return; }
    document.getElementById('spec-' + id)?.remove();
    if (!document.getElementById('specsTable').querySelector('tr[id]')) {
      const emptyRow = document.createElement('tr');
      emptyRow.id = 'specs-empty';
      emptyRow.innerHTML = '<td colspan="4" class="empty-state">No specialization rules yet.</td>';
      document.getElementById('specsTable').appendChild(emptyRow);
    }
    showToast('Rule deleted ✓');
  }).catch(() => showToast('Network error.', true));
}

function _upsertSpecRow(s) {
  document.getElementById('specs-empty')?.remove();
  const venueBadges = s.venue_codes.map(c =>
    `<span class="badge badge-specialized">${c}</span>`
  ).join(' ');

  let designatedHtml = '';
  if (s.prog_names && s.prog_names.length) {
    designatedHtml += `<div style="margin-bottom:0.2rem;"><span class="badge" style="background:#e8e8ff;color:#333;">Programs</span></div>`;
    s.prog_names.forEach(n => { designatedHtml += `<div style="font-size:0.85rem;">• ${n}</div>`; });
  }
  if (s.course_labels && s.course_labels.length) {
    if (designatedHtml) designatedHtml += '<div style="margin-top:0.4rem;"></div>';
    designatedHtml += `<div style="margin-bottom:0.2rem;"><span class="badge" style="background:#fff3cd;color:#856404;">Courses</span></div>`;
    s.course_labels.forEach(l => { designatedHtml += `<div style="font-size:0.85rem;">• ${l}</div>`; });
  }
  if (!designatedHtml) designatedHtml = '—';

  let tr = document.getElementById('spec-' + s.id);
  const rowHtml = `
    <td><strong>${s.name}</strong></td>
    <td>${venueBadges || '—'}</td>
    <td>${designatedHtml}</td>
    <td><div class="action-cell">
      <button class="btn btn-edit"   onclick="editSpec(${s.id})">✏️ Edit</button>
      <button class="btn btn-delete" onclick="deleteSpec(${s.id})">🗑 Delete</button>
    </div></td>`;
  if (tr) { tr.innerHTML = rowHtml; }
  else {
    tr = document.createElement('tr');
    tr.id = 'spec-' + s.id;
    tr.innerHTML = rowHtml;
    document.getElementById('specsTable').appendChild(tr);
  }
}