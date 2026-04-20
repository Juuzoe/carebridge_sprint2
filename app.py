
import os
import threading
import time
from functools import wraps
from urllib.parse import urlsplit

import sqlalchemy as sa
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

from core import (
    format_datetime,
    get_active_reminders,
    get_recent_notifications,
    get_status_notifications,
    get_user_logs,
    get_weekly_summary,
    local_now,
    run_reminder_engine,
    serialize_reminder,
)
from extensions import db, mail
from forms import LoginForm, MedicationForm, RegistrationForm
from models import MedicationSchedule, User

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key"

basedir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(basedir, "instance")
os.makedirs(instance_dir, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(instance_dir, "carebridge.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["MAIL_SERVER"] = "127.0.0.1"
app.config["MAIL_PORT"] = 8025
app.config["MAIL_USE_TLS"] = False
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_DEFAULT_SENDER"] = "noreply@carebridge.com"
app.config["REMINDER_ENGINE_INTERVAL_SECONDS"] = 20

mail.init_app(app)
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_status_banners():
    if not current_user.is_authenticated:
        return {"status_banners": []}
    return {"status_banners": get_status_notifications(current_user.id)}


def ensure_db():
    with app.app_context():
        db.create_all()


def start_background_worker():
    if app.extensions.get("carebridge_worker_started"):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    def worker():
        while True:
            with app.app_context():
                run_reminder_engine()
            time.sleep(app.config["REMINDER_ENGINE_INTERVAL_SECONDS"])

    thread = threading.Thread(target=worker, name="carebridge-reminder-worker", daemon=True)
    thread.start()
    app.extensions["carebridge_worker_started"] = True


def run_engine_before_view(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        run_reminder_engine()
        return view(*args, **kwargs)

    return wrapped


ensure_db()
start_background_worker()


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(sa.select(User).where(User.username == form.username.data.strip()))
        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get("next")
        if not next_page or urlsplit(next_page).netloc != "":
            next_page = url_for("home")
        return redirect(next_page)

    return render_template("login.html", title="Sign In", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data.strip(), email=form.email.data.strip())
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("Registration complete. You can sign in now.")
        return redirect(url_for("login"))

    return render_template("register.html", title="Register", form=form)


@app.route("/")
@login_required
@run_engine_before_view
def home():
    schedules = (
        MedicationSchedule.query.filter_by(user_id=current_user.id)
        .order_by(MedicationSchedule.scheduled_time.asc(), MedicationSchedule.med_name.asc())
        .all()
    )
    reminders = get_active_reminders(current_user.id)
    notifications = get_recent_notifications(current_user.id)
    return render_template(
        "home.html",
        now=format_datetime(local_now()),
        schedules=schedules,
        reminders=reminders,
        notifications=notifications,
    )


@app.route("/medications/new", methods=["GET", "POST"])
@login_required
def create_medication():
    form = MedicationForm()
    if form.validate_on_submit():
        schedule = MedicationSchedule(
            user_id=current_user.id,
            med_name=form.med_name.data.strip(),
            dosage=form.dosage.data.strip(),
            scheduled_time=form.scheduled_time.data,
            email=(form.email.data or current_user.email or "").strip() or None,
            active=form.active.data,
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Medication schedule added.")
        return redirect(url_for("home"))

    return render_template("medication_form.html", title="Add Medication", form=form, mode="add")


@app.route("/medications/<int:schedule_id>/edit", methods=["GET", "POST"])
@login_required
def edit_medication(schedule_id):
    schedule = db.session.get(MedicationSchedule, schedule_id)
    if schedule is None or schedule.user_id != current_user.id:
        flash("Medication not found.")
        return redirect(url_for("home"))

    form = MedicationForm(obj=schedule)
    if form.validate_on_submit():
        schedule.med_name = form.med_name.data.strip()
        schedule.dosage = form.dosage.data.strip()
        schedule.scheduled_time = form.scheduled_time.data
        schedule.email = (form.email.data or current_user.email or "").strip() or None
        schedule.active = form.active.data
        db.session.commit()
        flash("Medication schedule updated.")
        return redirect(url_for("home"))

    return render_template("medication_form.html", title="Edit Medication", form=form, mode="edit")


@app.route("/medications/<int:schedule_id>/delete", methods=["POST"])
@login_required
def delete_medication(schedule_id):
    schedule = db.session.get(MedicationSchedule, schedule_id)
    if schedule is None or schedule.user_id != current_user.id:
        flash("Medication not found.")
        return redirect(url_for("home"))

    db.session.delete(schedule)
    db.session.commit()
    flash("Medication schedule deleted.")
    return redirect(url_for("home"))


@app.route("/reminders/<int:reminder_id>")
@login_required
@run_engine_before_view
def reminder_detail(reminder_id):
    reminder = next((item for item in get_active_reminders(current_user.id) if item.id == reminder_id), None)
    if reminder is None:
        flash("Reminder not found or no longer active.")
        return redirect(url_for("home"))
    return render_template("reminder.html", reminder=reminder)


@app.route("/reminder/<int:schedule_id>")
@login_required
@run_engine_before_view
def legacy_schedule_reminder(schedule_id):
    reminder = next((item for item in get_active_reminders(current_user.id) if item.schedule_id == schedule_id), None)
    if reminder is None:
        flash("There is no active reminder for that medication right now.")
        return redirect(url_for("home"))
    return redirect(url_for("reminder_detail", reminder_id=reminder.id))


@app.route("/reminders/<int:reminder_id>/taken", methods=["POST"])
@login_required
def reminder_taken(reminder_id):
    from core import mark_reminder_taken

    reminder = mark_reminder_taken(reminder_id, current_user.id)
    if reminder is None:
        flash("Reminder could not be updated.")
    else:
        flash("Your response has been recorded.")
    return redirect(request.referrer or url_for("home"))


@app.route("/reminders/<int:reminder_id>/remind-later", methods=["POST"])
@login_required
def reminder_later(reminder_id):
    from core import snooze_reminder

    minutes = request.form.get("minutes", type=int)
    reminder = snooze_reminder(reminder_id, current_user.id, minutes)
    if reminder is None:
        flash("Unable to schedule a later reminder.")
    else:
        flash(f"Reminder moved to {format_datetime(reminder.due_at)}.")
    return redirect(request.referrer or url_for("home"))


@app.route("/api/reminders/poll")
@login_required
def reminder_poll():
    run_reminder_engine()
    reminders = [serialize_reminder(item) for item in get_active_reminders(current_user.id)]
    notifications = [
        {
            "id": item.id,
            "message": item.message,
            "channel": item.channel,
            "created_at": format_datetime(item.created_at),
        }
        for item in get_recent_notifications(current_user.id)
    ]
    return jsonify({"reminders": reminders, "notifications": notifications})


@app.route("/history")
@login_required
@run_engine_before_view
def history():
    logs = get_user_logs(current_user.id)
    return render_template("history.html", logs=logs)


@app.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    from core import clear_user_logs

    clear_user_logs(current_user.id)
    flash("History cleared.")
    return redirect(url_for("history"))


@app.route("/weekly-summary/view")
@login_required
@run_engine_before_view
def weekly_summary_view():
    summary = get_weekly_summary(current_user.id)
    return render_template("weekly_summary.html", summary=summary)


@app.route("/share-report")
@login_required
@run_engine_before_view
def share_report():
    summary = get_weekly_summary(current_user.id)
    report = (
        "Weekly Medication Summary\n\n"
        f"Generated at: {format_datetime(local_now())}\n"
        f"Total doses: {summary['total']}\n"
        f"Taken: {summary['taken']}\n"
        f"Missed: {summary['missed']}\n"
        f"Escalations: {summary['escalations']}\n"
        f"Adherence: {summary['adherence']}%\n"
    )
    return report, 200, {
        "Content-Type": "text/plain",
        "Content-Disposition": "attachment; filename=report.txt",
    }


if __name__ == "__main__":
    app.run(debug=True)
