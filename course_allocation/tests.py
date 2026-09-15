let it not look simple let it be as normal websites e.g https://library.chuka.ac.ke/ we can use such design let use online images and we include the text here to is department info The University through its various academic programmes train professionals and non-professionals in the public and private sector. This is in line with the University Vision to be a Premier University in the provision of quality education, training and research for sustainable national and global development.

DEPARTMENTAL OBJECTIVES

To prepare semester schedules in consultation with the Registrar (AA)
To prepare schedules for conducting, processing, and administration of semester examinations
To work closely with Deans, Directors, and COD’s in locating appropriate teaching facilities
To ensure security and confidentiality in preparation of semester examinations
To prepare a budget and make follow ups for procurement of materials needed by the timetabling and examinations
To advice the Registrar (AA) on critical issues on timetabling and conduct of examinations
To work closely with Deans, Chairman of departments on monitoring the implementation of curriculum
To ensure proper management and maintenance of lecture halls, lecture theatres,
workshops and laboratories in consultation with the respective Deans of Faculties.
To ensure that both teaching and examination timetables are followed as scheduled
The functions of the Directorate are:

Preparation of the academic calendar for the undergraduate and postgraduate programmes in the University.
Preparation of the teaching, consultation, and examinations timetable at the beginning of each semester for all degree, diploma and certificate programmes.
Ensure that both teaching and examination timetables are followed as scheduled.
Scheduling of all academic trips.
Co-ordinates use of various lecture halls for academic and non-academic purposes.
Ensure proper management and maintenance of lecture halls, theatres, workshops and laboratories in consultation with relevant departments.
Ensure proper processing of all the University examinations.
Co-ordinating University examinations and providing all the necessary logistics and materials.
Ensure that standards and procedures of the University examinations are maintained.
Work closely with Deans and Chairpersons of departments on monitoring of implementation of the curriculum.
Facilitating invitation and the working of all the external examiners. produce the website modern {% load static %}
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chuka University Portal</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --primary-green: #2E7D32;
            --dark-green: #1B5E20;
            --light-green: #4CAF50;
            --accent-blue: #2196F3;
            --light-gray: #f5f5f5;
            --text-dark: #333;
            --text-light: #fff;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background-color: #f9f9f9;
            color: var(--text-dark);
            line-height: 1.6;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        
        header {
            background: linear-gradient(135deg, var(--primary-green) 0%, var(--dark-green) 100%);
            color: var(--text-light);
            padding: 30px 0;
            border-radius: 0 0 20px 20px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
        }
        
        .header-content {
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
        }
        
        .logo-container {
            display: flex;
            align-items: center;
            gap: 20px;
        }
        
        .logo {
            width: 80px;
            height: 80px;
            background-color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: var(--primary-green);
            font-size: 14px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
        }
        
        .university-info h1 {
            font-size: 2.2rem;
            margin-bottom: 5px;
        }
        
        .university-info p {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        
        .main-links {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }
        
        .card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }
        
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        }
        
        .card-header {
            display: flex;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #eee;
        }
        
        .card-header i {
            font-size: 1.8rem;
            margin-right: 15px;
            color: var(--primary-green);
        }
        
        .card-header h2 {
            font-size: 1.5rem;
            color: var(--primary-green);
        }
        
        .nav-item {
            list-style: none;
            margin-bottom: 12px;
        }
        
        .nav-link {
            display: flex;
            align-items: center;
            padding: 12px 15px;
            background-color: var(--light-gray);
            border-radius: 8px;
            text-decoration: none;
            color: var(--text-dark);
            transition: all 0.3s ease;
        }
        
        .nav-link:hover {
            background-color: var(--primary-green);
            color: white;
            transform: translateX(5px);
        }
        
        .nav-link i {
            margin-right: 10px;
            width: 20px;
            text-align: center;
        }
        
        .btn {
            display: inline-block;
            padding: 12px 25px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            text-align: center;
            transition: all 0.3s ease;
            margin: 5px;
        }
        
        .btn-primary {
            background-color: var(--primary-green);
            color: white;
            border: 2px solid var(--primary-green);
        }
        
        .btn-primary:hover {
            background-color: var(--dark-green);
            border-color: var(--dark-green);
        }
        
        .btn-outline-primary {
            background-color: transparent;
            color: var(--primary-green);
            border: 2px solid var(--primary-green);
        }
        
        .btn-outline-primary:hover {
            background-color: var(--primary-green);
            color: white;
        }
        
        .btn-outline-light {
            background-color: transparent;
            color: white;
            border: 2px solid white;
        }
        
        .btn-outline-light:hover {
            background-color: white;
            color: var(--primary-green);
        }
        
        .shortcut-links {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
        }
        
        .shortcut-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05);
        }
        
        .shortcut-card h3 {
            color: var(--primary-green);
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid #eee;
        }
        
        .shortcut-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .shortcut-item {
            background: var(--light-gray);
            border-radius: 8px;
            padding: 12px 15px;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .shortcut-item:hover {
            background: var(--primary-green);
            color: white;
            transform: translateY(-3px);
        }
        
        .shortcut-item a {
            text-decoration: none;
            color: inherit;
            display: block;
            font-weight: 500;
        }
        
        .accent-blue {
            background-color: var(--accent-blue) !important;
            color: white;
        }
        
        footer {
            margin-top: 50px;
            padding: 20px 0;
            text-align: center;
            color: #777;
            border-top: 1px solid #eee;
        }
        
        @media (max-width: 768px) {
            .header-content {
                flex-direction: column;
                text-align: center;
                gap: 20px;
            }
            
            .logo-container {
                justify-content: center;
            }
            
            .main-links, .shortcut-links {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <div class="header-content">
                <div class="logo-container">
                    <div class="logo"><img src="{% static 'images/chuka.png' %}" alt="@chuka uniOS" class="logo"></div>
                    <div class="university-info">
                        <h1>Chuka University</h1>
                        <p>Automated University Timetabling System</p>
                    </div>
                </div>
                <div class="header-buttons">
                     <a class="nav-link" href="/login/">
                            Login to Staff Portal
                        </a>
                        <a href="/staff/portal/" class="btn btn-outline-primary"> Staff Portal</a>
                   
                   
                    <a href="/portal/" class="btn btn-primary"> Student Portal</a>
              
                <a class="nav-link" href="/student/portal/">
                            View Program Timetable
                        </a>
                          <a href="{% url 'export_main_exam_pdf_official' %}">Exam (PDF)</a>
                           <a href="{% url 'regular_timetabling_pdf_official' %}">Regular  Timetable (PDF)</a>
                </div>
            </div>
        </div>
    </header>
    
    <div class="container">
        <section class="main-links">
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-user-graduate"></i>
                    <h2>Students </h2>
                </div>
                <ul>
                   
                    <li class="nav-item">
                        <a class="nav-link" href="/student/portal/">
                            <i class="fas fa-sign-in-alt"></i> View Timetable
                        </a>
                    </li>
                     <!-- <li class="nav-item">
                        <a class="nav-link" href="/portal/">
                            <i class="fas fa-tachometer-alt"></i> Enter Student Portal
                        </a>
                    </li> -->
                </ul>
                <div class="text-center" style="margin-top: 20px;">
                    <a href="/portal/" class="btn btn-primary">Enter Student Portal</a>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-chalkboard-teacher"></i>
                    <h2>Staffs </h2>
                </div>
                <ul>
                    <!-- <li class="nav-item">
                        <a class="nav-link active" href="/portal/admin/">
                            <i class="fas fa-calendar-alt"></i> Timetabling Dashboard
                        </a>
                    </li> -->
                    <li class="nav-item">
                        <a class="nav-link" href="/login/">
                            <i class="fas fa-sign-in-alt"></i> Login to Staff Portal
                        </a>
                    </li>
                </ul>
                <div class="text-center" style="margin-top: 20px;">
                    <a href="/staff/portal/" class="btn btn-outline-primary">Enter Staff Portal</a>
                </div>
            </div>
            
            <!-- <div class="card">
                <div class="card-header">
                    <i class="fas fa-utensils"></i>
                    <h2>Mess Operations</h2>
                </div>
                <ul>
                    
                    <li class="nav-item">
                        <a class="nav-link" href="/mess/">
                            <i class="fas fa-concierge-bell"></i>Visit Chuka Mess 
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="/mess-admin/">
                            <i class="fas fa-utensils"></i> Mess Staff
                        </a>
                    </li>
                </ul>
            </div> -->
        </section>
        
        <section class="shortcut-links">
            <div class="shortcut-card">
                <h3>Quick Downloads</h3>
                <div class="shortcut-grid">
                    <div class="shortcut-item">
                        <a href="{% url 'export_main_exam_pdf_official' %}">Exam (PDF)</a>
                    </div>
                    <div class="shortcut-item">
                        <a href="{% url 'regular_timetabling_pdf_official' %}">Regular  Timetable (PDF)</a>
                    </div>
                    <!-- <div class="shortcut-item">
                        <a href="{% url 'export_main_pdf' %}">Styled Timetable (PDF)</a>
                    </div> -->
                </div>
            </div>
            
            <div class="shortcut-card">
                <h3>Quick Access</h3>
                <div class="shortcut-grid">
                    <div class="shortcut-item accent-blue">
                        <a href="/portal/">Student Portal</a>
                    </div>
                    <div class="shortcut-item accent-blue">
                        <a href="/staff/portal/">Staff Portal</a>
                    </div>
                    <div class="shortcut-item">
                        <a href="/mess/">Visit mess</a>
                    </div>
                    <div class="shortcut-item">
                        <a href="https://newshubke.pythonanywhere.com/">News Hub</a>
                    </div>
                    <!-- <div class="shortcut-item">
                        <a href="/">Main Portal</a>
                    </div> -->
                </div>
            </div>
        </section>
    </div>
    
    <footer>
        <div class="container">
            <p>&copy; 2023 Chuka University. All rights reserved.</p>
        </div>
    </footer>
</body>
</html>