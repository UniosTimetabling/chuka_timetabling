/*
 * campuses_timetable_campus_manual_timetable.js
 * Extracted inline JS from: campuses_timetable/templates/campus/manual_timetable.html
 * NOTE: May contain Django template vars - render through Django
 */

let csrftoken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    let currentType = 'class';
    let loadStartTime = Date.now();
    let navOpen = false;

    // Progressive Loading
    document.addEventListener('DOMContentLoaded', function() {
        startProgressiveLoading();
        initEventListeners();
        loadTimeSlots();
        loadTimetableData();
    });

    function toggleNav() {
        navOpen = !navOpen;
        const navMenu = document.getElementById('navMenu');
        const mainContent = document.getElementById('mainContent');
        
        if (navOpen) {
            navMenu.classList.add('open');
            mainContent.classList.add('shifted');
        } else {
            navMenu.classList.remove('open');
            mainContent.classList.remove('shifted');
        }
    }

    function startProgressiveLoading() {
        let progress = 0;
        const interval = setInterval(() => {
            progress += 10;
            document.getElementById('progressBar').style.width = progress + '%';
            
            if (progress <= 30) {
                document.getElementById('loadingMessage').textContent = 'Loading allocations...';
            } else if (progress <= 60) {
                document.getElementById('loadingMessage').textContent = 'Loading timetable...';
            } else if (progress <= 90) {
                document.getElementById('loadingMessage').textContent = 'Preparing interface...';
            } else {
                document.getElementById('loadingMessage').textContent = 'Ready!';
                setTimeout(() => {
                    document.getElementById('loadingOverlay').classList.add('hidden');
                    document.querySelector('.main-content').style.opacity = '1';
                }, 500);
                clearInterval(interval);
            }
        }, 300);
    }

    function initEventListeners() {
        // Form submission
        document.getElementById('timetableForm').addEventListener('submit', handleFormSubmit);
        
        // Availability check
        document.getElementById('checkAvailabilityBtn').addEventListener('click', checkAvailability);
        
        // Date change
        document.getElementById('dateSelect').addEventListener('change', loadTimeSlots);
        
        // Config form
        document.getElementById('configForm').addEventListener('submit', updateConfig);
        
        // Close nav when clicking outside on mobile
        document.addEventListener('click', function(event) {
            if (navOpen && !event.target.closest('.nav-menu') && !event.target.closest('.nav-toggle')) {
                toggleNav();
            }
        });
    }

    function switchMode(mode) {
        currentType = mode;
        
        // Update tabs
        document.querySelectorAll('.tab').forEach(tab => {
            tab.classList.remove('active');
        });
        document.querySelector(`[data-mode="${mode}"]`).classList.add('active');
        
        // Update title
        document.getElementById('timetableTitle').textContent = 
            mode === 'class' ? 'Class Timetable' : 'Exam Timetable';
        
        // Update submit button
        const submitBtn = document.getElementById('submitBtn');
        submitBtn.textContent = mode === 'class' ? 'Save Class Entry' : 'Save Exam Entry';
        
        // Reload time slots and timetable
        loadTimeSlots();
        loadTimetableData();
    }

    async function loadTimeSlots() {
        const date = document.getElementById('dateSelect').value;
        if (!date) return;
        
        const slotSelect = document.getElementById('slotSelect');
        slotSelect.innerHTML = '<option value="">Loading slots...</option>';
        
        try {
            const response = await fetch(`/manual/time-slots/?date=${date}&type=${currentType}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            
            slotSelect.innerHTML = '<option value="">Select a time slot...</option>';
            
            if (data.slots && data.slots.length > 0) {
                data.slots.forEach(slot => {
                    const option = document.createElement('option');
                    option.value = `${slot.start}-${slot.end}`;
                    option.textContent = slot.display;
                    slotSelect.appendChild(option);
                });
            } else if (data.error) {
                slotSelect.innerHTML = `<option value="" disabled>${data.error}</option>`;
            } else {
                slotSelect.innerHTML = '<option value="" disabled>No slots available for this date</option>';
            }
        } catch (error) {
            console.error('Error loading time slots:', error);
            slotSelect.innerHTML = '<option value="" disabled>Error loading slots</option>';
        }
    }

    async function loadTimetableData() {
        const container = document.getElementById('timetableContainer');
        container.innerHTML = '<div style="text-align: center; padding: 20px;">Loading...</div>';
        
        try {
            const response = await fetch(`/manual/timetable-data/?type=${currentType}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                renderTimetable(data);
                
                // Update counts
                if (currentType === 'class') {
                    document.getElementById('classCount').textContent = data.entries.length;
                } else {
                    document.getElementById('examCount').textContent = data.entries.length;
                }
            } else {
                container.innerHTML = '<div class="message error">Error loading timetable</div>';
            }
        } catch (error) {
            console.error('Error loading timetable:', error);
            container.innerHTML = '<div class="message error">Failed to load timetable</div>';
        }
    }

    function renderTimetable(data) {
        const container = document.getElementById('timetableContainer');
        
        if (data.entries.length === 0) {
            container.innerHTML = '<div class="message warning">No timetable entries found</div>';
            return;
        }
        
        // Group by date
        const byDate = {};
        data.entries.forEach(entry => {
            const dateKey = entry.date || entry.day;
            if (!byDate[dateKey]) {
                byDate[dateKey] = [];
            }
            byDate[dateKey].push(entry);
        });
        
        // Sort dates
        const sortedDates = Object.keys(byDate).sort();
        
        let html = '';
        
        sortedDates.forEach(date => {
            html += `<div class="date-group">`;
            html += `<div class="date-header">${formatDate(date)}</div>`;
            html += `<table>`;
            html += `<thead><tr><th>Course</th><th>Time</th><th>Lecturer</th><th>Students</th><th>Campus</th><th>Actions</th></tr></thead>`;
            html += `<tbody>`;
            
            byDate[date].forEach(entry => {
                html += `<tr>`;
                html += `<td><strong>${entry.course}</strong><br><small>${entry.course_name}</small></td>`;
                html += `<td>${entry.start} - ${entry.end}</td>`;
                html += `<td>${entry.lecturer}</td>`;
                html += `<td>${entry.students}</td>`;
                html += `<td><span class="campus-badge">${entry.campus}</span></td>`;
                html += `<td>`;
                html += `<button class="btn btn-sm btn-danger" onclick="deleteEntry(${entry.id}, '${currentType}')">Delete</button>`;
                html += `</td>`;
                html += `</tr>`;
            });
            
            html += `</tbody></table>`;
            html += `</div>`;
        });
        
        container.innerHTML = html;
    }

    function formatDate(dateStr) {
        // Check if it's a day name (Monday, Tuesday) or actual date
        if (dateStr.match(/^[A-Za-z]+$/)) {
            return dateStr;
        }
        
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { 
            weekday: 'long', 
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
    }

    async function handleFormSubmit(e) {
        e.preventDefault();
        
        const allocationId = document.getElementById('allocationSelect').value;
        const date = document.getElementById('dateSelect').value;
        const slot = document.getElementById('slotSelect').value;
        
        if (!allocationId || !date || !slot) {
            showFlashMessage('Please fill all required fields', 'warning');
            return;
        }
        
        const [startTime, endTime] = slot.split('-');
        
        // Check availability first
        const available = await checkAvailabilityInternal(allocationId, date, startTime, endTime);
        
        if (!available.available) {
            showFlashMessage(available.conflicts.join(' | '), 'error');
            return;
        }
        
        // Save entry
        const endpoint = currentType === 'class' 
            ? '/manual/class/create/' 
            : '/manual/exam/create/';
        
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify({
                    course_allocation: allocationId,
                    date: date,
                    start_time: startTime,
                    end_time: endTime
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                showFlashMessage(result.message, 'success');
                loadTimetableData();
                
                // Reset form except date
                document.getElementById('allocationSelect').value = '';
                document.getElementById('slotSelect').innerHTML = '<option value="">Select a time slot...</option>';
                
                // Reload time slots for the same date
                loadTimeSlots();
            } else {
                showFlashMessage(result.error || 'Error saving entry', 'error');
            }
        } catch (error) {
            console.error('Error saving entry:', error);
            showFlashMessage('An error occurred while saving', 'error');
        }
    }

    async function checkAvailability() {
        const allocationId = document.getElementById('allocationSelect').value;
        const date = document.getElementById('dateSelect').value;
        const slot = document.getElementById('slotSelect').value;
        
        if (!allocationId || !date || !slot) {
            showFlashMessage('Please fill all fields first', 'warning');
            return;
        }
        
        const [startTime, endTime] = slot.split('-');
        const result = await checkAvailabilityInternal(allocationId, date, startTime, endTime);
        
        const resultDiv = document.getElementById('availabilityResult');
        resultDiv.style.display = 'block';
        
        if (result.available) {
            resultDiv.className = 'message success';
            resultDiv.innerHTML = '✓ This time slot is available!';
        } else {
            resultDiv.className = 'message error';
            resultDiv.innerHTML = '✗ Conflicts found:<br>' + result.conflicts.map(c => '• ' + c).join('<br>');
        }
    }

    async function checkAvailabilityInternal(allocationId, date, startTime, endTime) {
        try {
            const response = await fetch(
                `/manual/check-availability/?` + 
                `allocation_id=${allocationId}&date=${date}` +
                `&start_time=${startTime}&end_time=${endTime}&exam_mode=${currentType === 'exam'}`
            );
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error('Error checking availability:', error);
            return { available: false, conflicts: ['Error checking availability'] };
        }
    }

    async function deleteEntry(id, type) {
        if (!confirm('Are you sure you want to delete this entry?')) return;
        
        const endpoint = type === 'class' 
            ? `/manual/class/delete/${id}/` 
            : `/manual/exam/delete/${id}/`;
        
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrftoken
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                showFlashMessage(result.message, 'success');
                loadTimetableData();
            } else {
                showFlashMessage(result.error || 'Error deleting entry', 'error');
            }
        } catch (error) {
            console.error('Error deleting entry:', error);
            showFlashMessage('An error occurred while deleting', 'error');
        }
    }

    async function clearTimetable(target) {
        if (!confirm(`Are you sure you want to clear all ${target} entries?`)) return;
        
        try {
            const response = await fetch('/manual/clear/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify({ target })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                showFlashMessage(result.message, 'success');
                loadTimetableData();
            } else {
                showFlashMessage(result.error || 'Error clearing timetable', 'error');
            }
        } catch (error) {
            console.error('Error clearing timetable:', error);
            showFlashMessage('An error occurred while clearing', 'error');
        }
    }

    async function updateConfig(e) {
        e.preventDefault();
        
        const formData = {
            start_date: document.getElementById('startDate').value,
            end_date: document.getElementById('endDate').value,
            day_start_time: document.getElementById('dayStartTime').value,
            day_end_time: document.getElementById('dayEndTime').value,
            class_slot_size: document.getElementById('classSlotSize').value,
            exam_slot_size: document.getElementById('examSlotSize').value
        };
        
        try {
            const response = await fetch('/manual/config/update/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify(formData)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                showFlashMessage(result.message, 'success');
                
                // Update date inputs
                document.getElementById('dateSelect').min = result.config.start_date;
                document.getElementById('dateSelect').max = result.config.end_date;
                
                // Reload time slots
                loadTimeSlots();
            } else {
                showFlashMessage(result.error || 'Error updating configuration', 'error');
            }
        } catch (error) {
            console.error('Error updating config:', error);
            showFlashMessage('An error occurred while updating configuration', 'error');
        }
    }

    function showFlashMessage(message, type) {
        const div = document.createElement('div');
        div.className = `flash-message ${type}`;
        div.textContent = message;
        document.body.appendChild(div);
        
        setTimeout(() => div.remove(), 5000);
    }