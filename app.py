from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:  # type: ignore[no-untyped-import]
    mysql = None  # type: ignore[assignment]
    MySQLError = Exception  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "rural_education.db"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["DB_TYPE"] = os.environ.get("DB_TYPE", "sqlite").lower()
    app.config["DATABASE"] = DB_PATH
    app.config["MYSQL_HOST"] = os.environ.get("MYSQL_HOST", "127.0.0.1")
    app.config["MYSQL_PORT"] = int(os.environ.get("MYSQL_PORT", 3306))
    app.config["MYSQL_USER"] = os.environ.get("MYSQL_USER", "root")
    app.config["MYSQL_PASSWORD"] = os.environ.get("MYSQL_PASSWORD", "")
    app.config["MYSQL_DATABASE"] = os.environ.get("MYSQL_DATABASE", "rural_education")
    DB_PATH.parent.mkdir(exist_ok=True)

    @app.before_request
    def before_request() -> None:
        raw_db = connect_db(app)
        g.db = DatabaseConnection(raw_db, app.config["DB_TYPE"])
        if app.config["DB_TYPE"] == "sqlite":
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")

    @app.teardown_request
    def teardown_request(_: BaseException | None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.post("/api/auth/register")
    def register() -> Any:
        payload = request.get_json(force=True)
        required = ["name", "email", "password", "role"]
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        if payload["role"] not in {"Student", "Teacher", "Donor", "Admin"}:
            return jsonify({"error": "Invalid role"}), 400
        try:
            cursor = g.db.execute(
                """
                INSERT INTO users (name, email, password_hash, role, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload["name"].strip(),
                    payload["email"].strip().lower(),
                    generate_password_hash(payload["password"]),
                    payload["role"],
                    payload.get("location", "").strip(),
                ),
            )
            g.db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "Email already registered"}), 409
        return jsonify(user_public(cursor.lastrowid)), 201

    @app.post("/api/auth/login")
    def login() -> Any:
        payload = request.get_json(force=True)
        user = query_one("SELECT * FROM users WHERE email = ?", (payload.get("email", "").strip().lower(),))
        if user is None or not check_password_hash(user["password_hash"], payload.get("password", "")):
            return jsonify({"error": "Invalid email or password"}), 401
        log_activity(
            "login",
            {
                "user_id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"],
            },
        )
        return jsonify(user_public(user["id"]))

    @app.get("/api/dashboard")
    def dashboard() -> Any:
        user_id = request.args.get("user_id", type=int)
        user = get_user_or_none(user_id)
        if user is None:
            return jsonify({"error": "Valid user_id is required"}), 400

        total_lessons = query_one("SELECT COUNT(*) AS count FROM lessons")["count"]
        progress_count = query_one(
            "SELECT COUNT(*) AS count FROM lesson_progress WHERE user_id = ? AND completed = 1",
            (user_id,),
        )["count"]
        quiz_avg = query_one(
            "SELECT ROUND(AVG(score), 1) AS avg_score FROM lesson_progress WHERE user_id = ? AND score IS NOT NULL",
            (user_id,),
        )["avg_score"]
        stats = {
            "courses": query_one("SELECT COUNT(*) AS count FROM courses")["count"],
            "lessons": total_lessons,
            "completed_lessons": progress_count,
            "progress_percent": round((progress_count / total_lessons) * 100) if total_lessons else 0,
            "quiz_average": quiz_avg or 0,
            "donations": query_one("SELECT COUNT(*) AS count FROM donations")["count"],
            "available_resources": query_one("SELECT COALESCE(SUM(quantity), 0) AS count FROM donations WHERE status = 'Available'")["count"],
            "requests": query_one("SELECT COUNT(*) AS count FROM resource_requests")["count"],
            "notes": query_one("SELECT COUNT(*) AS count FROM notes WHERE user_id = ?", (user_id,))["count"],
        }
        recent_requests = [dict(row) for row in query_all(
            """
            SELECT rr.*, u.name AS student_name, d.title AS donation_title
            FROM resource_requests rr
            JOIN users u ON u.id = rr.user_id
            JOIN donations d ON d.id = rr.donation_id
            ORDER BY rr.created_at DESC
            LIMIT 6
            """
        )]
        return jsonify({"user": dict_without_password(user), "stats": stats, "recent_requests": recent_requests})

    @app.get("/api/courses")
    def courses() -> Any:
        rows = query_all(
            """
            SELECT c.*, COUNT(l.id) AS lesson_count
            FROM courses c
            LEFT JOIN lessons l ON l.course_id = c.id
            GROUP BY c.id
            ORDER BY c.id
            """
        )
        return jsonify([dict(row) for row in rows])

    @app.post("/api/courses")
    def create_course() -> Any:
        user = require_role({"Admin", "Teacher"})
        if not isinstance(user, dict):
            return user
        payload = request.get_json(force=True)
        required = ["title", "language", "description", "level", "duration"]
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        cursor = g.db.execute(
            """
            INSERT INTO courses (title, language, description, level, duration, accent, teacher_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["title"].strip(),
                payload["language"].strip(),
                payload["description"].strip(),
                payload["level"].strip(),
                payload["duration"].strip(),
                payload.get("accent", "#2f80ed"),
                user["id"],
            ),
        )
        g.db.commit()
        return jsonify({"id": cursor.lastrowid, "message": "Course added"}), 201

    @app.get("/api/courses/<int:course_id>/lessons")
    def lessons(course_id: int) -> Any:
        user_id = request.args.get("user_id", type=int)
        rows = query_all(
            """
            SELECT l.*, c.title AS course_title, c.language,
                   COALESCE(lp.completed, 0) AS completed,
                   lp.score
            FROM lessons l
            JOIN courses c ON c.id = l.course_id
            LEFT JOIN lesson_progress lp ON lp.lesson_id = l.id AND lp.user_id = ?
            WHERE l.course_id = ?
            ORDER BY l.position
            """,
            (user_id or -1, course_id),
        )
        return jsonify([dict(row) for row in rows])

    @app.get("/api/lessons/<int:lesson_id>")
    def lesson(lesson_id: int) -> Any:
        row = query_one(
            """
            SELECT l.*, c.title AS course_title, c.language, c.accent
            FROM lessons l
            JOIN courses c ON c.id = l.course_id
            WHERE l.id = ?
            """,
            (lesson_id,),
        )
        if row is None:
            return jsonify({"error": "Lesson not found"}), 404
        resources = query_all("SELECT * FROM lesson_resources WHERE lesson_id = ? ORDER BY id", (lesson_id,))
        result = dict(row)
        result["resources"] = [dict(resource) for resource in resources]
        return jsonify(result)

    @app.post("/api/lessons")
    def create_lesson() -> Any:
        user = require_role({"Admin", "Teacher"})
        if not isinstance(user, dict):
            return user
        payload = request.get_json(force=True)
        required = ["course_id", "title", "summary", "content", "starter_code", "challenge", "question", "answer"]
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        next_position = query_one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM lessons WHERE course_id = ?",
            (int(payload["course_id"]),),
        )["position"]
        cursor = g.db.execute(
            """
            INSERT INTO lessons
            (course_id, title, position, summary, content, starter_code, challenge, question, answer, expected_output, hint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(payload["course_id"]),
                payload["title"].strip(),
                next_position,
                payload["summary"].strip(),
                payload["content"].strip(),
                payload["starter_code"].strip(),
                payload["challenge"].strip(),
                payload["question"].strip(),
                payload["answer"].strip(),
                payload.get("expected_output", "").strip(),
                payload.get("hint", "").strip(),
            ),
        )
        g.db.commit()
        return jsonify({"id": cursor.lastrowid, "message": "Lesson added"}), 201

    @app.post("/api/enrollments")
    def enroll() -> Any:
        payload = request.get_json(force=True)
        user_id = int(payload.get("user_id", 0))
        course_id = int(payload.get("course_id", 0))
        if get_user_or_none(user_id) is None:
            return jsonify({"error": "Valid user_id is required"}), 400
        g.db.execute(
            "INSERT OR IGNORE INTO enrollments (user_id, course_id) VALUES (?, ?)",
            (user_id, course_id),
        )
        g.db.commit()
        return jsonify({"message": "Enrollment saved"})

    @app.post("/api/progress")
    def save_progress() -> Any:
        payload = request.get_json(force=True)
        user_id = int(payload.get("user_id", 0))
        lesson_id = int(payload.get("lesson_id", 0))
        lesson_row = query_one("SELECT answer, course_id FROM lessons WHERE id = ?", (lesson_id,))
        if get_user_or_none(user_id) is None or lesson_row is None:
            return jsonify({"error": "Valid user and lesson are required"}), 400
        answer = payload.get("answer", "").strip()
        is_correct = answer.lower() == lesson_row["answer"].strip().lower()
        score = 100 if is_correct else 0
        prev_progress = query_one(
            "SELECT completed FROM lesson_progress WHERE user_id = ? AND lesson_id = ?",
            (user_id, lesson_id),
        )
        g.db.execute(
            "INSERT OR IGNORE INTO enrollments (user_id, course_id) VALUES (?, ?)",
            (user_id, lesson_row["course_id"]),
        )
        if is_mysql_connection(g.db):
            g.db.execute(
                """
                INSERT INTO lesson_progress (user_id, lesson_id, completed, score, answer_text)
                VALUES (?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE completed = VALUES(completed), score = VALUES(score),
                                      answer_text = VALUES(answer_text), updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, lesson_id, 1 if is_correct else 0, score, answer),
            )
        else:
            g.db.execute(
                """
                INSERT INTO lesson_progress (user_id, lesson_id, completed, score, answer_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, lesson_id)
                DO UPDATE SET completed = excluded.completed, score = excluded.score,
                              answer_text = excluded.answer_text, updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, lesson_id, 1 if is_correct else 0, score, answer),
            )
        g.db.commit()

        if is_correct and (prev_progress is None or prev_progress["completed"] != 1):
            total_lessons = query_one(
                "SELECT COUNT(*) AS count FROM lessons WHERE course_id = ?",
                (lesson_row["course_id"],),
            )["count"]
            completed_lessons = query_one(
                "SELECT COUNT(*) AS count FROM lesson_progress WHERE user_id = ? AND completed = 1 AND lesson_id IN (SELECT id FROM lessons WHERE course_id = ?)",
                (user_id, lesson_row["course_id"]),
            )["count"]
            if total_lessons and completed_lessons == total_lessons:
                user = get_user_or_none(user_id)
                course = query_one("SELECT title FROM courses WHERE id = ?", (lesson_row["course_id"],))
                log_activity(
                    "course_completion",
                    {
                        "user_id": user_id,
                        "user_name": user["name"] if user else None,
                        "course_id": lesson_row["course_id"],
                        "course_title": course["title"] if course else None,
                        "completed_lessons": completed_lessons,
                        "total_lessons": total_lessons,
                    },
                )

        return jsonify({"correct": is_correct, "score": score, "message": "Progress saved"})

    @app.get("/api/notes")
    def get_notes() -> Any:
        user_id = request.args.get("user_id", type=int)
        lesson_id = request.args.get("lesson_id", type=int)
        if get_user_or_none(user_id) is None:
            return jsonify({"error": "Valid user_id is required"}), 400
        sql = """
            SELECT n.*, l.title AS lesson_title, c.title AS course_title
            FROM notes n
            JOIN lessons l ON l.id = n.lesson_id
            JOIN courses c ON c.id = l.course_id
            WHERE n.user_id = ?
        """
        params: list[Any] = [user_id]
        if lesson_id:
            sql += " AND n.lesson_id = ?"
            params.append(lesson_id)
        sql += " ORDER BY n.created_at DESC"
        return jsonify([dict(row) for row in query_all(sql, params)])

    @app.post("/api/notes")
    def create_note() -> Any:
        payload = request.get_json(force=True)
        required = ["user_id", "lesson_id", "highlight_text", "note_text"]
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        if get_user_or_none(int(payload["user_id"])) is None:
            return jsonify({"error": "Valid user_id is required"}), 400
        cursor = g.db.execute(
            """
            INSERT INTO notes (user_id, student_name, lesson_id, highlight_text, note_text, color)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(payload["user_id"]),
                payload.get("student_name", "").strip(),
                int(payload["lesson_id"]),
                payload["highlight_text"].strip(),
                payload["note_text"].strip(),
                payload.get("color", "#ffe08a"),
            ),
        )
        g.db.commit()
        return jsonify({"id": cursor.lastrowid, "message": "Note saved"}), 201

    @app.delete("/api/notes/<int:note_id>")
    def delete_note(note_id: int) -> Any:
        user_id = request.args.get("user_id", type=int)
        if get_user_or_none(user_id) is None:
            return jsonify({"error": "Valid user_id is required"}), 400
        cursor = g.db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        g.db.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Note not found"}), 404
        return jsonify({"message": "Note deleted"})

    @app.get("/api/donations")
    def get_donations() -> Any:
        item_type = request.args.get("item_type", "").strip()
        location = request.args.get("location", "").strip()
        search = request.args.get("search", "").strip()
        sql = "SELECT * FROM donations WHERE 1 = 1"
        params: list[Any] = []
        if item_type and item_type != "All":
            sql += " AND item_type = ?"
            params.append(item_type)
        if location:
            sql += " AND location LIKE ?"
            params.append(f"%{location}%")
        if search:
            sql += " AND (title LIKE ? OR condition_note LIKE ? OR donor_name LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        sql += " ORDER BY created_at DESC LIMIT 50"
        return jsonify([dict(row) for row in query_all(sql, params)])

    @app.post("/api/donations")
    def create_donation() -> Any:
        payload = request.get_json(force=True)
        required = ["donor_name", "item_type", "title", "quantity", "contact"]
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        cursor = g.db.execute(
            """
            INSERT INTO donations
            (donor_user_id, donor_name, item_type, title, quantity, condition_note, contact, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("donor_user_id") or None,
                payload["donor_name"].strip(),
                payload["item_type"].strip(),
                payload["title"].strip(),
                max(1, int(payload["quantity"])),
                payload.get("condition_note", "").strip(),
                payload["contact"].strip(),
                payload.get("location", "").strip(),
            ),
        )
        g.db.commit()
        return jsonify({"id": cursor.lastrowid, "message": "Donation added"}), 201

    @app.patch("/api/donations/<int:donation_id>")
    def update_donation(donation_id: int) -> Any:
        user = require_role({"Admin", "Donor"})
        if not isinstance(user, dict):
            return user
        status = request.get_json(force=True).get("status", "").strip()
        if status not in {"Available", "Assigned", "Delivered"}:
            return jsonify({"error": "Invalid status"}), 400
        g.db.execute("UPDATE donations SET status = ? WHERE id = ?", (status, donation_id))
        g.db.commit()
        return jsonify({"message": "Donation status updated"})

    @app.post("/api/resource-requests")
    def request_resource() -> Any:
        payload = request.get_json(force=True)
        user_id = int(payload.get("user_id", 0))
        donation_id = int(payload.get("donation_id", 0))
        if get_user_or_none(user_id) is None:
            return jsonify({"error": "Valid user_id is required"}), 400
        cursor = g.db.execute(
            """
            INSERT INTO resource_requests (user_id, donation_id, message)
            VALUES (?, ?, ?)
            """,
            (user_id, donation_id, payload.get("message", "").strip()),
        )
        g.db.commit()
        return jsonify({"id": cursor.lastrowid, "message": "Request submitted"}), 201

    @app.get("/api/admin")
    def admin_data() -> Any:
        user = require_role({"Admin"})
        if not isinstance(user, dict):
            return user
        return jsonify(
            {
                "users": [dict_without_password(row) for row in query_all("SELECT * FROM users ORDER BY created_at DESC")],
                "requests": [dict(row) for row in query_all(
                    """
                    SELECT rr.*, u.name AS student_name, d.title AS donation_title
                    FROM resource_requests rr
                    JOIN users u ON u.id = rr.user_id
                    JOIN donations d ON d.id = rr.donation_id
                    ORDER BY rr.created_at DESC
                    """
                )],
                "progress": [dict(row) for row in query_all(
                    """
                    SELECT u.name, c.title AS course_title, l.title AS lesson_title, lp.completed, lp.score, lp.updated_at
                    FROM lesson_progress lp
                    JOIN users u ON u.id = lp.user_id
                    JOIN lessons l ON l.id = lp.lesson_id
                    JOIN courses c ON c.id = l.course_id
                    ORDER BY lp.updated_at DESC
                    LIMIT 40
                    """
                )],
            }
        )

    init_db(app)
    return app


def get_db() -> Any:
    return g.db


def is_mysql_connection(db: Any) -> bool:
    return getattr(db, "db_type", None) == "mysql"


def ensure_mysql_database(app: Flask) -> None:
    if mysql is None:
        raise RuntimeError("MySQL driver missing. Install mysql-connector-python.")
    try:
        conn = mysql.connector.connect(
            host=app.config["MYSQL_HOST"],
            port=app.config["MYSQL_PORT"],
            user=app.config["MYSQL_USER"],
            password=app.config["MYSQL_PASSWORD"],
        )
    except MySQLError as exc:
        raise RuntimeError(
            "MySQL connection failed. Check MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_PORT, and DB_TYPE."
        ) from exc
    cursor = conn.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{app.config['MYSQL_DATABASE']}` DEFAULT CHARACTER SET utf8mb4"
    )
    conn.commit()
    cursor.close()
    conn.close()


def connect_db(app: Flask) -> Any:
    if app.config["DB_TYPE"] == "mysql":
        if mysql is None:
            raise RuntimeError("MySQL driver missing. Install mysql-connector-python.")
        ensure_mysql_database(app)
        try:
            return mysql.connector.connect(
                host=app.config["MYSQL_HOST"],
                port=app.config["MYSQL_PORT"],
                user=app.config["MYSQL_USER"],
                password=app.config["MYSQL_PASSWORD"],
                database=app.config["MYSQL_DATABASE"],
            )
        except MySQLError as exc:
            raise RuntimeError(
                "MySQL connection failed. Check MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_PORT, and DB_TYPE."
            ) from exc
    return sqlite3.connect(app.config["DATABASE"])


def log_activity(event_name: str, details: dict[str, Any]) -> None:
    db = get_db()
    details_json = json.dumps(details, ensure_ascii=False)
    db.execute(
        "INSERT INTO activity_log (event_name, user_id, course_id, details) VALUES (?, ?, ?, ?)",
        (
            event_name,
            details.get("user_id"),
            details.get("course_id"),
            details_json,
        ),
    )
    db.commit()


class DatabaseConnection:
    def __init__(self, conn: Any, db_type: str) -> None:
        object.__setattr__(self, "conn", conn)
        object.__setattr__(self, "db_type", db_type)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"conn", "db_type"}:
            object.__setattr__(self, name, value)
        elif name == "row_factory" and self.db_type == "sqlite":
            self.conn.row_factory = value
        else:
            setattr(self.conn, name, value)

    def execute(self, sql: str, params: Any = ()) -> Any:
        if self.db_type == "mysql":
            sql = sql.replace("?", "%s").replace("INSERT OR IGNORE", "INSERT IGNORE")
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            return cursor
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: Any) -> Any:
        if self.db_type == "mysql":
            sql = sql.replace("?", "%s").replace("INSERT OR IGNORE", "INSERT IGNORE")
            cursor = self.conn.cursor(dictionary=True)
            cursor.executemany(sql, params_seq)
            return cursor
        return self.conn.executemany(sql, params_seq)

    def cursor(self, dictionary: bool = False) -> Any:
        if self.db_type == "mysql":
            return self.conn.cursor(dictionary=dictionary)
        return self.conn.cursor()


def query_all(sql: str, params: Any = ()) -> list[dict[str, Any]]:
    return get_db().execute(sql, params).fetchall()


def query_one(sql: str, params: Any = ()) -> dict[str, Any] | None:
    return get_db().execute(sql, params).fetchone()


def dict_without_password(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data.pop("password_hash", None)
    return data


def user_public(user_id: int) -> dict[str, Any]:
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if row is None:
        return {}
    return dict_without_password(row)


def get_user_or_none(user_id: int | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def require_role(roles: set[str]) -> dict[str, Any] | Any:
    user_id = request.args.get("user_id", type=int) or (request.get_json(silent=True) or {}).get("user_id")
    user = get_user_or_none(int(user_id or 0))
    if user is None:
        return jsonify({"error": "Valid user_id is required"}), 400
    if user["role"] not in roles:
        return jsonify({"error": "Permission denied"}), 403
    return user


def init_db(app: Flask) -> None:
    with app.app_context():
        raw_db = connect_db(app)
        db = DatabaseConnection(raw_db, app.config["DB_TYPE"])
        if app.config["DB_TYPE"] == "sqlite":
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
        create_schema(db)
        migrate_existing(db)
        seed_database(db)
        db.close()


def create_schema(db: Any) -> None:
    if is_mysql_connection(db):
        cursor = db.cursor()
        mysql_statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Student', 'Teacher', 'Donor', 'Admin')),
                location TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS courses (
                id INT PRIMARY KEY AUTO_INCREMENT,
                title TEXT NOT NULL,
                language TEXT NOT NULL,
                description TEXT NOT NULL,
                level TEXT NOT NULL,
                duration TEXT NOT NULL,
                accent TEXT NOT NULL,
                teacher_id INT,
                FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS lessons (
                id INT PRIMARY KEY AUTO_INCREMENT,
                course_id INT NOT NULL,
                title TEXT NOT NULL,
                position INT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL,
                starter_code TEXT NOT NULL,
                challenge TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                expected_output TEXT DEFAULT '',
                hint TEXT DEFAULT '',
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS lesson_resources (
                id INT PRIMARY KEY AUTO_INCREMENT,
                lesson_id INT NOT NULL,
                title TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                url TEXT,
                FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS enrollments (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                course_id INT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS lesson_progress (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                lesson_id INT NOT NULL,
                completed INT NOT NULL DEFAULT 0,
                score INT,
                answer_text TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, lesson_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT,
                student_name TEXT,
                lesson_id INT NOT NULL,
                highlight_text TEXT NOT NULL,
                note_text TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#ffe08a',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS donations (
                id INT PRIMARY KEY AUTO_INCREMENT,
                donor_user_id INT,
                donor_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                title TEXT NOT NULL,
                quantity INT NOT NULL,
                condition_note TEXT,
                contact TEXT NOT NULL,
                location TEXT,
                status TEXT NOT NULL DEFAULT 'Available',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (donor_user_id) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS resource_requests (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                donation_id INT NOT NULL,
                message TEXT,
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (donation_id) REFERENCES donations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INT PRIMARY KEY AUTO_INCREMENT,
                teacher_id INT,
                course_id INT NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INT PRIMARY KEY AUTO_INCREMENT,
                event_name TEXT NOT NULL,
                user_id INT,
                course_id INT,
                details TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
        ]
        for statement in mysql_statements:
            cursor.execute(statement)
        db.commit()
        cursor.close()
        return

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Student', 'Teacher', 'Donor', 'Admin')),
            location TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            description TEXT NOT NULL,
            level TEXT NOT NULL,
            duration TEXT NOT NULL,
            accent TEXT NOT NULL,
            teacher_id INTEGER,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            position INTEGER NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL,
            starter_code TEXT NOT NULL,
            challenge TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            expected_output TEXT DEFAULT '',
            hint TEXT DEFAULT '',
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS lesson_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            url TEXT,
            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, course_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS lesson_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            lesson_id INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            score INTEGER,
            answer_text TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, lesson_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            student_name TEXT,
            lesson_id INTEGER NOT NULL,
            highlight_text TEXT NOT NULL,
            note_text TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#ffe08a',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_user_id INTEGER,
            donor_name TEXT NOT NULL,
            item_type TEXT NOT NULL,
            title TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            condition_note TEXT,
            contact TEXT NOT NULL,
            location TEXT,
            status TEXT NOT NULL DEFAULT 'Available',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (donor_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS resource_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            donation_id INTEGER NOT NULL,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (donation_id) REFERENCES donations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            instructions TEXT NOT NULL,
            due_date TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            user_id INTEGER,
            course_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
        );
        """
    )
    db.commit()


