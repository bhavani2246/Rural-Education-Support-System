# Rural Education Support System

A full-stack database-driven web application designed to improve access to quality education for students in rural and low-income communities. This system bridges the gap between educational resources and underprivileged students by managing learning content, donations, and student progress efficiently.

## 🚀 Features

* Responsive frontend built with HTML, CSS, and JavaScript (mobile-friendly)
* Python Flask backend with SQL database integration
* Supports both SQLite (default) and MySQL databases
* Role-based authentication:

  * Student
  * Teacher
  * Donor
  * Admin
* Interactive programming courses with beginner-friendly lessons
* Quiz-based learning and progress tracking
* Student enrollment and performance monitoring
* Book and learning-resource donation system
* Resource search, request, and approval workflow
* Notes and sentence highlighting saved to database
* Teacher lesson upload functionality
* Admin dashboard with reports and analytics
* Dark mode and accessibility options

## 🛠️ Tech Stack

* Frontend: HTML, CSS, JavaScript
* Backend: Python (Flask)
* Database: SQLite / MySQL

## ▶️ How to Run

```bash
python -m pip install -r requirements.txt
python app.py
```

Open in browser:

```
http://127.0.0.1:5000
```

Default database:

```
instance/rural_education.db
```

## 🗄️ MySQL Configuration (Optional)

Set environment variable:

```
DB_TYPE=mysql
```

Then configure:

* MYSQL_HOST (default: 127.0.0.1)
* MYSQL_PORT (default: 3306)
* MYSQL_USER
* MYSQL_PASSWORD
* MYSQL_DATABASE

The database will be created automatically if it does not exist.

## 🔑 Demo Accounts

```
Student: student@example.com / student123
Teacher: teacher@example.com / teacher123
Donor: donor@example.com / donor123
Admin: admin@example.com / admin123
```

## 🎯 Objective

To provide an accessible, scalable, and efficient platform that empowers rural students through digital education and resource sharing.
