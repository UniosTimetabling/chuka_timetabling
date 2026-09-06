/* export_import_smart.js — extracted from smart_import_export_page.html */
// File input validation
    document.querySelector('input[type="file"]')?.addEventListener('change', function(e) {
        const file = this.files[0];
        if (file) {
            const ext = file.name.split('.').pop().toLowerCase();
            const validExts = ['csv', 'xlsx', 'xls', 'json'];
            if (!validExts.includes(ext)) {
                alert('Please select a valid file: CSV, Excel, or JSON');
                this.value = '';
            }
        }
    });

    // Confirm before canceling import
    document.querySelectorAll('.btn-danger').forEach(btn => {
        btn.addEventListener('click', function(e) {
            if (this.textContent.includes('Cancel') && !confirm('Are you sure you want to cancel?')) {
                e.preventDefault();
            }
        });
    });