/*
 * documentation_documentation_single_page.js
 * Extracted inline JS from: documentation/templates/documentation/single_page.html
 * NOTE: May contain Django template vars - render through Django
 */

document.addEventListener('DOMContentLoaded', function() {
            // Initialize highlight.js
            hljs.highlightAll();
            
            // Mobile menu toggle
            const mobileMenuBtn = document.getElementById('mobileMenuBtn');
            const sidebar = document.getElementById('sidebar');
            
            if (mobileMenuBtn && sidebar) {
                mobileMenuBtn.addEventListener('click', function() {
                    sidebar.classList.toggle('active');
                });
            }
            
            // Close sidebar when clicking outside on mobile
            document.addEventListener('click', function(event) {
                if (window.innerWidth <= 992 && sidebar.classList.contains('active')) {
                    if (!sidebar.contains(event.target) && !mobileMenuBtn.contains(event.target)) {
                        sidebar.classList.remove('active');
                    }
                }
            });
            
            // Search functionality
            const searchInput = document.getElementById('searchInput');
            const searchResults = document.getElementById('searchResults');
            
            if (searchInput && searchResults) {
                let searchTimeout;
                
                searchInput.addEventListener('input', function() {
                    clearTimeout(searchTimeout);
                    const query = this.value.trim();
                    
                    if (query.length < 2) {
                        searchResults.style.display = 'none';
                        return;
                    }
                    
                    searchTimeout = setTimeout(() => {
                        fetch(`/documentation/api/quick-search/?q=${encodeURIComponent(query)}`)
                            .then(response => response.json())
                            .then(data => {
                                if (data.results && data.results.length > 0) {
                                    searchResults.innerHTML = data.results.map(result => `
                                        <div class="search-result-item" data-url="${result.url}">
                                            <div class="fw-bold">${result.title}</div>
                                            <small class="text-muted">${result.category} • ${result.type}</small>
                                            <div class="text-truncate">${result.description}</div>
                                        </div>
                                    `).join('');
                                    
                                    // Add click handlers
                                    document.querySelectorAll('.search-result-item').forEach(item => {
                                        item.addEventListener('click', function() {
                                            window.location.href = this.getAttribute('data-url');
                                        });
                                    });
                                    
                                    searchResults.style.display = 'block';
                                } else {
                                    searchResults.innerHTML = `
                                        <div class="search-result-item text-muted">
                                            No results found
                                        </div>
                                    `;
                                    searchResults.style.display = 'block';
                                }
                            })
                            .catch(error => {
                                console.error('Search error:', error);
                            });
                    }, 300);
                });
                
                // Hide results when clicking outside
                document.addEventListener('click', function(e) {
                    if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
                        searchResults.style.display = 'none';
                    }
                });
            }
            
            // Copy code functionality
            document.querySelectorAll('.copy-code').forEach(button => {
                button.addEventListener('click', function() {
                    const code = this.getAttribute('data-code');
                    navigator.clipboard.writeText(code).then(() => {
                        const originalText = this.innerHTML;
                        this.innerHTML = '<i class="fas fa-check"></i> Copied!';
                        setTimeout(() => {
                            this.innerHTML = originalText;
                        }, 2000);
                    });
                });
            });
            
            // Smooth scrolling for anchor links
            document.querySelectorAll('a[href^="#"]').forEach(anchor => {
                anchor.addEventListener('click', function(e) {
                    const href = this.getAttribute('href');
                    if (href === '#') return;
                    
                    const targetElement = document.querySelector(href);
                    if (targetElement) {
                        e.preventDefault();
                        targetElement.scrollIntoView({
                            behavior: 'smooth',
                            block: 'start'
                        });
                    }
                });
            });
            
            // Table of contents highlighting
            const tocLinks = document.querySelectorAll('.toc-link');
            const sections = document.querySelectorAll('.content-section[id^="section-"]');
            
            function highlightToc() {
                let currentSection = '';
                
                sections.forEach(section => {
                    const sectionTop = section.offsetTop;
                    const sectionHeight = section.clientHeight;
                    if (window.scrollY >= (sectionTop - 150)) {
                        currentSection = section.id;
                    }
                });
                
                tocLinks.forEach(link => {
                    link.classList.remove('active');
                    if (link.getAttribute('href') === `#${currentSection}`) {
                        link.classList.add('active');
                    }
                });
            }
            
            if (tocLinks.length > 0) {
                window.addEventListener('scroll', highlightToc);
                highlightToc(); // Initial call
            }
        });