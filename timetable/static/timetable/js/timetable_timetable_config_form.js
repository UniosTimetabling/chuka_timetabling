/*
 * timetable_timetable_config_form.js
 * Extracted inline JS from: timetable/templates/timetable/config_form.html
 * NOTE: May contain Django template vars - render through Django
 */

document.getElementById("configForm").addEventListener("submit", function(e) {
      let start = document.getElementById("id_start_time").value;
      let end = document.getElementById("id_end_time").value;
      let slot = document.getElementById("id_slot_size").value;

      // Regex: HH:MM:SS in 24h format
      let timePattern = /^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$/;

      if (!timePattern.test(start) || !timePattern.test(end)) {
        alert("Please enter Start and End times in 24-hour format (HH:MM:SS). Example: 07:00:00 or 19:00:00.");
        e.preventDefault();
        return;
      }

      if (parseInt(slot) <= 0) {
        alert("Slot size must be greater than 0.");
        e.preventDefault();
        return;
      }
    });

    // Add some visual feedback for form interactions
    document.querySelectorAll('input, select').forEach(element => {
      element.addEventListener('focus', function() {
        this.style.backgroundColor = '#f8f9fa';
      });
      
      element.addEventListener('blur', function() {
        this.style.backgroundColor = '';
      });
    });