import os
import base64
from io import BytesIO
from datetime import date, datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from PIL import Image
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_RAILWAY")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "CHANGE_ME_IN_RAILWAY")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///yamuna_mess_local.db"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    roll TEXT UNIQUE NOT NULL,
    phone TEXT,
    course TEXT,
    photo_data TEXT,
    username TEXT UNIQUE,
    password_hash TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS menus (
    id SERIAL PRIMARY KEY,
    menu_date DATE NOT NULL,
    meal TEXT NOT NULL,
    item TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    menu_date DATE NOT NULL,
    meal TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, menu_date, meal)
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    amount NUMERIC(12,2) NOT NULL,
    paid_date DATE NOT NULL,
    note TEXT
);
"""

def init_db():
    with engine.begin() as c:
        if DATABASE_URL.startswith("sqlite"):
            c.execute(text("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    roll TEXT UNIQUE NOT NULL,
                    phone TEXT,
                    course TEXT,
                    photo_data TEXT,
                    username TEXT UNIQUE,
                    password_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS menus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    menu_date DATE NOT NULL,
                    meal TEXT NOT NULL,
                    item TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    menu_date DATE NOT NULL,
                    meal TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, menu_date, meal)
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    amount NUMERIC(12,2) NOT NULL,
                    paid_date DATE NOT NULL,
                    note TEXT
                );
            """))
        else:
            c.execute(text(SCHEMA))
        # Safe migration for existing Railway/PostgreSQL or local databases.
        # IMPORTANT: use IF NOT EXISTS so one already-present column does not
        # abort the PostgreSQL transaction and prevent later columns from being added.
        for stmt in [
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_data TEXT",
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS username TEXT",
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS password_hash TEXT"
        ]:
            c.execute(text(stmt))
        # Keep existing usernames intact. For old students missing credentials,
        # create a compatible login using roll as username and mobile (or roll) as password.
        try:
            rows = c.execute(text("SELECT id, roll, phone, username, password_hash FROM students")).mappings().all()
            for r in rows:
                if not r["username"]:
                    c.execute(text("UPDATE students SET username=:u WHERE id=:id"), {
                        "u": r["roll"], "id": r["id"]
                    })
                if not r["password_hash"]:
                    c.execute(text("UPDATE students SET password_hash=:p WHERE id=:id"), {
                        "p": generate_password_hash(r["phone"] or r["roll"]), "id": r["id"]
                    })
        except Exception:
            pass

init_db()

def save_photo(file):
    if not file or not getattr(file, "filename", ""):
        return None
    try:
        img = Image.open(file.stream).convert("RGB")
        img.thumbnail((500, 500))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=78, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None

def whatsapp_notify(student, meal):
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    api_version = os.environ.get("WHATSAPP_API_VERSION", "v23.0").strip()
    template_name = os.environ.get("WHATSAPP_ATTENDANCE_TEMPLATE", "").strip()
    if not token or not phone_number_id or not template_name or not student.get("phone"):
        return False, "WhatsApp API not configured"
    phone = "".join(ch for ch in str(student["phone"]) if ch.isdigit())
    if len(phone) == 10:
        phone = "91" + phone
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "mr")},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": str(student["name"])},
                {"type": "text", "text": str(meal)}
            ]}]
        }
    }
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=12)
        if r.ok:
            return True, "sent"
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)[:300]

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper

@app.context_processor
def common():
    return {
        "today": date.today().isoformat(),
        "admin": session.get("admin"),
        "student_logged_in": bool(session.get("student_id"))
    }