def migrate_existing(db: Any) -> None:
    if is_mysql_connection(db):
        return

    def add_column(table: str, column: str, definition: str) -> None:
        columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    add_column("courses", "teacher_id", "INTEGER")
    add_column("lessons", "expected_output", "TEXT DEFAULT ''")
    add_column("lessons", "hint", "TEXT DEFAULT ''")
    add_column("notes", "user_id", "INTEGER")
    add_column("notes", "student_name", "TEXT")
    add_column("donations", "donor_user_id", "INTEGER")
    db.commit()


def seed_database(db: Any) -> None:
    row = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    if row and row["count"] == 0:
        seed_users(db)

    row = db.execute("SELECT COUNT(*) AS count FROM courses").fetchone()
    existing_courses = row["count"] if row else 0
    if existing_courses < 4:
        db.execute("DELETE FROM lesson_resources")
        db.execute("DELETE FROM lessons")
        db.execute("DELETE FROM courses")
        if is_mysql_connection(db):
            cursor = db.cursor()
            cursor.execute("ALTER TABLE courses AUTO_INCREMENT = 1")
            cursor.execute("ALTER TABLE lessons AUTO_INCREMENT = 1")
            cursor.execute("ALTER TABLE lesson_resources AUTO_INCREMENT = 1")
            cursor.close()
        else:
            db.execute("DELETE FROM sqlite_sequence WHERE name IN ('courses', 'lessons', 'lesson_resources')")
        seed_courses_and_lessons(db)

    row = db.execute("SELECT COUNT(*) AS count FROM donations").fetchone()
    if row and row["count"] < 6:
        seed_donations(db)

    row = db.execute("SELECT COUNT(*) AS count FROM assignments").fetchone()
    if row and row["count"] == 0:
        teacher = db.execute("SELECT id FROM users WHERE role = 'Teacher' LIMIT 1").fetchone()
        if teacher:
            db.executemany(
                """
                INSERT INTO assignments (teacher_id, course_id, title, instructions, due_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (teacher["id"], 1, "Python Practice Set", "Solve five variable and loop questions in your notebook.", "2026-06-15"),
                    (teacher["id"], 2, "Build a Profile Page", "Create one HTML page with a heading, image placeholder, list, and button.", "2026-06-20"),
                ],
            )
    db.commit()


def seed_users(db: Any) -> None:
    users = [
        ("Demo Student", "student@example.com", "student123", "Student", "Hassan"),
        ("Meera Teacher", "teacher@example.com", "teacher123", "Teacher", "Mysuru"),
        ("Asha Donor", "donor@example.com", "donor123", "Donor", "Mandya"),
        ("Admin User", "admin@example.com", "admin123", "Admin", "Bengaluru"),
    ]
    db.executemany(
        """
        INSERT INTO users (name, email, password_hash, role, location)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(name, email, generate_password_hash(password), role, location) for name, email, password, role, location in users],
    )


