# Rural Education System

A full-stack DBMS website for rural and low-income students. It provides interactive programming classes, book/resource donation tracking, and saved notes/highlights.

## Features

- Responsive frontend built with HTML, CSS, and JavaScript for mobile phones, tablets, and laptops
- Python Flask backend with a SQL database
- Default database is SQLite; optional MySQL support is available via `DB_TYPE=mysql`
- Login/register with Student, Teacher, Donor, and Admin roles
- Dashboard with DBMS reports and progress statistics
- Seeded programming courses and accurate beginner-friendly lessons
- Interactive quiz-style lesson checks
- Student enrollment and lesson progress tracking
- Book and learning-resource donation form
- Donation search, filters, resource requests, and status updates
- Student notes and sentence highlights saved to the database
- Teacher lesson upload form
- Admin report for users, requests, and progress
- Dark mode and larger text accessibility controls

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The default database is SQLite and is created automatically at `instance/rural_education.db`.

### MySQL support

Set `DB_TYPE=mysql` to use a MySQL database and provide connection details with:

- `MYSQL_HOST` (default `127.0.0.1`)
- `MYSQL_PORT` (default `3306`)
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

The app will create the MySQL database automatically if it does not exist.

## Demo Accounts

Use these accounts to test each role:

```text
Student: student@example.com / student123
Teacher: teacher@example.com / teacher123
Donor: donor@example.com / donor123
Admin: admin@example.com / admin123
```
