/* row_click.js
 * Makes each row in a Django admin changelist table navigate to that
 * row's own edit page when clicked anywhere on the row — except when
 * the click was on a link, button, checkbox, or other interactive
 * element already inside the row, so existing behaviour (selection,
 * the real edit link, any inline action links) is left untouched.
 */
document.addEventListener("DOMContentLoaded", function () {
  var table = document.getElementById("result_list");
  if (!table) return;

  var rows = table.querySelectorAll("tbody tr");

  rows.forEach(function (row) {
    // The first real link in a changelist row is Django's own
    // edit-link for that object (usually in the first <th>/<td>).
    var editLink = row.querySelector("th a, td a");
    if (!editLink) return;

    row.classList.add("clickable-row");

    row.addEventListener("click", function (event) {
      var target = event.target;

      // Don't hijack clicks on links, buttons, checkboxes, or inputs —
      // let them behave exactly as they already do.
      if (target.closest("a, button, input, select, textarea, label")) {
        return;
      }

      // Respect modifier-key clicks (open in new tab, etc.) by not
      // interfering — only handle a plain left click ourselves.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }

      window.location.href = editLink.href;
    });
  });
});