# focus-deals v0.9.6 – Jan 2026
import os
import secrets
from flask import Flask, flash, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from openai import OpenAI
client = OpenAI()

app = Flask(__name__)
app.secret_key = "f10360288a752c7695de054e98e48d3a"

serializer = URLSafeTimedSerializer(app.secret_key)

def generate_reset_token(user_id):
    return serializer.dumps(user_id, salt="password-reset")

def verify_reset_token(token, max_age=3600):
    try:
        return serializer.loads(token, salt="password-reset", max_age=max_age)
    except:
        return None

# ---------- Render DB ----------
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------- Models ----------

class Users(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)

    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    must_change_password = db.Column(db.Boolean, default=True)

    security_question = db.Column(db.String(255))
    security_answer_hash = db.Column(db.String(255))

class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    shift = db.Column(db.String(20), nullable=False)
    hours = db.Column(db.Float, nullable=False)
    deals = db.Column(db.Integer, nullable=False)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_month_start = db.Column(db.Date, nullable=False)
    current_month_end = db.Column(db.Date, nullable=False)
    monthly_deal_target = db.Column(db.Integer, nullable=False)
    stretch_goal = db.Column(db.Integer, nullable=False)

class MonthlyScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month_start = db.Column(db.Date, nullable=False)
    month_end = db.Column(db.Date, nullable=False)
    employee = db.Column(db.String(50), nullable=False)
    hours = db.Column(db.Float, nullable=False)
    deals = db.Column(db.Integer, nullable=False)
    dph = db.Column(db.Float, nullable=False)

class ScheduleShift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)

    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    role = db.Column(db.String(50), nullable=True)  # Cashier / Store Manager / ASM / Shift Lead

    __table_args__ = (db.UniqueConstraint('employee', 'date', name='uq_schedule_employee_date'),)


# ---------- Helpers ----------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in") or not session.get("is_admin"):
            # Redirect non-admins to home or login
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

def get_week_start_saturday(d):
    # Python weekday: Mon=0 ... Sun=6, Saturday=5
    offset = (d.weekday() - 5) % 7
    return d - timedelta(days=offset)

def get_settings():
    s = Setting.query.first()
    if not s:
        today = datetime.today().date()
        s = Setting(
            current_month_start=today.replace(day=1),
            current_month_end=today,
            monthly_deal_target=100,
            stretch_goal=120
        )
        db.session.add(s)
        db.session.commit()
    return s

def build_leaderboard(entries):
    data = {}
    for e in entries:
        if e.employee not in data:
            data[e.employee] = {"hours":0, "deals":0}
        data[e.employee]["hours"] += e.hours
        data[e.employee]["deals"] += e.deals

    leaderboard = []
    for emp, v in data.items():
        dph = round(v["deals"] / v["hours"], 2) if v["hours"] > 0 else 0
        hours_rounded = round(v["hours"], 2)
        leaderboard.append({
            "employee": emp,
            "hours": hours_rounded,
            "deals": v["deals"],
            "dph": round(v["deals"] / hours_rounded, 2) if hours_rounded > 0 else 0
        })
    leaderboard.sort(key=lambda x: x["dph"], reverse=True)
    return leaderboard

SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What city were you born in?",
    "What is your mother’s maiden name?",
    "What was the name of your first school?",
    "What is the name of the street you grew up on?",
    "What was the make of your first car?",
    "What is your favorite movie?"
]

@app.context_processor
def inject_name_helpers():
    users = Users.query.all()
    name_map = {u.username: (u.display_name or u.username) for u in users}

    def display_name(value: str) -> str:
        if not value:
            return ""
        return name_map.get(value, value)  # fallback for old rows

    return {"name_map": name_map, "display_name": display_name}

# ---------- Undo Stack ----------
undo_stack = []

# ---------- Routes ----------

@app.route("/admin")
@admin_required
def admin_dashboard():
    s = get_settings()
    return render_template("admin_dashboard.html", settings=s)

