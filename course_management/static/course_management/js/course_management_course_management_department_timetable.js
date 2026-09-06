/*
 * course_management_course_management_department_timetable.js
 * Extracted inline JS from: course_management/templates/course_management/department_timetable.html
 * NOTE: May contain Django template vars - render through Django
 */

// Configuration
    const API_ENDPOINTS = {
        timetableData: '/api/timetable/department-data/',
        departmentCourses: '/api/timetable/department-courses/',
        departmentPrograms: '/api/timetable/department-programs/',
        userDepartmentInfo: '/api/timetable/user-department-info/',
        selectDepartment: '/select-department/'
    };

    // Global state
    let currentState = {
        timetableType: 'regular',
        searchQuery: '',
        programFilter: '',
        examDate: '',
        department: null,
        courses: [],
        programs: [],
        timetableData: null,
        searchSuggestions: []
    };

    // Initialize on page load
    $(document).ready(function() {
        // Start loading process immediately
        startLoadingProcess();
        setupEventListeners();
        
        // Auto-refresh every 5 minutes
        setInterval(loadTimetableData, 300000);
    });

    // Start the loading process
    function startLoadingProcess() {
        showLoading();
        loadUserDepartmentInfo();
    }

    // Load user department information AND initial timetable data
    function loadUserDepartmentInfo() {
        $.ajax({
            url: API_ENDPOINTS.userDepartmentInfo,
            method: 'GET',
            success: function(response) {
                if (response.current_department) {
                    currentState.department = response.current_department;
                    updateDepartmentHeader(response);
                    
                    if (response.can_select) {
                        $('#switchDeptBtn').show();
                    } else {
                        $('#switchDeptBtn').hide();
                    }
                    
                    // Load programs and courses
                    loadProgramsAndCourses();
                    
                    // Load timetable data IMMEDIATELY
                    loadTimetableData();
                    
                } else if (response.redirect) {
                    window.location.href = response.redirect_url;
                    hideLoading();
                } else {
                    hideLoading();
                }
            },
            error: function() {
                showError('Failed to load department information');
                hideLoading();
            }
        });
    }

    // Load programs and courses for search
    function loadProgramsAndCourses() {
        // Load programs for filter dropdown
        $.ajax({
            url: API_ENDPOINTS.departmentPrograms,
            method: 'GET',
            success: function(response) {
                if (response.programs && !response.redirect) {
                    currentState.programs = response.programs;
                    populateProgramFilter(response.programs);
                }
            }
        });

        // Load courses for search suggestions
        $.ajax({
            url: API_ENDPOINTS.departmentCourses,
            method: 'GET',
            success: function(response) {
                if (response.courses && !response.redirect) {
                    currentState.courses = response.courses;
                }
            }
        });
    }

    // Load timetable data based on current filters
    function loadTimetableData() {
        showLoading();
        
        const params = {
            type: currentState.timetableType,
            search: currentState.searchQuery,
            program_id: currentState.programFilter
        };
        
        // Only add date parameter if explicitly set by user (not empty)
        if (currentState.timetableType === 'exam' && currentState.examDate && currentState.examDate.trim() !== '') {
            params.date = currentState.examDate;
        }
        // If it's exam type but no date specified, don't send date parameter
        // This will make the backend return ALL exams
        
        $.ajax({
            url: API_ENDPOINTS.timetableData,
            method: 'GET',
            data: params,
            success: function(response) {
                if (response.error) {
                    if (response.redirect) {
                        window.location.href = response.redirect_url;
                    } else {
                        showError(response.error);
                    }
                    hideLoading();
                    return;
                }
                
                currentState.timetableData = response;
                renderTimetable(response);
                hideLoading();
            },
            error: function(xhr, status, error) {
                showError('Failed to load timetable data: ' + error);
                hideLoading();
            }
        });
    }

    // Render timetable based on data
    function renderTimetable(data) {
        const container = $('#timetableContainer');
        
        if (!data.has_data) {
            $('#noDataMessage').show();
            container.html('');
            return;
        }
        
        $('#noDataMessage').hide();
        
        if (data.type === 'regular') {
            renderRegularTimetable(data);
        } else {
            renderExamTimetable(data);
        }
        
        // Highlight search terms
        if (currentState.searchQuery) {
            highlightSearchTerms(currentState.searchQuery);
        }
        
        // Setup course hover events
        setupCourseHoverEvents();
        
        // Update table title
        if (data.type === 'regular') {
            $('#tableDateLabel').text('- Week View');
        } else if (data.date_range) {
            $('#tableDateLabel').text('- ' + data.date_range);
        } else {
            $('#tableDateLabel').text('- All Scheduled Exams');
        }
    }

    // Render regular timetable
    function renderRegularTimetable(data) {
        let html = '<div class="timetable-container">';
        
        const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
        
        days.forEach(day => {
            const dayData = data.days[day] || [];
            
            if (dayData.length > 0) {
                // Get unique venues and timeslots for this day
                const venues = [...new Set(dayData.map(course => course.venue))].sort();
                const timeSlots = [...new Set(dayData.map(course => course.time_slot))].sort((a, b) => {
                    return new Date('1970/01/01 ' + a.split(' - ')[0]) - new Date('1970/01/01 ' + b.split(' - ')[0]);
                });
                
                html += `
                    <div class="day-table-container">
                        <div class="day-header">
                            <span><i class="fas fa-calendar-day me-2"></i>${day}</span>
                            <span class="badge bg-light text-dark">${dayData.length} sessions</span>
                        </div>
                        <div class="table-responsive">
                            <table class="day-table ${$('#compactView').is(':checked') ? 'compact' : ''}">
                                <thead>
                                    <tr>
                                        <th class="venue-header">Venue</th>`;
                
                // Add time slot headers
                timeSlots.forEach(slot => {
                    html += `<th class="time-header">${slot}</th>`;
                });
                
                html += `</tr></thead><tbody>`;
                
                // Create rows for each venue
                venues.forEach(venue => {
                    html += `<tr><td class="venue-header">${venue}</td>`;
                    
                    // For each time slot, find the course in this venue
                    timeSlots.forEach(slot => {
                        const course = dayData.find(c => 
                            c.venue === venue && c.time_slot === slot
                        );
                        
                        if (course) {
                            html += `<td class="course-cell" 
                                       data-course-code="${course.course_code}"
                                       data-course-name="${course.course_name}"
                                       data-lecturer="${course.lecturer}"
                                       data-program="${course.program}"
                                       data-venue="${course.venue}"
                                       data-time="${course.time_slot}"
                                       data-students="${course.students}">
                                        <div class="course-cell-content">
                                            <div class="course-code">${course.course_code}</div>
                                            <div class="course-name">${course.course_name}</div>
                                            <div class="course-lecturer">${course.lecturer}</div>
                                            <div class="course-program">${course.program}</div>
                                        </div>
                                    </td>`;
                        } else {
                            html += '<td class="empty-cell">-</td>';
                        }
                    });
                    
                    html += '</tr>';
                });
                
                html += `</tbody></table></div></div>`;
            }
        });
        
        html += '</div>';
        $('#timetableContainer').html(html);
    }

    // Render exam timetable
    function renderExamTimetable(data) {
        let html = '<div class="timetable-container">';
        
        if (!data.dates || Object.keys(data.dates).length === 0) {
            html = '<div class="text-center py-5">No exam schedule available</div>';
        } else {
            Object.values(data.dates).forEach(dateData => {
                const entries = dateData.entries || [];
                
                if (entries.length > 0) {
                    // Get unique venues and timeslots for this date
                    const venues = [...new Set(entries.map(course => course.venue))].sort();
                    const timeSlots = [...new Set(entries.map(course => course.time_slot))].sort((a, b) => {
                        return new Date('1970/01/01 ' + a.split(' - ')[0]) - new Date('1970/01/01 ' + b.split(' - ')[0]);
                    });
                    
                    html += `
                        <div class="day-table-container">
                            <div class="day-header exam-date-header">
                                <span><i class="fas fa-file-alt me-2"></i>${dateData.display_date} (${dateData.day})</span>
                                <span class="badge bg-light text-dark">${entries.length} exams</span>
                            </div>
                            <div class="table-responsive">
                                <table class="day-table exam-table ${$('#compactView').is(':checked') ? 'compact' : ''}">
                                    <thead>
                                        <tr>
                                            <th class="venue-header">Venue</th>`;
                    
                    // Add time slot headers
                    timeSlots.forEach(slot => {
                        html += `<th class="time-header">${slot}</th>`;
                    });
                    
                    html += `</tr></thead><tbody>`;
                    
                    // Create rows for each venue
                    venues.forEach(venue => {
                        html += `<tr><td class="venue-header">${venue}</td>`;
                        
                        // For each time slot, find the course in this venue
                        timeSlots.forEach(slot => {
                            const course = entries.find(c => 
                                c.venue === venue && c.time_slot === slot
                            );
                            
                            if (course) {
                                html += `<td class="course-cell" 
                                           data-course-code="${course.course_code}"
                                           data-course-name="${course.course_name}"
                                           data-lecturer="${course.lecturer}"
                                           data-program="${course.program}"
                                           data-venue="${course.venue}"
                                           data-time="${course.time_slot}"
                                           data-students="${course.students}">
                                            <div class="course-cell-content">
                                                <div class="course-code">${course.course_code}</div>
                                                <div class="course-name">${course.course_name}</div>
                                                <div class="course-lecturer">${course.lecturer}</div>
                                                <div class="course-program">${course.program}</div>
                                            </div>
                                        </td>`;
                            } else {
                                html += '<td class="empty-cell">-</td>';
                            }
                        });
                        
                        html += '</tr>';
                    });
                    
                    html += `</tbody></table></div></div>`;
                }
            });
        }
        
        html += '</div>';
        $('#timetableContainer').html(html);
    }

    // Setup event listeners
    function setupEventListeners() {
        // Timetable type toggle - load immediately on change
        $('input[name="timetableType"]').change(function() {
            currentState.timetableType = $(this).attr('id') === 'regularType' ? 'regular' : 'exam';
            
            if (currentState.timetableType === 'exam') {
                $('#examDateContainer').show();
                $('#tableTypeLabel').text('Exam Timetable');
                // Clear exam date by default to show ALL exams
                $('#examDate').val('');
                currentState.examDate = '';
            } else {
                $('#examDateContainer').hide();
                $('#tableTypeLabel').text('Regular Timetable');
            }
            
            loadTimetableData();
        });

        // Search functionality
        $('#searchInput').on('input', function() {
            const query = $(this).val().trim();
            currentState.searchQuery = query;
            
            if (query.length >= 2) {
                showSearchSuggestions(query);
            } else {
                $('#searchSuggestions').hide();
            }
        });

        $('#searchBtn').click(function() {
            currentState.searchQuery = $('#searchInput').val().trim();
            loadTimetableData();
        });

        $('#searchInput').keypress(function(e) {
            if (e.which === 13) { // Enter key
                currentState.searchQuery = $(this).val().trim();
                loadTimetableData();
            }
        });

        $('#clearSearch').click(function() {
            $('#searchInput').val('');
            currentState.searchQuery = '';
            $('#searchSuggestions').hide();
            loadTimetableData();
        });

        // Program filter
        $('#programFilter').change(function() {
            currentState.programFilter = $(this).val();
            loadTimetableData();
        });

        // Exam date
        $('#examDate').change(function() {
            currentState.examDate = $(this).val();
            loadTimetableData();
        });

        $('#clearDate').click(function() {
            $('#examDate').val('');
            currentState.examDate = '';
            loadTimetableData();
        });

        // Refresh button
        $('#refreshBtn').click(function() {
            loadTimetableData();
        });

        // Download PDF button — server-rendered, letterhead-formatted PDF
        // (reportlab) instead of the old window.print() browser output.
        $('#printBtn').click(function() {
            const params = {
                type: currentState.timetableType,
                search: currentState.searchQuery,
                program_id: currentState.programFilter
            };
            if (currentState.timetableType === 'exam' && currentState.examDate && currentState.examDate.trim() !== '') {
                params.date = currentState.examDate;
            }
            const pdfUrl = $(this).data('pdf-url');
            window.location.href = pdfUrl + '?' + $.param(params);
        });

        // Switch department
        $('#switchDeptBtn').click(function(e) {
            e.preventDefault();
            window.location.href = API_ENDPOINTS.selectDepartment;
        });

        // Compact view toggle
        $('#compactView').change(function() {
            if ($(this).is(':checked')) {
                $('.day-table').addClass('compact');
            } else {
                $('.day-table').removeClass('compact');
            }
        });
    }

    // Show search suggestions
    function showSearchSuggestions(query) {
        if (!currentState.courses || currentState.courses.length === 0) {
            return;
        }
        
        const suggestions = currentState.courses.filter(course => {
            return course.course_code.toLowerCase().includes(query.toLowerCase()) ||
                   course.course_name.toLowerCase().includes(query.toLowerCase());
        }).slice(0, 5); // Limit to 5 suggestions
        
        if (suggestions.length > 0) {
            let html = '<div class="list-group">';
            suggestions.forEach(course => {
                html += `<a href="#" class="list-group-item list-group-item-action" 
                            data-course-code="${course.course_code}">
                            <strong>${course.course_code}</strong> - ${course.course_name}
                         </a>`;
            });
            html += '</div>';
            
            $('#searchSuggestions').html(html).show();
            
            // Setup click handlers for suggestions
            $('#searchSuggestions .list-group-item').click(function(e) {
                e.preventDefault();
                const courseCode = $(this).data('course-code');
                $('#searchInput').val(courseCode);
                currentState.searchQuery = courseCode;
                $('#searchSuggestions').hide();
                loadTimetableData();
            });
        } else {
            $('#searchSuggestions').hide();
        }
    }

    // Highlight search terms in the timetable
    function highlightSearchTerms(query) {
        if (!query) return;
        
        const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        
        $('.course-cell-content').each(function() {
            const $this = $(this);
            const html = $this.html();
            const highlighted = html.replace(regex, '<span class="search-highlight">$1</span>');
            $this.html(highlighted);
        });
    }

    // Setup course hover events for tooltip
    function setupCourseHoverEvents() {
        $('.course-cell').hover(
            function(e) {
                const $cell = $(this);
                const tooltip = $('#courseTooltip');
                
                // Set tooltip content
                $('#tooltipCourseCode').text($cell.data('course-code'));
                $('#tooltipCourseName').text($cell.data('course-name'));
                $('#tooltipLecturer').text($cell.data('lecturer'));
                $('#tooltipProgram').text($cell.data('program'));
                $('#tooltipVenue').text($cell.data('venue'));
                $('#tooltipTime').text($cell.data('time'));
                $('#tooltipStudents').text($cell.data('students'));
                
                // Position and show tooltip
                tooltip.css({
                    left: e.pageX + 10,
                    top: e.pageY + 10,
                    display: 'block'
                });
            },
            function() {
                $('#courseTooltip').hide();
            }
        );
        
        // Move tooltip with mouse
        $(document).mousemove(function(e) {
            const tooltip = $('#courseTooltip');
            if (tooltip.is(':visible')) {
                tooltip.css({
                    left: e.pageX + 10,
                    top: e.pageY + 10
                });
            }
        });
    }

    // Populate program filter dropdown
    function populateProgramFilter(programs) {
        const $select = $('#programFilter');
        $select.empty().append('<option value="">All Programs</option>');
        
        programs.forEach(program => {
            $select.append(`<option value="${program.id}">${program.name}</option>`);
        });
    }

    // Update department header
    function updateDepartmentHeader(data) {
        $('#deptName').text(data.current_department.name);
    }

    // Show loading indicator
    function showLoading() {
        $('#loadingOverlay').show();
    }

    // Hide loading indicator
    function hideLoading() {
        $('#loadingOverlay').hide();
    }

    // Show error message
    function showError(message) {
        // Create error alert
        const alertHtml = `<div class="alert alert-danger alert-dismissible fade show" role="alert">
                            <i class="fas fa-exclamation-triangle me-2"></i>
                            ${message}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                          </div>`;
        
        // Prepend to container
        $('.container-fluid').prepend(alertHtml);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            $('.alert').alert('close');
        }, 5000);
    }