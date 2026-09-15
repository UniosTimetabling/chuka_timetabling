/* Toggle visibility of the "Generic API Template" fieldset depending on
 * which provider is selected, so the admin form doesn't look intimidating
 * for people just picking Gemini/OpenAI/Anthropic/Ollama/DeepSeek/etc.
 */
(function () {
  function findGenericFieldset() {
    var fieldsets = document.querySelectorAll("fieldset.module, div.form-row, .field-generic_http_method");
    // Locate the <fieldset> that contains the generic_http_method field.
    var marker = document.querySelector(".field-generic_http_method, [class*='generic_http_method']");
    if (!marker) return null;
    return marker.closest("fieldset") || marker.closest(".module");
  }

  function toggle() {
    var select = document.getElementById("id_provider");
    var fieldset = findGenericFieldset();
    if (!select || !fieldset) return;
    fieldset.style.display = select.value === "generic" ? "" : "none";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("id_provider");
    if (!select) return;
    toggle();
    select.addEventListener("change", toggle);
  });
})();