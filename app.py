from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "f10360288a752c7695de054e98e48d3a"

# ---------- Render DB ----------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------- Admin ----------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password"  # change to secure password

# ---------- Models ----------

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

# ---------- Helpers ----------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

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
        leaderboard.append({"employee": emp, "hours": v["hours"], "deals": v["deals"], "dph": dph})
    leaderboard.sort(key=lambda x: x["deals"], reverse=True)
    return leaderboard

# ---------- Undo Stack ----------
undo_stack = []

# ---------- Routes ----------

@app.route("/admin_login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username==ADMIN_USERNAME and password==ADMIN_PASSWORD:
            session["admin_logged_in"]=True
            return redirect(url_for("settings"))
        return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/admin_logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

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

    employees = ["Sarah","Angie","Beth","Terry","Jeff","Vernon"]
    shifts = ["Open","Mid","Close"]

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
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = Entry.query.filter(Entry.date>=start, Entry.date<=end).all()
    leaderboard_data = build_leaderboard(filtered)

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard_data,
        start_date=start_date,
        end_date=end_date,
        deals_done=sum(e.deals for e in filtered),
        settings=get_settings()
    )

@app.route("/history")
@admin_required
def history():
    entries = Entry.query.order_by(Entry.date.desc()).all()
    return render_template("history.html", entries=entries, can_undo=len(undo_stack)>0)

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

@app.route("/delete/<int:id>")
def delete_entry(id):
    e = Entry.query.get_or_404(id)
    undo_stack.append(e)
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for("history"))

@app.route("/undo_delete")
def undo_delete():
    if undo_stack:
        e = undo_stack.pop()
        db.session.add(e)
        db.session.commit()
    return redirect(url_for("history"))

@app.route("/settings", methods=["GET","POST"])
@admin_required
def settings():
    s = get_settings()
    if request.method=="POST":
        s.current_month_start = datetime.strptime(request.form["current_month_start"], "%Y-%m-%d").date()
        s.current_month_end = datetime.strptime(request.form["current_month_end"], "%Y-%m-%d").date()
        s.monthly_deal_target = int(request.form["monthly_deal_target"])
        s.stretch_goal = int(request.form["stretch_goal"])
        db.session.commit()
        return redirect(url_for("settings"))
    return render_template("settings.html", settings=s)

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
    return redirect(url_for("settings"))

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
    app.run()