def seed_courses_and_lessons(db: Any) -> None:
    teacher_id = db.execute("SELECT id FROM users WHERE role = 'Teacher' LIMIT 1").fetchone()["id"]
    courses = [
        ("Python Foundations", "Python", "Learn variables, decisions, loops, functions, lists, and files through practical village-school examples.", "Beginner", "6 weeks", "#2f80ed", teacher_id),
        ("Web Basics", "HTML, CSS, JavaScript", "Build accessible pages, responsive layouts, forms, and interactive browser features.", "Beginner", "6 weeks", "#00a676", teacher_id),
        ("Java Problem Solving", "Java", "Understand methods, classes, arrays, input, and object-oriented thinking used in school and college projects.", "Intermediate", "7 weeks", "#f2994a", teacher_id),
        ("Database Skills", "SQL and DBMS", "Learn tables, keys, relationships, SQL queries, joins, and simple reporting for real projects.", "Beginner", "5 weeks", "#9b51e0", teacher_id),
    ]
    db.executemany(
        """
        INSERT INTO courses (title, language, description, level, duration, accent, teacher_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        courses,
    )

    lessons = [
        (1, 1, "Variables and Data Types", "Store text, numbers, and truth values.", "A variable is a named place for a value. Python chooses the data type from the value you assign: strings hold text, integers hold whole numbers, floats hold decimal numbers, and booleans hold True or False. Clear names make programs easier to read and debug.", "student = 'Anu'\nbooks = 4\nattendance = 92.5\nis_present = True\nprint(student, books, attendance, is_present)", "Create variables for a student's name, grade, and whether fees are paid.", "Which Python type stores True or False?", "boolean", "boolean", "It starts with b."),
        (1, 2, "Input and Output", "Read user input and print helpful messages.", "The input function reads text typed by a user. Because input returns text, convert it with int or float before doing math. Printing clear messages helps first-time users understand what the program needs.", "name = input('Student name: ')\nmarks = int(input('Marks: '))\nprint(name, 'scored', marks)", "Ask for a book title and print a borrowed-book message.", "Which function reads keyboard input in Python?", "input", "input", "It is also the common word for data given to a system."),
        (1, 3, "Conditions", "Make decisions with if, elif, and else.", "Conditional statements let a program choose a path. Use if for the first test, elif for extra tests, and else for the fallback. Comparisons such as >=, ==, and != create True or False results.", "marks = 68\nif marks >= 35:\n    print('Pass')\nelse:\n    print('Needs practice')", "Check whether a student is eligible for a scholarship when marks are 75 or more.", "Which keyword handles the fallback case?", "else", "Needs practice", "It pairs with if."),
        (1, 4, "Loops", "Repeat work over ranges and lists.", "Loops reduce repeated code. A for loop works well for known collections such as a list of books. The range function creates number sequences, which is useful for counters and repeated practice.", "for number in range(1, 6):\n    print(number)", "Print all subjects in a list using a for loop.", "Which function creates a sequence of numbers?", "range", "1 2 3 4 5", "It means distance between start and end."),
        (1, 5, "Functions", "Name reusable steps with parameters.", "A function groups code under a meaningful name. Parameters are values passed into a function. Returning a value is better than only printing when another part of the program needs the result.", "def average(a, b):\n    return (a + b) / 2\nprint(average(70, 80))", "Create a function that returns the total of three marks.", "Which keyword sends a value back from a function?", "return", "75.0", "It means give back."),
        (1, 6, "Lists and Dictionaries", "Organize many values and labelled details.", "Lists store ordered values and dictionaries store key-value pairs. Use lists for many book titles and dictionaries for one student's labelled details such as name, grade, and village.", "student = {'name': 'Ravi', 'grade': 8}\nbooks = ['Math', 'Science']\nprint(student['name'], books[0])", "Store a student profile in a dictionary and print the name.", "Which bracket type commonly creates a Python list?", "square", "Ravi Math", "Lists use [ and ]."),
        (2, 1, "Semantic HTML", "Use tags that describe meaning.", "Semantic HTML helps browsers, search engines, and screen readers understand a page. Use header, main, section, article, nav, button, and form when the meaning matches the content.", "<main>\n  <section>\n    <h1>Learning Hub</h1>\n    <p>Free programming classes.</p>\n  </section>\n</main>", "Create a section for recent donations with a heading.", "Which tag should wrap the main unique page content?", "main", "Learning Hub", "It means the central content."),
        (2, 2, "Forms and Labels", "Collect data accessibly.", "Every input should have a label so users and assistive technologies know what to enter. Required fields, clear placeholders, and useful validation messages make forms easier to complete.", "<label for='book'>Book title</label>\n<input id='book' name='book' required>", "Build inputs for donor name and contact number with labels.", "Which element names an input for users?", "label", "", "It describes the field."),
        (2, 3, "CSS Layout", "Create responsive layouts with grid and flexbox.", "Flexbox is strong for one-dimensional rows or columns. CSS Grid is strong for two-dimensional page areas. Responsive layouts use percentages, minmax, and media queries so phones, tablets, and laptops all work well.", ".cards {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));\n  gap: 16px;\n}", "Make a responsive grid of course cards.", "Which CSS layout system is best for rows and columns together?", "grid", "", "It is the two-dimensional layout tool."),
        (2, 4, "JavaScript Events", "React to clicks, typing, and form submits.", "Events are browser signals caused by user actions. addEventListener connects an element to a function, which keeps JavaScript organized and avoids mixing behavior into HTML attributes.", "const button = document.querySelector('button');\nbutton.addEventListener('click', () => {\n  console.log('Started');\n});", "Show a message when a Start button is clicked.", "Which method attaches an event handler?", "addEventListener", "Started", "It contains the word listener."),
        (2, 5, "Fetch API", "Load and save data with a backend.", "The Fetch API lets browser JavaScript call backend routes. Use GET to read data and POST to create data. JSON is a common format for sending structured information between frontend and backend.", "fetch('/api/courses')\n  .then(response => response.json())\n  .then(data => console.log(data));", "Fetch donations from an API and display their titles.", "Which data format is commonly used by APIs?", "json", "", "It looks like JavaScript objects."),
        (2, 6, "Accessible Interfaces", "Design for keyboard and screen-reader users.", "Accessible sites use good contrast, visible focus styles, labels, alt text, keyboard-friendly buttons, and logical heading order. Accessibility improves the site for everyone, including learners using low-cost devices.", "<button aria-label='Open menu'>Menu</button>", "Add an aria-label to an icon-only button.", "Which attribute can describe an icon-only button?", "aria-label", "", "It starts with aria."),
        (3, 1, "Java Program Structure", "Understand class, main, and statements.", "A Java program usually starts in the main method. Statements end with semicolons. Java is statically typed, so variable types such as int, double, and String must be declared.", "public class Main {\n  public static void main(String[] args) {\n    System.out.println(\"Hello\");\n  }\n}", "Write a program that prints your school name.", "Which method starts a basic Java program?", "main", "Hello", "It is public static void."),
        (3, 2, "Variables and Types", "Declare typed values in Java.", "Java variables need a declared type. Use int for whole numbers, double for decimals, boolean for true or false, char for one character, and String for text.", "String name = \"Anu\";\nint grade = 8;\nboolean present = true;", "Create variables for book title and quantity.", "Which Java type stores text?", "String", "", "It starts with uppercase S."),
        (3, 3, "Methods", "Reuse named actions.", "Methods are named blocks of code. Parameters receive values, and return types describe what value comes back. Use void when a method performs an action without returning a value.", "static int total(int a, int b) {\n  return a + b;\n}", "Create a method that returns the average of two marks.", "Which keyword returns a value from a method?", "return", "", "Same word as Python."),
        (3, 4, "Arrays and Loops", "Process groups of values.", "Arrays store fixed-size groups of same-type values. Loops are often used to visit every item in an array, such as marks or attendance numbers.", "int[] marks = {70, 82, 91};\nfor (int mark : marks) {\n  System.out.println(mark);\n}", "Print every book count from an int array.", "Which symbol pair is used in Java array types?", "[]", "", "Two square brackets."),
        (3, 5, "Classes and Objects", "Model real things in code.", "A class is a blueprint and an object is a specific instance. Fields store data, and methods describe behavior. In this system, Student, Course, and Donation are natural class examples.", "class Student {\n  String name;\n  int grade;\n}", "Create a Book class with title and subject fields.", "An object is created from which blueprint?", "class", "", "The blueprint keyword."),
        (3, 6, "Input with Scanner", "Read typed values in Java.", "Scanner reads input from the keyboard. Import it from java.util, create a Scanner object, and use methods such as nextLine and nextInt for different data types.", "import java.util.Scanner;\nScanner sc = new Scanner(System.in);\nString name = sc.nextLine();", "Read a student's name and grade using Scanner.", "Which class reads keyboard input in Java?", "Scanner", "", "It scans input."),
        (4, 1, "Tables and Records", "Store related data in rows and columns.", "A database table stores one type of thing, such as students, courses, or donations. Each row is a record, and each column stores one attribute. Good table names are clear and plural.", "CREATE TABLE students (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  grade INTEGER\n);", "Create a books table with id, title, and subject.", "What is one row in a table called?", "record", "", "Another word is tuple."),
        (4, 2, "Primary and Foreign Keys", "Connect tables safely.", "A primary key uniquely identifies a row. A foreign key stores the primary key of another table to create a relationship. Keys prevent confusion when names repeat.", "CREATE TABLE enrollments (\n  student_id INTEGER,\n  course_id INTEGER,\n  FOREIGN KEY (student_id) REFERENCES students(id)\n);", "Design a donation request table with student_id and donation_id.", "Which key uniquely identifies a row?", "primary key", "", "It is the main key."),
        (4, 3, "SELECT Queries", "Read exactly the data you need.", "SELECT statements retrieve data. WHERE filters rows, ORDER BY sorts results, and LIMIT controls how many rows return. These clauses make reports fast and useful.", "SELECT title, quantity\nFROM donations\nWHERE status = 'Available'\nORDER BY created_at DESC;", "Find all available books from the donations table.", "Which SQL clause filters rows?", "where", "", "It asks where a condition is true."),
        (4, 4, "INSERT, UPDATE, DELETE", "Change database records carefully.", "INSERT creates rows, UPDATE changes existing rows, and DELETE removes rows. Always use a WHERE clause with UPDATE or DELETE unless you truly mean every row.", "UPDATE donations\nSET status = 'Delivered'\nWHERE id = 3;", "Update a requested resource to Assigned.", "Which SQL command creates a new row?", "insert", "", "It means add."),
        (4, 5, "Joins", "Combine related tables.", "A JOIN combines rows from related tables. Inner joins return matches from both tables. Joins power reports such as student name with course title or donation title with requester name.", "SELECT users.name, courses.title\nFROM enrollments\nJOIN users ON users.id = enrollments.user_id\nJOIN courses ON courses.id = enrollments.course_id;", "Join requests with users to show requester names.", "Which SQL operation combines related tables?", "join", "", "It means connect."),
        (4, 6, "Reports and Dashboards", "Turn data into useful decisions.", "Dashboards summarize counts, averages, and recent activity. Useful reports for this project include lesson completion, available books, pending requests, and quiz averages.", "SELECT COUNT(*) AS total_available\nFROM donations\nWHERE status = 'Available';", "Count completed lessons for a student.", "Which SQL function counts rows?", "count", "", "It returns how many."),
        (2, 7, "MySQL Fundamentals for Web Apps", "Understand the role of MySQL in web development.", "MySQL is a common relational database used with web applications. It stores tables of data such as users, courses, donations, and requests. Web frontends access MySQL data through backend APIs, not directly.", "CREATE TABLE users (\n  id INT AUTO_INCREMENT PRIMARY KEY,\n  name VARCHAR(100),\n  email VARCHAR(100)\n);", "Explain why a browser cannot connect directly to MySQL.", "Which layer connects the browser to MySQL?", "backend", "", "The browser uses APIs, not direct database connections."),
        (2, 8, "Frontend Forms to MySQL", "Send form data safely from the browser to the database.", "A frontend form gathers user input and sends it to the server as JSON or form data. The server then uses SQL INSERT or UPDATE statements to store that data in MySQL. This separation keeps the database secure and makes the web app maintainable.", "<form id='donation-form'>...</form>\nconst formData = {item:'Books'};", "Describe how a donation form reaches the database.", "Which method sends data to a backend API?", "POST", "", "It is used to create new resources."),
        (2, 9, "AJAX and MySQL-backed APIs", "Load database results into the browser with AJAX.", "AJAX calls use fetch or XHR to request JSON from a backend route. That backend route can run MySQL queries and return structured results. The frontend then updates the page with data such as course lists, donation status, or student progress.", "fetch('/api/courses').then(r => r.json()).then(data => console.log(data));", "Which API method requests data from the server?", "fetch", "", "It is also a JavaScript function."),
    ]
    db.executemany(
        """
        INSERT INTO lessons
        (course_id, position, title, summary, content, starter_code, challenge, question, answer, expected_output, hint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        lessons,
    )
    lesson_ids = [row["id"] for row in db.execute("SELECT id FROM lessons").fetchall()]
    resources = []
    for lesson_id in lesson_ids:
        resources.append((lesson_id, "Revision notes", "PDF", ""))
        resources.append((lesson_id, "Practice activity", "Worksheet", ""))
    db.executemany(
        "INSERT INTO lesson_resources (lesson_id, title, resource_type, url) VALUES (?, ?, ?, ?)",
        resources,
    )


def seed_donations(db: Any) -> None:
    db.execute("DELETE FROM donations")
    donor = db.execute("SELECT id FROM users WHERE role = 'Donor' LIMIT 1").fetchone()
    donor_id = donor["id"] if donor else None
    donations = [
        (donor_id, "Asha Foundation", "Books", "Class 10 Science Guides", 24, "Good condition, latest syllabus", "asha@example.org", "Mysuru", "Available"),
        (donor_id, "Kiran Kumar", "Device", "Used Android Tablets", 5, "Working, chargers included", "9876543210", "Mandya", "Assigned"),
        (donor_id, "Bright Path Library", "Stationery", "Notebooks and Geometry Boxes", 60, "New", "library@example.org", "Hassan", "Available"),
        (donor_id, "Code Circle", "Books", "Python Beginner Books", 18, "Beginner friendly programming books", "code@example.org", "Tumakuru", "Available"),
        (donor_id, "Local College NSS", "Internet Support", "Monthly Data Recharge Coupons", 30, "For online classes and coding practice", "nss@example.org", "Mysuru", "Available"),
        (donor_id, "Sahana Trust", "Device", "Refurbished Laptops", 3, "Suitable for web and Python practice", "sahana@example.org", "Bengaluru Rural", "Delivered"),
    ]
    db.executemany(
        """
        INSERT INTO donations
        (donor_user_id, donor_name, item_type, title, quantity, condition_note, contact, location, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        donations,
    )


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
