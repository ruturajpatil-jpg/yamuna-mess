import os
from datetime import date, datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_RAILWAY")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "CHANGE_ME_IN_RAILWAY")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    # Local fallback for testing; Railway should always set DATABASE_URL.
    DATABASE_URL = "sqlite:///yamuna_mess_local.db"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
 id SERIAL PRIMARY KEY,
 name TEXT NOT NULL,
 roll TEXT UNIQUE NOT NULL,
 phone TEXT,
 course TEXT,
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
        # SQLite local fallback needs INTEGER primary keys instead of SERIAL.
        if DATABASE_URL.startswith("sqlite"):
            c.execute(text("""
            CREATE TABLE IF NOT EXISTS students (
             id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
             roll TEXT UNIQUE NOT NULL, phone TEXT, course TEXT,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS menus (
             id INTEGER PRIMARY KEY AUTOINCREMENT, menu_date DATE NOT NULL,
             meal TEXT NOT NULL, item TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attendance (
             id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
             menu_date DATE NOT NULL, meal TEXT NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             UNIQUE(student_id, menu_date, meal));
            CREATE TABLE IF NOT EXISTS payments (
             id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
             amount NUMERIC(12,2) NOT NULL, paid_date DATE NOT NULL, note TEXT);
            """))
        else:
            c.execute(text(SCHEMA))
init_db()

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper

@app.context_processor
def common():
    return {"today": date.today().isoformat(), "admin": session.get("admin")}

@app.route("/")
def home():
    with engine.begin() as c:
        rows = c.execute(text("""
          SELECT meal, STRING_AGG(item, ' + ') AS item
          FROM menus WHERE menu_date=:d GROUP BY meal
        """), {"d": date.today()}).mappings().all() if not DATABASE_URL.startswith("sqlite") else c.execute(text("""
          SELECT meal, GROUP_CONCAT(item, ' + ') AS item
          FROM menus WHERE menu_date=:d GROUP BY meal
        """), {"d": date.today()}).mappings().all()
    return render_template("home.html", menus=rows)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form["name"].strip()
        roll=request.form["roll"].strip()
        phone=request.form.get("phone","").strip()
        course=request.form.get("course","").strip()
        try:
            with engine.begin() as c:
                c.execute(text("""
                  INSERT INTO students(name,roll,phone,course)
                  VALUES(:name,:roll,:phone,:course)
                """), locals())
            flash("Registration successful. / नोंदणी यशस्वी झाली.")
            return redirect(url_for("student", roll=roll))
        except IntegrityError:
            flash("Roll No already exists. / Roll No आधीच आहे.")
    return render_template("register.html")

@app.route("/student", methods=["GET"])
def student():
    roll=request.args.get("roll","").strip()
    with engine.begin() as c:
        s=c.execute(text("SELECT * FROM students WHERE roll=:roll"), {"roll":roll}).mappings().first()
        if not s:
            return render_template("student.html", student=None, roll=roll)
        att=c.execute(text("""
          SELECT meal FROM attendance WHERE student_id=:id AND menu_date=:d
        """), {"id":s["id"],"d":date.today()}).mappings().all()
        paid=c.execute(text("""
          SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE student_id=:id
        """), {"id":s["id"]}).mappings().first()["total"]
    return render_template("student.html", student=s, att=[x["meal"] for x in att], paid=paid)

@app.post("/attendance")
def mark_attendance():
    roll=request.form["roll"]; meal=request.form["meal"]
    with engine.begin() as c:
        s=c.execute(text("SELECT id FROM students WHERE roll=:roll"), {"roll":roll}).mappings().first()
        if s:
            try:
                c.execute(text("""
                  INSERT INTO attendance(student_id,menu_date,meal)
                  VALUES(:sid,:d,:meal)
                """), {"sid":s["id"],"d":date.today(),"meal":meal})
            except IntegrityError:
                pass
    return redirect(url_for("student", roll=roll))

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if request.form["username"]==ADMIN_USER and request.form["password"]==ADMIN_PASS:
            session["admin"]=True
            return redirect(url_for("dashboard"))
        flash("Invalid admin login.")
    return render_template("login.html")

@app.get("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/admin")
@admin_required
def dashboard():
    with engine.begin() as c:
        students=c.execute(text("SELECT * FROM students ORDER BY id DESC")).mappings().all()
        counts={}
        for meal in ["Breakfast","Lunch","Evening Snacks","Dinner"]:
            counts[meal]=c.execute(text("""
              SELECT COUNT(*) n FROM attendance
              WHERE menu_date=:d AND meal=:meal
            """), {"d":date.today(),"meal":meal}).scalar()
    return render_template("admin.html", students=students, counts=counts)

@app.post("/admin/menu")
@admin_required
def add_menu():
    with engine.begin() as c:
        c.execute(text("""
          INSERT INTO menus(menu_date,meal,item) VALUES(:d,:meal,:item)
        """), {"d":request.form["menu_date"],"meal":request.form["meal"],"item":request.form["item"]})
    return redirect(url_for("dashboard"))

@app.post("/admin/payment")
@admin_required
def payment():
    with engine.begin() as c:
        s=c.execute(text("SELECT id FROM students WHERE roll=:roll"), {"roll":request.form["roll"]}).mappings().first()
        if s:
            c.execute(text("""
              INSERT INTO payments(student_id,amount,paid_date,note)
              VALUES(:sid,:amount,:d,:note)
            """), {"sid":s["id"],"amount":float(request.form["amount"]),
                    "d":date.today(),"note":request.form.get("note","")})
    return redirect(url_for("dashboard"))

@app.get("/admin/export")
@admin_required
def export():
    with engine.begin() as c:
        students=[dict(x) for x in c.execute(text("SELECT * FROM students")).mappings().all()]
        attendance=[dict(x) for x in c.execute(text("SELECT * FROM attendance")).mappings().all()]
        payments=[dict(x) for x in c.execute(text("SELECT * FROM payments")).mappings().all()]
    return jsonify({"students":students,"attendance":attendance,"payments":payments})

@app.get("/health")
def health():
    try:
        with engine.begin() as c: c.execute(text("SELECT 1"))
        return {"status":"ok","database":"connected"}
    except Exception as e:
        return {"status":"error","database":"unavailable","message":str(e)},500

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
