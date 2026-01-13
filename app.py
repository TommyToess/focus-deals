from flask import Flask, render_template, request, redirect, url_for, session
import json
from datetime import datetime, timedelta
import os
from functools import wraps

app = Flask(__name__)

app.secret_key = "f10360288a752c7695de054e98e48d3a"  # Needed for sessions

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password"  # Change this to a secure one

ENTRIES_FILE = "entries.json"
SETTINGS_FILE = "settings.json"
MONTHLY_SCORES_FILE = "monthly_scores.json"

# ---------- Helper Functions ----------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

################################################################################

def read_entries():
    if not os.path.exists(ENTRIES_FILE):
        return []
    with open(ENTRIES_FILE, "r") as f:
        return json.load(f)

def write_entries(entries):
    with open(ENTRIES_FILE, "w") as f:
        json.dump(entries, f, indent=4)

def read_settings():
    if not os.path.exists(SETTINGS_FILE):
        default_settings = {
            "current_month_start": datetime.today().replace(day=1).strftime("%Y-%m-%d"),
            "current_month_end": datetime.today().strftime("%Y-%m-%d"),
            "monthly_deal_target": 100,
            "stretch_goal": 120
        }
        write_settings(default_settings)
        return default_settings
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def write_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def read_monthly_scores():
    if not os.path.exists(MONTHLY_SCORES_FILE):
        return []
    with open(MONTHLY_SCORES_FILE, "r") as f:
        return json.load(f)

def write_monthly_scores(scores):
    with open(MONTHLY_SCORES_FILE, "w") as f:
        json.dump(scores, f, indent=4)

def filter_by_dates(entries, start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return [
        e for e in entries
        if start <= datetime.strptime(e["date"], "%Y-%m-%d").date() <= end
    ]

def build_leaderboard(entries):
    data = {}
    for e in entries:
        emp = e["employee"]
        if emp not in data:
            data[emp] = {"hours": 0, "deals": 0}
        data[emp]["hours"] += e["hours"]
        data[emp]["deals"] += e["deals"]

    leaderboard = []
    for emp, v in data.items():
        dph = round(v["deals"] / v["hours"], 2) if v["hours"] > 0 else 0
        leaderboard.append({
            "employee": emp,
            "hours": v["hours"],
            "deals": v["deals"],
            "dph": dph
        })

    leaderboard.sort(key=lambda x: x["deals"], reverse=True)
    return leaderboard

# ---------- Undo Stack ----------
undo_stack = []

# ---------- Routes ----------

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("settings"))  # Redirect to admin page
        else:
            return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/admin_logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

################################################################################

@app.route("/", methods=["GET", "POST"])
def index():
    today = datetime.today().date()
    today_str = today.strftime("%Y-%m-%d")

    settings = read_settings()
    start = datetime.strptime(settings["current_month_start"], "%Y-%m-%d").date()
    end = datetime.strptime(settings["current_month_end"], "%Y-%m-%d").date()

    total_days = (end - start).days + 1
    daily_needed = round(settings["monthly_deal_target"] / total_days, 2) if total_days > 0 else 0
    days_remaining = max(0, (end - today).days + 1)

    entries = read_entries()
    month_entries = filter_by_dates(entries, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    deals_done = sum(e["deals"] for e in month_entries)

    if request.method == "POST":
        entry = {
            "employee": request.form["employee"],
            "date": request.form["date"],
            "shift": request.form["shift"],
            "hours": float(request.form["hours"]),
            "deals": int(request.form["deals"])
        }
        entries.append(entry)
        write_entries(entries)
        return redirect(url_for("index"))

    employees = ["Sarah", "Angie", "Beth", "Terry", "Jeff", "Vernon"]
    shifts = ["Open", "Mid", "Close"]

    return render_template(
        "index.html",
        today=today_str,
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

    entries = read_entries()
    filtered = filter_by_dates(entries, start_date, end_date)
    leaderboard_data = build_leaderboard(filtered)

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard_data,
        start_date=start_date,
        end_date=end_date,
        deals_done =sum(e["deals"] for e in filtered),
        settings=read_settings()
    )

@app.route("/history")
@admin_required
def history():
    entries = read_entries()
    entries.sort(key=lambda x: x["date"], reverse=True)
    return render_template("history.html", entries=entries, can_undo=len(undo_stack) > 0)

@app.route("/edit_entry/<int:index>", methods=["POST"])
def edit_entry(index):
    import json
    entries = read_entries()
    if 0 <= index < len(entries):
        data = request.get_json()
        entries[index]["employee"] = data.get("employee", entries[index]["employee"])
        entries[index]["date"] = data.get("date", entries[index]["date"])
        entries[index]["shift"] = data.get("shift", entries[index]["shift"])
        entries[index]["hours"] = float(data.get("hours", entries[index]["hours"]))
        entries[index]["deals"] = int(data.get("deals", entries[index]["deals"]))
        write_entries(entries)
        return "", 200
    return "Index out of range", 400

@app.route("/delete/<int:index>")
def delete_entry(index):
    entries = read_entries()
    if 0 <= index < len(entries):
        undo_stack.append(entries[index])
        entries.pop(index)
        write_entries(entries)
    return redirect(url_for("history"))

@app.route("/undo_delete")
def undo_delete():
    if undo_stack:
        entries = read_entries()
        entries.append(undo_stack.pop())
        write_entries(entries)
    return redirect(url_for("history"))

@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    settings = read_settings()

    if request.method == "POST":
        settings["current_month_start"] = request.form["current_month_start"]
        settings["current_month_end"] = request.form["current_month_end"]
        settings["monthly_deal_target"] = int(request.form["monthly_deal_target"])
        settings["stretch_goal"] = int(request.form["stretch_goal"])
        write_settings(settings)
        return redirect(url_for("settings"))

    return render_template("settings.html", settings=settings)

@app.route("/finalize_month", methods=["POST"])
def finalize_month():
    settings = read_settings()
    entries = read_entries()

    month_entries = filter_by_dates(
        entries,
        settings["current_month_start"],
        settings["current_month_end"]
    )

    leaderboard = build_leaderboard(month_entries)
    scores = read_monthly_scores()

    scores.append({
        "month_start": settings["current_month_start"],
        "month_end": settings["current_month_end"],
        "scores": leaderboard
    })

    write_monthly_scores(scores)
    return redirect(url_for("settings"))

@app.route("/compare_scores")
def compare_scores():
    months = read_monthly_scores()

    if not months:
        return "<div class='content-card'><p>No monthly scores saved.</p></div>"

    employees = sorted({
        e["employee"]
        for m in months
        for e in m["scores"]
    })

    table = []

    for emp in employees:
        row = {"employee": emp, "scores": []}
        prev = None

        for m in months:
            entry = next((e for e in m["scores"] if e["employee"] == emp), None)
            dph = entry["dph"] if entry else None

            change = round(dph - prev, 2) if dph is not None and prev is not None else None
            row["scores"].append({"value": dph, "change": change})

            if dph is not None:
                prev = dph

        table.append(row)

    labels = [m["month_start"][:7] for m in months]

    return render_template("compare_scores.html", table=table, months=labels)

# ---------- Pretty Date Filter (Windows Safe) ----------

@app.template_filter("pretty_date")
def pretty_date(value):
    dt = datetime.strptime(value, "%Y-%m-%d")
    try:
        return dt.strftime("%b %-d")
    except ValueError:
        return dt.strftime("%b %#d")

# ---------- Run ----------

if __name__ == "__main__":
    app.run()