@app.route("/admin/schedule")
@admin_required
def admin_schedule():
    # simplest: reuse schedule() but allow editing in template via a flag
    today = datetime.today().date()
    week_start_str = request.args.get("week_start")
    week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date() if week_start_str else get_week_start_saturday(today)

    days = [week_start + timedelta(days=i) for i in range(7)]
    week_end = days[-1]

    shifts = ScheduleShift.query.filter(ScheduleShift.date >= week_start, ScheduleShift.date <= week_end).all()
    grid = {(s.employee, s.date): s for s in shifts}

    employees = Users.query.order_by(Users.display_name).all()
    roles = ["Cashier", "Assistant Manager", "Store Manager", "Shift Lead"]

    prev_week = (week_start - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (week_start + timedelta(days=7)).strftime("%Y-%m-%d")

    return render_template(
        "schedule.html",
        employees=employees,
        days=days,
        grid=grid,
        roles=roles,
        week_start=week_start,
        prev_week=prev_week,
        next_week=next_week,
        editable=True
    )

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    s = get_settings()
    if request.method == "POST":
        s.current_month_start = datetime.strptime(request.form["current_month_start"], "%Y-%m-%d").date()
        s.current_month_end = datetime.strptime(request.form["current_month_end"], "%Y-%m-%d").date()
        s.monthly_deal_target = int(request.form["monthly_deal_target"])
        s.stretch_goal = int(request.form["stretch_goal"])
        db.session.commit()
        flash("Settings saved", "success")
        return redirect(url_for("admin_settings"))
    return render_template("settings.html", settings=s)

@app.route("/admin/history")
@admin_required
def admin_history():
    entries = Entry.query.order_by(Entry.date.desc()).all()
    users = Users.query.order_by(Users.display_name).all()
    return render_template(
        "history.html",
        entries=entries,
        users=users,
        can_undo=len(undo_stack) > 0
    )

@app.route("/admin/users")
@admin_required
def admin_users():
    users = Users.query.order_by(Users.username).all()
    return render_template("admin_users.html", users=users)

@app.route("/admin/users/create", methods=["POST"])
@admin_required
def create_user():
    username = request.form["username"].strip().lower()
    display_name = request.form.get("display_name", "").strip() or username
    password = request.form.get("password")
    is_admin = "is_admin" in request.form

    if Users.query.filter_by(username=username).first():
        flash("Username already exists", "error")
        return redirect(url_for("admin_users"))

    if not password:
        password = secrets.token_urlsafe(8)
        flash(f"Temporary password: {password}", "success")

    user = Users(
        username=username,
        display_name=display_name,
        is_admin=is_admin,
        must_change_password=True
    )
    user.password_hash = generate_password_hash(password)

    db.session.add(user)
    db.session.commit()

    flash("User created", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
@admin_required
def update_user(user_id):
    user = Users.query.get_or_404(user_id)

    user.username = request.form["username"].strip().lower()
    user.display_name = request.form.get("display_name", "").strip() or user.username
    user.is_admin = "is_admin" in request.form
    user.must_change_password = "must_change_password" in request.form

    db.session.commit()
    flash("User updated", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/reset_password", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    user = Users.query.get_or_404(user_id)

    temp_password = secrets.token_urlsafe(8)

    # Write the hash into whichever field your User model actually has
    if hasattr(user, "password_hash"):
        user.password_hash = generate_password_hash(temp_password)
    elif hasattr(user, "password"):
        user.password = generate_password_hash(temp_password)
    else:
        raise RuntimeError("User model missing password_hash/password field")

    # Force password change if the column exists
    if hasattr(user, "must_change_password"):
        user.must_change_password = True

    db.session.commit()

    flash(f"Temporary password for {user.username}: {temp_password}", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete yourself")
        return redirect(url_for("admin_users"))

    user = Users.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()

    flash("User deleted")
    return redirect(url_for("admin_users"))

@app.route("/schedule")
def schedule():
    today = datetime.today().date()
    week_start_str = request.args.get("week_start")

    if week_start_str:
        week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()
    else:
        week_start = get_week_start_saturday(today)

    days = [week_start + timedelta(days=i) for i in range(7)]
    week_end = days[-1]

    shifts = ScheduleShift.query.filter(
        ScheduleShift.date >= week_start,
        ScheduleShift.date <= week_end
    ).all()

    # Map (employee, date) -> shift record
    grid = {(s.employee, s.date): s for s in shifts}

    employees = Users.query.order_by(Users.display_name).all()
    roles = ["Cashier", "Assistant Manager", "Store Manager", "Shift Lead"]

    prev_week = (week_start - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (week_start + timedelta(days=7)).strftime("%Y-%m-%d")

    return render_template(
        "schedule.html",
        employees=employees,
        days=days,
        grid=grid,
        roles=roles,
        week_start=week_start,
        prev_week=prev_week,
        next_week=next_week,
        editable=False
    )

@app.route("/schedule/set", methods=["POST"])
@admin_required
def schedule_set():
    employee = request.form["employee"]
    date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()

    start_raw = request.form.get("start_time", "").strip()
    end_raw = request.form.get("end_time", "").strip()
    role = request.form.get("role", "").strip() or None

    # If no times, treat as Off (delete)
    if not start_raw or not end_raw:
        existing = ScheduleShift.query.filter_by(employee=employee, date=date).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
        return redirect(url_for("admin_schedule", week_start=request.form.get("week_start")))


    start_time = datetime.strptime(start_raw, "%H:%M").time()
    end_time = datetime.strptime(end_raw, "%H:%M").time()

    existing = ScheduleShift.query.filter_by(employee=employee, date=date).first()
    if existing:
        existing.start_time = start_time
        existing.end_time = end_time
        existing.role = role
    else:
        db.session.add(ScheduleShift(
            employee=employee,
            date=date,
            start_time=start_time,
            end_time=end_time,
            role=role
        ))

    db.session.commit()
    return redirect(url_for("admin_schedule", week_start=request.form.get("week_start")))

@app.route("/employee/<name>")
def employee_profile(name):
    s = get_settings()

    # Month range from settings
    start = s.current_month_start
    end = s.current_month_end

    # Pull this employee's entries for the month
    entries = Entry.query.filter(
        Entry.employee == name,
        Entry.date >= start,
        Entry.date <= end
    ).order_by(Entry.date.desc()).all()

    total_hours = round(sum(e.hours for e in entries), 2)
    total_deals = sum(e.deals for e in entries)
    dph = round(total_deals / total_hours, 2) if total_hours > 0 else 0

    # Daily trend (group by date)
    daily = {}
    for e in entries:
        if e.date not in daily:
            daily[e.date] = {"hours": 0.0, "deals": 0}
        daily[e.date]["hours"] += e.hours
        daily[e.date]["deals"] += e.deals

    trend = []
    for d in sorted(daily.keys()):
        h = daily[d]["hours"]
        deals = daily[d]["deals"]
        trend.append({
            "date": d.strftime("%Y-%m-%d"),
            "hours": round(h, 2),
            "deals": deals,
            "dph": round(deals / h, 2) if h > 0 else 0
        })

    # Recent entries (last 10)
    recent = entries[:10]

    return render_template(
        "employee.html",
        employee=name,
        settings=s,
        total_hours=total_hours,
        total_deals=total_deals,
        dph=dph,
        trend=trend,
        recent=recent
    )

@app.route("/ask", methods=["GET"])
@admin_required
def ask():
    # Initialize session chat if it doesn't exist
    if "chat" not in session:
        session["chat"] = []
    return render_template("ask.html", chat=session["chat"], error=None)

@app.route("/ask_ajax", methods=["POST"])
@admin_required
def ask_ajax():
    data = request.get_json()
    question = data.get("question")
    if not question:
        return {"error": "No question provided"}, 400

    try:
        # Summarize your entries for context
        entries = Entry.query.all()
        summary = [f"{e.employee}: {round(e.hours,2)} hours, {e.deals} deals on {e.date}" for e in entries]

        # Get settings
        s = get_settings()  # your existing helper
        settings_info = (
            f"Current month: {s.current_month_start} to {s.current_month_end}\n"
            f"Monthly deal target: {s.monthly_deal_target}\n"
            f"Stretch goal: {s.stretch_goal}"
        )

        # Combine data context
        data_context = "\n".join(summary) + "\n" + settings_info

        # Build system messages
        messages = [
            {"role": "system", "content": (
                "You are a gas station performance analyst. "
                "Answer ONLY using the provided data. "
                "Do NOT explain calculations unless explicitly asked. "
                "Do NOT add extra commentary."
                "Leaderboard is to track DPH (deals per hour) for employees."
                "Current deal month is defined by the settings."
            )},
            {"role": "system", "content": f"DATA:\n{data_context}"}
        ]

        # Add previous conversation
        for msg in session["chat"]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current question last
        messages.append({"role": "user", "content": question})

        # Call OpenAI
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages
        )

        answer = response.choices[0].message.content

        # Save to session
        session["chat"].append({"role": "user", "content": question})
        session["chat"].append({"role": "assistant", "content": answer})
        session.modified = True

        return {"answer": answer}

    except Exception as e:
        print("OPENAI ERROR:", e)
        return {"error": "AI service unavailable."}, 500

@app.route("/clear_chat")
@admin_required
def clear_chat():
    session.pop("chat", None)
    return redirect(url_for("ask"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = Users.query.filter_by(username=username).first()

        # If user not found OR hash missing, fail cleanly (no exception)
        if not user or not user.password_hash:
            flash("Invalid username or password", "error")
            return render_template("login.html"), 401

        try:
            ok = check_password_hash(user.password_hash, password)
        except ValueError:
            # This means the stored hash is malformed (common if it was saved wrong / empty)
            app.logger.exception("Invalid password hash for user_id=%s username=%s", user.id, user.username)
            flash("This account password is corrupted. Ask an admin to reset it.", "error")
            return render_template("login.html"), 400

        if ok:
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            session["logged_in"] = True

            if user.must_change_password:
                # allow reset page to load for this user
                session["allow_password_reset"] = True
                session["reset_user"] = user.id
                return redirect(url_for("reset_password_secure"))


            return redirect(url_for("index"))

        flash("Invalid username or password", "error")
        return render_template("login.html"), 401

    return render_template("login.html")

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username")
        user = Users.query.filter_by(username=username).first()

        if not user or not user.security_question:
            flash("User not found or no security question set", "error")
            return redirect(url_for("forgot_password"))

        session["reset_user"] = user.id
        return redirect(url_for("security_question"))

    return render_template("forgot_password.html")

@app.route("/security_question", methods=["GET", "POST"])
def security_question():
    user_id = session.get("reset_user")
    if not user_id:
        return redirect(url_for("login"))

    user = Users.query.get(user_id)

    if request.method == "POST":
        answer = request.form.get("answer", "").lower().strip()

        if check_password_hash(user.security_answer_hash, answer):
            session["allow_password_reset"] = True
            return redirect(url_for("reset_password_secure"))

        flash("Incorrect answer", "error")

    return render_template(
        "security_question.html",
        question=user.security_question
    )

from werkzeug.security import generate_password_hash

@app.route("/reset_password_secure", methods=["GET", "POST"])
def reset_password_secure():
    if not session.get("allow_password_reset") or not session.get("reset_user"):
        flash("Reset session expired. Please start again.", "error")
        return redirect(url_for("login"))

    user = Users.query.get(session.get("reset_user"))
    if not user:
        session.pop("allow_password_reset", None)
        session.pop("reset_user", None)
        flash("User not found.", "error")
        return redirect(url_for("login"))

    # Define “needs security setup” however your app currently does it.
    # Common pattern:
    needs_security_setup = (not user.security_question) or (not user.security_answer_hash)

    if request.method == "POST":
        # If first-time setup, require question+answer
        if needs_security_setup:
            q = (request.form.get("security_question") or "").strip()
            a = (request.form.get("security_answer") or "").strip()
            if not q or not a:
                flash("Security question and answer are required.", "error")
                return render_template("reset_password.html", needs_security_setup=True, questions=SECURITY_QUESTIONS)

            user.security_question = q
            user.security_answer_hash = generate_password_hash(a)

            # Optional: set display name here too if you want
            dn = (request.form.get("display_name") or "").strip()
            if dn:
                user.display_name = dn

        new = (request.form.get("new_password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()

        if not new:
            flash("Password is required.", "error")
            return render_template("reset_password.html", needs_security_setup=needs_security_setup, questions=SECURITY_QUESTIONS)

        if new != confirm:
            flash("Passwords must match", "error")
            return render_template("reset_password.html", needs_security_setup=needs_security_setup, questions=SECURITY_QUESTIONS)

        user.password_hash = generate_password_hash(new)
        user.must_change_password = False
        db.session.commit()

        session.pop("allow_password_reset", None)
        session.pop("reset_user", None)

        # If they’re already logged in (forced reset), keep them logged in
        if session.get("logged_in") and session.get("user_id") == user.id:
            flash("Password updated.", "success")
            return redirect(url_for("index"))

        flash("Password reset successfully", "success")
        return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        needs_security_setup=needs_security_setup,
        questions=SECURITY_QUESTIONS,
    )

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    session.pop("user_id", None)
    session.pop("is_admin", None)
    return redirect(url_for("login"))  # redirect to login page

@app.route("/", methods=["GET","POST"])
def index():
    settings = get_settings()
    today = datetime.today().date()
    total_days = (settings.current_month_end - settings.current_month_start).days + 1
    daily_needed = round(settings.monthly_deal_target / total_days, 2) if total_days>0 else 0
    days_remaining = max(0, (settings.current_month_end - today).days + 1)

    month_entries = Entry.query.filter(
        Entry.date>=settings.current_month_start,
        Entry.date<=settings.current_month_end
    ).all()
    deals_done = sum(e.deals for e in month_entries)
    goal_pct = round((deals_done / settings.monthly_deal_target) * 100, 1) if settings.monthly_deal_target else 0
    stretch_pct = round((deals_done / settings.stretch_goal) * 100, 1) if settings.stretch_goal else 0


    if request.method=="POST":
        e = Entry(
            employee=request.form["employee"],
            date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
            shift=request.form["shift"],
            hours=float(request.form["hours"]),
            deals=int(request.form["deals"])
        )
        db.session.add(e)
        db.session.commit()
        return redirect(url_for("index"))

    employees = Users.query.order_by(Users.display_name).all()
    shifts = ["Open","Mid","Close"]

    return render_template(
        "index.html",
        today=today.strftime("%Y-%m-%d"),
        settings=settings,
        days_remaining=days_remaining,
        daily_needed=daily_needed,
        deals_done=deals_done,
        goal_pct=goal_pct,
        stretch_pct=stretch_pct,
        employees=employees,
        shifts=shifts
    )

@app.route("/leaderboard")
def leaderboard():
    s = get_settings()  # <-- uses your settings table

    # Defaults = current month range from settings
    start_date = request.args.get("start_date", s.current_month_start.strftime("%Y-%m-%d"))
    end_date   = request.args.get("end_date",   s.current_month_end.strftime("%Y-%m-%d"))

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end   = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = Entry.query.filter(Entry.date >= start, Entry.date <= end).all()
    leaderboard_data = build_leaderboard(filtered)

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard_data,
        start_date=start_date,
        end_date=end_date,
        deals_done=sum(e.deals for e in filtered),
        settings=s
    )

@app.route("/get_entry/<int:id>")
@admin_required
def get_entry(id):
    e = Entry.query.get_or_404(id)
    return jsonify({
        "id": e.id,
        "employee": e.employee,
        "date": e.date.strftime("%Y-%m-%d"),
        "shift": e.shift,
        "hours": e.hours,
        "deals": e.deals
    })

@app.route("/edit_entry/<int:id>", methods=["POST"])
@admin_required
def edit_entry(id):
    e = Entry.query.get_or_404(id)
    data = request.get_json()
    e.employee = data.get("employee", e.employee)
    e.date = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else e.date
    e.shift = data.get("shift", e.shift)
    e.hours = float(data.get("hours", e.hours))
    e.deals = int(data.get("deals", e.deals))
    db.session.commit()
    return "", 200

@app.route("/delete/<int:id>", methods=["POST"])
@admin_required
def delete_entry(id):
    e = Entry.query.get_or_404(id)

    # store data, not the ORM object
    undo_stack.append({
        "id": e.id,
        "employee": e.employee,
        "date": e.date,
        "shift": e.shift,
        "hours": e.hours,
        "deals": e.deals,
    })

    db.session.delete(e)
    db.session.commit()
    flash("Entry deleted (you can undo).", "info")
    return redirect(url_for("admin_history"))

@app.route("/undo_delete")
@admin_required
def undo_delete():
    if not undo_stack:
        flash("Nothing to undo.", "info")
        return redirect(url_for("admin_history"))

    data = undo_stack.pop()

    restored = Entry(
        employee=data["employee"],
        date=data["date"],
        shift=data["shift"],
        hours=data["hours"],
        deals=data["deals"],
    )

    # Try to restore the same ID (nice for UI), but fall back if it conflicts
    restored.id = data["id"]

    db.session.add(restored)
    try:
        db.session.commit()
        flash("Undo successful.", "success")
    except IntegrityError:
        db.session.rollback()
        restored.id = None
        db.session.add(restored)
        db.session.commit()
        flash("Undo successful (new ID assigned).", "success")

    return redirect(url_for("admin_history"))

@app.route("/finalize_month", methods=["POST"])
@admin_required
def finalize_month():
    s = get_settings()
    month_entries = Entry.query.filter(Entry.date>=s.current_month_start, Entry.date<=s.current_month_end).all()
    leaderboard = build_leaderboard(month_entries)

    for e in leaderboard:
        ms = MonthlyScore(
            month_start=s.current_month_start,
            month_end=s.current_month_end,
            employee=e["employee"],
            hours=e["hours"],
            deals=e["deals"],
            dph=e["dph"]
        )
        db.session.add(ms)
    db.session.commit()
    return redirect(url_for("admin_settings"))

@app.route("/compare_scores")
def compare_scores():
    months = db.session.query(MonthlyScore.month_start, MonthlyScore.month_end).distinct().all()
    if not months:
        return "<div class='content-card'><p>No monthly scores saved.</p></div>"

    employees = [e.employee for e in db.session.query(MonthlyScore.employee).distinct()]
    table = []

    for emp in employees:
        row = {"employee": emp, "scores":[]}
        prev = None
        for m in months:
            ms = MonthlyScore.query.filter_by(employee=emp, month_start=m[0], month_end=m[1]).first()
            dph = ms.dph if ms else None
            change = round(dph - prev,2) if dph is not None and prev is not None else None
            row["scores"].append({"value": dph, "change": change})
            if dph is not None:
                prev = dph
        table.append(row)

    labels = [m[0].strftime("%b %Y") for m in months]
    return render_template("compare_scores.html", table=table, months=labels)

# ---------- Pretty Date Filter ----------
@app.template_filter("pretty_date")
def pretty_date(value):
    dt = datetime.strptime(value, "%Y-%m-%d") if isinstance(value,str) else value
    try:
        return dt.strftime("%b %-d")
    except:
        return dt.strftime("%b %#d")

# ---------- Initialize DB ----------
with app.app_context():
    db.create_all()

# ---------- Run ----------
if __name__=="__main__":
    app.run(debug=True)