@app.route("/")
def home():
    if session.get("admin"):
        return redirect(url_for("dashboard"))
    if session.get("student_id"):
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        roll = request.form["roll"].strip()
        phone = request.form.get("phone", "").strip()
        course = request.form.get("course", "").strip()
        photo_data = save_photo(request.files.get("photo"))
        username = request.form.get("username", "").strip() or roll
        password = request.form.get("password", "").strip()
        if not password:
            flash("Password आवश्यक आहे. / Password is required.")
            return render_template("register.html")

        try:
            with engine.begin() as c:
                c.execute(text("""
                    INSERT INTO students(name, roll, phone, course, photo_data, username, password_hash)
                    VALUES(:name, :roll, :phone, :course, :photo_data, :username, :password_hash)
                """), {
                    "name": name, "roll": roll, "phone": phone,
                    "course": course, "photo_data": photo_data,
                    "username": username, "password_hash": generate_password_hash(password)
                })
            flash("Registration successful. / नोंदणी यशस्वी झाली.")
            return redirect(url_for("login"))
        except IntegrityError:
            flash("Roll No already exists. / Roll No आधीच आहे.")

    return render_template("register.html")

@app.route("/student", methods=["GET"])
def student():
    if not session.get("student_id"):
        return redirect(url_for("student_login"))
    return redirect(url_for("student_dashboard"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role", "student")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if role == "admin":
            if username == ADMIN_USER and password == ADMIN_PASS:
                session.clear(); session["admin"] = True
                return redirect(url_for("dashboard"))
            flash("Admin username/password चुकीचे आहेत.")
        else:
            with engine.begin() as c:
                s = c.execute(text("""
                    SELECT id, password_hash FROM students
                    WHERE LOWER(COALESCE(username, ''))=LOWER(:u)
                       OR LOWER(roll)=LOWER(:u)
                    LIMIT 1
                """), {"u": username}).mappings().first()
            if s and s["password_hash"] and check_password_hash(s["password_hash"], password):
                session.clear(); session["student_id"] = s["id"]
                return redirect(url_for("student_dashboard"))
            flash("Student username/password चुकीचे आहेत.")
    return render_template("login.html")

@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    return redirect(url_for("login"))

@app.get("/student/logout")
def student_logout():
    session.pop("student_id", None)
    return redirect(url_for("home"))

@app.get("/student/dashboard")
def student_dashboard():
    sid = session.get("student_id")
    if not sid:
        return redirect(url_for("student_login"))
    today = date.today()
    month_start = today.replace(day=1)
    with engine.begin() as c:
        student = c.execute(text("SELECT * FROM students WHERE id=:id"), {"id": sid}).mappings().first()
        if not student:
            session.pop("student_id", None)
            return redirect(url_for("student_login"))
        attendance = c.execute(text("""
            SELECT menu_date, meal, created_at FROM attendance
            WHERE student_id=:sid ORDER BY menu_date DESC, meal
        """), {"sid": sid}).mappings().all()
        month_counts = c.execute(text("""
            SELECT COUNT(*) AS total, COUNT(DISTINCT menu_date) AS days
            FROM attendance WHERE student_id=:sid AND menu_date >= :start AND menu_date <= :end
        """), {"sid": sid, "start": month_start, "end": today}).mappings().first()
        today_meals = set(c.execute(text("""
            SELECT meal FROM attendance WHERE student_id=:sid AND menu_date=:d
        """), {"sid": sid, "d": today}).scalars().all())
        menus = c.execute(text("""
            SELECT menu_date, meal, item FROM menus
            WHERE menu_date >= :d ORDER BY menu_date ASC,
            CASE meal WHEN 'Breakfast' THEN 1 WHEN 'Lunch' THEN 2 WHEN 'Evening Snacks' THEN 3 WHEN 'Dinner' THEN 4 ELSE 5 END
            LIMIT 20
        """), {"d": today}).mappings().all()
        payments = c.execute(text("""
            SELECT amount, paid_date, note FROM payments
            WHERE student_id=:sid ORDER BY paid_date DESC, id DESC
        """), {"sid": sid}).mappings().all()
        paid_total = c.execute(text("SELECT COALESCE(SUM(amount),0) FROM payments WHERE student_id=:sid"), {"sid": sid}).scalar()
    return render_template("student_dashboard.html", student=student, attendance=attendance,
                           month_counts=month_counts, today_meals=today_meals, menus=menus,
                           payments=payments, paid_total=paid_total, month_name=today.strftime("%B %Y"))

@app.post("/attendance")
def mark_attendance():
    roll = request.form["roll"]
    meal = request.form["meal"]

    with engine.begin() as c:
        s = c.execute(
            text("SELECT id FROM students WHERE roll=:roll"),
            {"roll": roll}
        ).mappings().first()

        if s:
            try:
                c.execute(text("""
                    INSERT INTO attendance(student_id, menu_date, meal)
                    VALUES(:sid, :d, :meal)
                """), {
                    "sid": s["id"],
                    "d": date.today(),
                    "meal": meal
                })
            except IntegrityError:
                pass

    return redirect(url_for("student", roll=roll))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    return redirect(url_for("login"))

@app.get("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/admin")
@admin_required
def dashboard():
    q = request.args.get("q", "").strip()
    with engine.begin() as c:
        students = c.execute(text("""
            SELECT s.*, COALESCE(SUM(p.amount),0) AS paid_total
            FROM students s LEFT JOIN payments p ON p.student_id=s.id
            GROUP BY s.id ORDER BY s.id DESC
        """)).mappings().all()

        counts = {}
        for meal in ["Breakfast", "Lunch", "Evening Snacks", "Dinner"]:
            counts[meal] = c.execute(text("""
                SELECT COUNT(*) FROM attendance
                WHERE menu_date=:d AND meal=:meal
            """), {"d": date.today(), "meal": meal}).scalar()

        today_attendance = c.execute(text("""
            SELECT student_id, meal FROM attendance WHERE menu_date=:d
        """), {"d": date.today()}).mappings().all()

    meal_ids = {meal: {x["student_id"] for x in today_attendance if x["meal"] == meal} for meal in ["Breakfast", "Lunch", "Evening Snacks", "Dinner"]}
    return render_template("admin.html", students=students, counts=counts, meal_ids=meal_ids, q=q)

@app.get("/admin/student")
@admin_required
def admin_student():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect(url_for("dashboard"))
    with engine.begin() as c:
        student = c.execute(text("""
            SELECT s.*, COALESCE(SUM(p.amount),0) AS paid_total
            FROM students s LEFT JOIN payments p ON p.student_id=s.id
            WHERE LOWER(s.roll)=LOWER(:q) OR LOWER(s.name) LIKE LOWER(:like)
            GROUP BY s.id ORDER BY CASE WHEN LOWER(s.roll)=LOWER(:q) THEN 0 ELSE 1 END, s.name
            LIMIT 1
        """), {"q": q, "like": f"%{q}%"}).mappings().first()
        if not student:
            flash("Student not found. / विद्यार्थी सापडला नाही.")
            return redirect(url_for("dashboard"))

        attendance = c.execute(text("""
            SELECT menu_date, meal, created_at FROM attendance
            WHERE student_id=:sid ORDER BY menu_date DESC, created_at DESC
        """), {"sid": student["id"]}).mappings().all()
        payments = c.execute(text("""
            SELECT amount, paid_date, note FROM payments
            WHERE student_id=:sid ORDER BY paid_date DESC, id DESC
        """), {"sid": student["id"]}).mappings().all()
        today = c.execute(text("""
            SELECT meal FROM attendance WHERE student_id=:sid AND menu_date=:d
        """), {"sid": student["id"], "d": date.today()}).scalars().all()

    return render_template("admin_student.html", student=student, attendance=attendance,
                           payments=payments, today_meals=set(today))

@app.route("/admin/student/<int:student_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_student(student_id):
    with engine.begin() as c:
        student = c.execute(text("SELECT * FROM students WHERE id=:id"), {"id": student_id}).mappings().first()
        if not student:
            flash("Student not found. / विद्यार्थी सापडला नाही.")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = request.form["name"].strip()
            roll = request.form["roll"].strip()
            phone = request.form.get("phone", "").strip()
            course = request.form.get("course", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            photo_data = save_photo(request.files.get("photo"))
            keep_photo = request.form.get("keep_photo") == "1"
            if photo_data is None and keep_photo:
                photo_data = student.get("photo_data")
            try:
                if not username:
                    username = student.get("username") or roll
                if password:
                    c.execute(text("""UPDATE students SET name=:name, roll=:roll, phone=:phone, course=:course, photo_data=:photo, username=:username, password_hash=:password_hash WHERE id=:id"""), {"name":name,"roll":roll,"phone":phone,"course":course,"photo":photo_data,"username":username,"password_hash":generate_password_hash(password),"id":student_id})
                else:
                    c.execute(text("""UPDATE students SET name=:name, roll=:roll, phone=:phone, course=:course, photo_data=:photo, username=:username WHERE id=:id"""), {"name":name,"roll":roll,"phone":phone,"course":course,"photo":photo_data,"username":username,"id":student_id})
                flash("Student updated. / विद्यार्थी माहिती अपडेट झाली.")
                return redirect(url_for("admin_student", q=roll))
            except IntegrityError:
                flash("Roll No already exists. / Roll No आधीच आहे.")
    return render_template("edit_student.html", student=student)

@app.post("/admin/attendance")
@admin_required
def admin_attendance():
    student_id = request.form["student_id"]
    meal = request.form["meal"]
    student = None
    inserted = False
    with engine.begin() as c:
        student = c.execute(text("SELECT id, name, phone FROM students WHERE id=:id"), {"id": student_id}).mappings().first()
        if not student:
            flash("Student not found.")
            return redirect(url_for("dashboard"))
        try:
            c.execute(text("""INSERT INTO attendance(student_id, menu_date, meal) VALUES(:sid, :d, :meal)"""), {"sid": student_id,"d": date.today(),"meal": meal})
            inserted = True
        except IntegrityError:
            flash("Attendance already saved. / उपस्थिती आधीच सेव्ह आहे.")
    if inserted:
        ok, detail = whatsapp_notify(student, meal)
        if ok:
            flash(f"{meal} attendance saved. WhatsApp message sent to {student['name']}.")
        else:
            flash(f"{meal} attendance saved. WhatsApp message not sent (API setup pending).")
    return redirect(url_for("dashboard"))

@app.post("/admin/menu")
@admin_required
def add_menu():
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO menus(menu_date, meal, item)
            VALUES(:d, :meal, :item)
        """), {
            "d": request.form["menu_date"],
            "meal": request.form["meal"],
            "item": request.form["item"]
        })

    return redirect(url_for("dashboard"))

@app.post("/admin/payment")
@admin_required
def payment():
    with engine.begin() as c:
        s = c.execute(
            text("SELECT id FROM students WHERE roll=:roll"),
            {"roll": request.form["roll"]}
        ).mappings().first()

        if s:
            c.execute(text("""
                INSERT INTO payments(student_id, amount, paid_date, note)
                VALUES(:sid, :amount, :d, :note)
            """), {
                "sid": s["id"],
                "amount": float(request.form["amount"]),
                "d": date.today(),
                "note": request.form.get("note", "")
            })

    return redirect(url_for("dashboard"))

@app.post("/admin/student/<int:student_id>/delete")
@admin_required
def delete_student(student_id):
    with engine.begin() as c:
        c.execute(text("DELETE FROM attendance WHERE student_id=:id"), {"id": student_id})
        c.execute(text("DELETE FROM payments WHERE student_id=:id"), {"id": student_id})
        c.execute(text("DELETE FROM students WHERE id=:id"), {"id": student_id})
    flash("Student removed. / विद्यार्थी काढून टाकला.")
    return redirect(url_for("dashboard"))

@app.get("/admin/export")
@admin_required
def export():
    with engine.begin() as c:
        students = [dict(x) for x in c.execute(
            text("SELECT * FROM students")
        ).mappings().all()]
        attendance = [dict(x) for x in c.execute(
            text("SELECT * FROM attendance")
        ).mappings().all()]
        payments = [dict(x) for x in c.execute(
            text("SELECT * FROM payments")
        ).mappings().all()]

    return jsonify({
        "students": students,
        "attendance": attendance,
        "payments": payments
    })

@app.get("/health")
def health():
    try:
        with engine.begin() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {
            "status": "error",
            "database": "unavailable",
            "message": str(e)
        }, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
