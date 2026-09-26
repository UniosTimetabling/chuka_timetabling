/*
 * department_management_department_management_dean_panel.js
 * Extracted inline JS from: department_management/templates/department_management/dean_panel.html
 * NOTE: May contain Django template vars - render through Django
 */

// ===== Modal Logic =====
  const modal = document.getElementById("dept-modal");
  const addBtn = document.getElementById("add-dept-btn");
  const closeBtn = modal.querySelector(".close");
  const form = document.getElementById("dept-form");
  const title = document.getElementById("modal-title");
  const idInput = document.getElementById("dept-id");
  const nameInput = document.getElementById("dept-name");
  const descInput = document.getElementById("dept-desc");
  const facultyInput = document.getElementById("dept-faculty");
  const leaderInput = document.getElementById("dept-leader");
  const newCodContainer = document.getElementById("new-cod-name-container");
  const newCodInput = document.getElementById("new-cod-name");
  const list = document.getElementById("dept-list");

  leaderInput.addEventListener("change", () => {
    newCodContainer.style.display = leaderInput.value === "new" ? "block" : "none";
    if(leaderInput.value !== "new") newCodInput.value = "";
  });

  addBtn.onclick = () => {
    title.textContent = "Add Department";
    idInput.value = "";
    nameInput.value = "";
    descInput.value = "";
    facultyInput.value = "";
    leaderInput.value = "none";
    newCodInput.value = "";
    newCodContainer.style.display = "none";
    modal.style.display = "block";
  };

  closeBtn.onclick = () => modal.style.display = "none";
  window.onclick = e => { if(e.target == modal) modal.style.display = "none"; };

  form.onsubmit = async e => {
    e.preventDefault();
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    const action = idInput.value ? "edit" : "create";

    const formData = new FormData();
    formData.append("action", action);
    if(idInput.value) formData.append("id", idInput.value);
    formData.append("name", nameInput.value);
    formData.append("description", descInput.value);
    formData.append("faculty_id", facultyInput.value);
    formData.append("leader_option", leaderInput.value);
    formData.append("new_cod_name", newCodInput.value);

    const response = await fetch("", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfToken },
      body: formData
    });
    const data = await response.json();
    if(data.status === "success") modal.style.display = "none";
    updateList(data);
  };

  function updateList(data){
    if(data.department){
      let li = list.querySelector(`li[data-id='${data.department.id}']`);
      if(li){
        li.querySelector(".dept-name").textContent = data.department.name;
        li.querySelector(".dept-faculty").textContent = data.department.faculty;
        li.querySelector(".dept-description").textContent = data.department.description;
        li.querySelector(".dept-leader").textContent = data.department.leader || "None";
      } else {
        li = document.createElement("li");
        li.classList.add("dept-card");
        li.setAttribute("data-id", data.department.id);
        li.innerHTML = `
          <div class="dept-header">
            <span class="dept-name">${data.department.name}</span>
            <span class="dept-faculty">${data.department.faculty}</span>
          </div>
          <div class="dept-body">
            <p class="dept-description">${data.department.description}</p>
            <p class="dept-leader"><strong>COD:</strong> ${data.department.leader || 'None'}</p>
          </div>
          <div class="dept-actions">
            <button class="edit-btn">✏️ Edit</button>
            <button class="delete-btn">🗑 Delete</button>
          </div>`;
        list.appendChild(li);
        
        // Remove empty state if it exists
        const emptyState = list.querySelector('.empty-state');
        if (emptyState) {
          emptyState.remove();
        }
      }
    } else if(data.id){
      const li = list.querySelector(`li[data-id='${data.id}']`);
      if(li) li.remove();
      
      // Add empty state if no departments left
      if (list.children.length === 0) {
        const emptyState = document.createElement('li');
        emptyState.classList.add('empty-state');
        emptyState.innerHTML = `
          <h3>No departments found</h3>
          <p>Click "Add Department" to create your first department</p>`;
        list.appendChild(emptyState);
      }
    }
  }

  // Edit / Delete
  list.onclick = e => {
    const li = e.target.closest("li");
    if(!li) return;
    const id = li.dataset.id;

    if(e.target.classList.contains("edit-btn")){
      title.textContent = "Edit Department";
      idInput.value = id;
      nameInput.value = li.querySelector(".dept-name").textContent;
      descInput.value = li.querySelector(".dept-description").textContent;
      facultyInput.value = [...facultyInput.options].find(opt => opt.text === li.querySelector(".dept-faculty").textContent)?.value || "";
      const currentLeader = li.querySelector(".dept-leader").textContent.replace("COD: ","").trim();
      leaderInput.value = [...leaderInput.options].find(opt => opt.text === currentLeader)?.value || "none";
      newCodInput.value = "";
      newCodContainer.style.display = "none";
      modal.style.display = "block";
    }

    if(e.target.classList.contains("delete-btn")){
      if(confirm("Are you sure you want to delete this department?")){
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
        const formData = new FormData();
        formData.append("action","delete");
        formData.append("id", id);

        fetch("", {
          method: "POST",
          headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfToken },
          body: formData
        })
        .then(res => res.json())
        .then(data => updateList(data))
        .catch(err => { console.error(err); alert("Delete failed."); });
      }
    }
  };