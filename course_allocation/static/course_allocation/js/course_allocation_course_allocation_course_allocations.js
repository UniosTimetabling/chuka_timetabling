/*
 * course_allocation_course_allocation_course_allocations.js
 * Extracted inline JS from: course_allocation/templates/course_allocation/course_allocations.html
 * NOTE: May contain Django template vars - render through Django
 */

(function(){
  const deptSelect     = document.getElementById('dept-select');
  const filterBtn      = document.getElementById('filter-btn');
  const resetBtn       = document.getElementById('reset-btn');
  const searchInput    = document.getElementById('search-input');
  const searchBtn      = document.getElementById('search-btn');
  const clearSearchBtn = document.getElementById('clear-search-btn');
  const rows           = Array.from(document.querySelectorAll('tr.alloc-row'));
  const noResults      = document.getElementById('no-results');
  const deptChips      = Array.from(document.querySelectorAll('.dept-chip'));

  function clearHighlights() {
    rows.forEach(r => r.classList.remove('highlight'));
  }

  function applyDeptFilter(deptId) {
    clearHighlights();
    let anyVisible = false;
    rows.forEach(r => {
      const rowDept = r.getAttribute('data-dept-id');
      if (!deptId || deptId === '' || rowDept === deptId) {
        r.classList.remove('hidden'); anyVisible = true;
      } else {
        r.classList.add('hidden');
      }
    });
    noResults.style.display = anyVisible ? 'none' : '';
  }

  function resetAll() {
    deptSelect.value = '';
    searchInput.value = '';
    rows.forEach(r => r.classList.remove('hidden'));
    clearHighlights();
    noResults.style.display = 'none';
    deptChips.forEach(c => c.classList.remove('active'));
  }

  function doSearch(query) {
    query = (query || '').trim().toLowerCase();
    clearHighlights();
    if (!query) { applyDeptFilter(deptSelect.value); return; }

    const visibleRows = rows.filter(r => !r.classList.contains('hidden'));
    const matches = visibleRows.filter(r => {
      const code = (r.dataset.courseCode || '').toLowerCase();
      const name = (r.dataset.courseName || '').toLowerCase();
      return code.includes(query) || name.includes(query);
    });

    clearHighlights();
    matches.forEach(r => r.classList.add('highlight'));

    if (matches.length === 0) {
      noResults.style.display = '';
    } else {
      noResults.style.display = 'none';
      try { matches[0].scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch(e) {}
    }
  }

  filterBtn.addEventListener('click', function() {
    const deptId = deptSelect.value;
    deptChips.forEach(c => c.classList.toggle('active', c.dataset.deptId === deptId && deptId !== ''));
    applyDeptFilter(deptId);
  });

  resetBtn.addEventListener('click', resetAll);

  deptChips.forEach(chip => {
    chip.addEventListener('click', function() {
      const id = this.dataset.deptId;
      deptSelect.value = id;
      deptChips.forEach(c => c.classList.remove('active'));
      this.classList.add('active');
      applyDeptFilter(id);
    });
  });

  searchBtn.addEventListener('click', () => doSearch(searchInput.value));
  searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); doSearch(searchInput.value); } });
  clearSearchBtn.addEventListener('click', () => { searchInput.value = ''; clearHighlights(); noResults.style.display = 'none'; });

  resetAll();
})();