/*
 * course_allocation_course_allocation_cod_panel_clear.js
 * Extracted inline JS from: course_allocation/templates/course_allocation/cod_panel_clear.html
 * NOTE: May contain Django template vars - render through Django
 */

function confirmSemesterPrompt(form) {
  let semester = prompt("Enter semester (e.g. 1, 2, 3, or 2025S1):");
  if (!semester) {
    alert("Semester is required to continue.");
    return false;
  }
  document.getElementById("semester_input").value = semester.trim();
  return confirm("Are you sure you want to archive and delete all allocations for semester '" + semester + "'?");
}