from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "f10360288a752c7695de054e98e48d3a"  # Needed for sessions

# SQLite DB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------- Admin ----------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password"  # Change this to a secure one

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ---------- Models ----------
class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    shift = db.Column(db.String(10), nullable=False)
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

# ---------- Database Init ----------
with app.app_context():
    db.create_all()
    # Create default settings if none exist
    if Setting.query.first() is None:
        today = datetime.today()
        default = Setting(
            current_month_start=today.replace(day=1),
            current_month_end=today,
            monthly_deal_target=100,
            stretch_goal=120
        )
        db.session.add(default)
        db.session.commit()

# ---------- Routes ----------
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("settings"))
        else:
            return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/admin_logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

@app.route("/", methods=["GET", "POST"])
def index():
    today = datetime.today().date()
    settings = Setting.query.first()

    total_days = (settings.current_month_end - settings.current_month_start).days + 1
    daily_needed = round(settings.monthly_deal_target / total_days, 2) if total_days > 0 else 0
    days_remaining = max(0, (settings.current_month_end - today).days + 1)

    month_entries = Entry.query.filter(
        Entry.date >= settings.current_month_start,
        Entry.date <= settings.current_month_end
    ).all()
    deals_done = sum(e.deals for e in month_entries)

    if request.method == "POST":
        employee = request.form["employee"]
        shift = request.form["shift"]
        date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        hours = float(request.form["hours"])
        deals = int(request.form["deals"])
        entry = Entry(employee=employee, date=date, shift=shift, hours=hours, deals=deals)
        db.session.add(entry)
        db.session.commit()
        return redirect(url_for("index"))

    employees = ["Sarah", "Angie", "Beth", "Terry", "Jeff", "Vernon"]
    shifts = ["Open", "Mid", "Close"]

    return render_template(
        "index.html",
        today=today.strftime("%Y-%m-%d"),
        settings=settings,
        days_remaining=days_remaining,
        daily_needed=daily_needed,
        deals_done=deals_done,
        employees=employees,
        shifts=shifts
    )

@app.route("/leaderboard")
def leaderboard():
    today = datetime.today().date()
    start_date = request.args.get("start_date", (today - timedelta(days=6)).strftime("%Y-%m-%d"))
    end_date = request.args.get("end_date", today.strftime("%Y-%m-%d"))
    start_date_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = Entry.query.filter(
        Entry.date >= start_date_dt,
        Entry.date <= end_date_dt
    ).all()

    leaderboard_data = build_leaderboard(filtered)
    deals_done = sum(e.deals for e in filtered)

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard_data,
        start_date=start_date,
        end_date=end_date,
        deals_done=deals_done,
        settings=Setting.query.first()
    )

@app.route("/history")
@admin_required
def history():
    entries = Entry.query.order_by(Entry.date.desc()).all()
    return render_template("history.html", entries=entries, can_undo=False)  # Undo stack optional for now

@app.route("/edit_entry/<int:entry_id>", methods=["POST"])
def edit_entry(entry_id):
    entry = Entry.query.get(entry_id)
    if not entry:
        return "Entry not found", 404
    data = request.get_json()
    entry.employee = data.get("employee", entry.employee)
    entry.date = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
    entry.shift = data.get("shift", entry.shift)
    entry.hours = float(data.get("hours", entry.hours))
    entry.deals = int(data.get("deals", entry.deals))
    db.session.commit()
    return "", 200

@app.route("/delete/<int:entry_id>")
@admin_required
def delete_entry(entry_id):
    entry = Entry.query.get(entry_id)
    if entry:
        db.session.delete(entry)
        db.session.commit()
    return redirect(url_for("history"))

@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    settings = Setting.query.first()
    if request.method == "POST":
        settings.current_month_start = datetime.strptime(request.form["current_month_start"], "%Y-%m-%d").date()
        settings.current_month_end = datetime.strptime(request.form["current_month_end"], "%Y-%m-%d").date()
        settings.monthly_deal_target = int(request.form["monthly_deal_target"])
        settings.stretch_goal = int(request.form["stretch_goal"])
        db.session.commit()
        return redirect(url_for("settings"))
    return render_template("settings.html", settings=settings)

@app.route("/finalize_month", methods=["POST"])
@admin_required
def finalize_month():
    settings = Setting.query.first()
    month_entries = Entry.query.filter(
        Entry.date >= settings.current_month_start,
        Entry.date <= settings.current_month_end
    ).all()

    for e in month_entries:
        dph = round(e.deals / e.hours, 2) if e.hours > 0 else 0
        score = MonthlyScore(
            month_start=settings.current_month_start,
            month_end=settings.current_month_end,
            employee=e.employee,
            hours=e.hours,
            deals=e.deals,
            dph=dph
        )
        db.session.add(score)
    db.session.commit()
    return redirect(url_for("settings"))

@app.route("/compare_scores")
def compare_scores():
    # Get distinct months
    months = db.session.query(MonthlyScore.month_start, MonthlyScore.month_end).distinct().order_by(MonthlyScore.month_start).all()
    if not months:
        return "<div class='content-card'><p>No monthly scores saved.</p></div>"

    # Get employees
    employees = sorted({s.employee for s in MonthlyScore.query.all()})
    table = []
    for emp in employees:
        row = {"employee": emp, "scores": []}
        prev = None
        for m in months:
            score = MonthlyScore.query.filter_by(month_start=m[0], month_end=m[1], employee=emp).first()
            value = score.dph if score else None
            change = round(value - prev, 2) if prev is not None and value is not None else None
            row["scores"].append({"value": value, "change": change})
            if value is not None:
                prev = value
        table.append(row)

    labels = [m[0].strftime("%b %Y") for m in months]
    return render_template("compare_scores.html", table=table, months=labels)

# ---------- Helper Functions ----------
def build_leaderboard(entries):
    data = {}
    for e in entries:
        if e.employee not in data:
            data[e.employee] = {"hours":0, "deals":0}
        data[e.employee]["hours"] += e.hours
        data[e.employee]["deals"] += e.deals
    leaderboard = []
    for emp, v in data.items():
        dph = round(v["deals"]/v["hours"],2) if v["hours"]>0 else 0
        leaderboard.append({"employee":emp,"hours":v["hours"],"deals":v["deals"],"dph":dph})
    leaderboard.sort(key=lambda x:x["deals"], reverse=True)
    return leaderboard

# ---------- Pretty Date Filter ----------
@app.template_filter("pretty_date")
def pretty_date(value):
    if isinstance(value, str):
        dt = datetime.strptime(value, "%Y-%m-%d")
    else:
        dt = value
    try:
        return dt.strftime("%b %-d")
    except ValueError:
        return dt.strftime("%b %#d")

# ---------- Run ----------
if __name__ == "__main__":
    app.run