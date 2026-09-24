/*
 * classreps_classrep_dashboard.js
 * Extracted inline JS from: classreps/templates/classrep_dashboard.html
 * NOTE: May contain Django template vars - render through Django
 */

// AJAX Program Code Form
    document.getElementById("programCodeForm").addEventListener("submit", async function(e) {
      e.preventDefault();
      const formData = new FormData(this);
      const submitBtn = this.querySelector('button[type="submit"]');
      const originalText = submitBtn.textContent;
      
      // Show loading state
      submitBtn.textContent = "Saving...";
      submitBtn.disabled = true;
      
      try {
        const response = await fetch("", {
          method: "POST",
          headers: { "X-Requested-With": "XMLHttpRequest" },
          body: formData
        });
        const data = await response.json();

        const result = document.getElementById("result");
        result.style.display = "block";
        
        if (data.success) {
          result.className = "alert alert-success";
          result.innerHTML = data.message;
          // Clear form on success
          this.reset();
        } else {
          result.className = "alert alert-warning";
          result.innerHTML = data.message || "Error occurred.";
        }
        
        // Reload after a delay to show the updated table
        setTimeout(() => location.reload(), 1500);
      } catch (error) {
        const result = document.getElementById("result");
        result.style.display = "block";
        result.className = "alert alert-warning";
        result.innerHTML = "Network error occurred. Please try again.";
      } finally {
        // Restore button state
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
      }
    });