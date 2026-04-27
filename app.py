import os
from secrets import token_hex

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

from core import (
    clear_user_logs,
    complete_today_exercise,
    format_datetime,
    get_active_reminders,
    get_daily_status,
    get_exercise_logs,
    get_recent_notifications,
    get_schedule_active_reminder,
    get_status_notifications,
    get_weekly_exercise_summary,
    get_weekly_summary,
    load_logs,
    local_now,
    mark_reminder_skipped,
    mark_reminder_taken,
    run_reminder_engine,
    snooze_reminder,
)
from extensions import db, mail
from forms import AccountSettingsForm, ConfirmDoseForm, LoginForm, MedicationForm, RegistrationForm
from mailer import send_gp_summary
from models import Schedule, User, seed_schedules


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

basedir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(basedir, "instance")
os.makedirs(instance_dir, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(instance_dir, "carebridge.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "127.0.0.1")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "8025"))
app.config["MAIL_USE_TLS"] = False
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@carebridge.com")

mail.init_app(app)
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def add_status_banners():
    if current_user.is_authenticated:
        banners = get_status_notifications(current_user.id)
    else:
        banners = []

    return {"status_banners": banners}


def create_database_tables():
    with app.app_context():
        db.create_all()


def ensure_db():
    create_database_tables()


def check_reminders():
    run_reminder_engine()


def make_carer_code():
    code = token_hex(4)

    while User.query.filter_by(carer_code=code).first():
        code = token_hex(4)

    return code


def make_sure_user_has_carer_code(user):
    if not user.carer_code:
        user.carer_code = make_carer_code()
        db.session.commit()


def get_schedules_dict(user_id):
    out = {}

    for schedule in Schedule.query.filter_by(user_id=user_id).order_by(Schedule.id).all():
        out[schedule.id] = {
            "med_name": schedule.med_name,
            "dosage": schedule.dosage,
            "time_of_day": schedule.time_of_day,
            "active": schedule.active,
        }

    return out


ensure_db()


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()

        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        login_user(user, remember=form.remember_me.data)
        flash("You are now signed in.")
        return redirect(url_for("home"))

    return render_template("login.html", title="Sign In", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.")
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    form = RegistrationForm()

    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.strip(),
            carer_code=make_carer_code(),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        seed_schedules(user.id)
        db.session.commit()
        flash("Registration complete. You can sign in now.")
        return redirect(url_for("login"))

    return render_template("register.html", title="Register", form=form)


@app.route("/")
@login_required
def home():
    check_reminders()

    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.scheduled_time.asc()).all()
    reminders = get_active_reminders(current_user.id)
    notifications = get_recent_notifications(current_user.id)
    daily_status = get_daily_status(current_user.id)

    return render_template(
        "home.html",
        title="Home",
        now=format_datetime(local_now()),
        schedules=schedules,
        schedules_dict=get_schedules_dict(current_user.id),
        reminders=reminders,
        notifications=notifications,
        daily_status=daily_status,
    )


@app.route("/account/settings", methods=["GET", "POST"])
@login_required
def account_settings():
    make_sure_user_has_carer_code(current_user)
    form = AccountSettingsForm()

    if form.validate_on_submit():
        current_user.email = form.email.data.strip()
        current_user.carer_email = form.carer_email.data.strip()
        current_user.gp_email = form.gp_email.data.strip()
        db.session.commit()
        flash("Account contact details updated.")
        return redirect(url_for("account_settings"))

    if request.method == "GET":
        form.email.data = current_user.email
        form.carer_email.data = current_user.carer_email
        form.gp_email.data = current_user.gp_email

    return render_template("account_settings.html", title="Account Settings", form=form)


@app.route("/carer", methods=["GET", "POST"])
def carer_login():
    if request.method == "POST":
        carer_code = request.form.get("carer_code", "").strip()

        if not carer_code:
            flash("Please enter a carer access code.")
            return redirect(url_for("carer_login"))

        user = User.query.filter_by(carer_code=carer_code).first()

        if user is None:
            flash("Carer access code was not found.")
            return redirect(url_for("carer_login"))

        return redirect(url_for("carer_portal", carer_code=carer_code))

    return render_template("carer_login.html", title="Carer Portal")


@app.route("/medications/new", methods=["GET", "POST"])
@login_required
def create_medication():
    form = MedicationForm()

    if form.validate_on_submit():
        schedule = Schedule(
            user_id=current_user.id,
            med_name=form.med_name.data.strip(),
            dosage=form.dosage.data.strip(),
            scheduled_time=form.scheduled_time.data,
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
    schedule = Schedule.query.get(schedule_id)

    if schedule is None or schedule.user_id != current_user.id:
        flash("Medication not found.")
        return redirect(url_for("home"))

    form = MedicationForm(obj=schedule)

    if form.validate_on_submit():
        schedule.med_name = form.med_name.data.strip()
        schedule.dosage = form.dosage.data.strip()
        schedule.scheduled_time = form.scheduled_time.data
        schedule.active = form.active.data
        db.session.commit()
        flash("Medication schedule updated.")
        return redirect(url_for("home"))

    return render_template("medication_form.html", title="Edit Medication", form=form, mode="edit")


@app.route("/medications/<int:schedule_id>/delete", methods=["POST"])
@login_required
def delete_medication(schedule_id):
    schedule = Schedule.query.get(schedule_id)

    if schedule is None or schedule.user_id != current_user.id:
        flash("Medication not found.")
        return redirect(url_for("home"))

    db.session.delete(schedule)
    db.session.commit()
    flash("Medication schedule deleted.")
    return redirect(url_for("home"))


@app.route("/reminder/<int:schedule_id>")
@login_required
def reminder(schedule_id):
    check_reminders()

    reminder_item = get_schedule_active_reminder(schedule_id, current_user.id)
    if reminder_item is None:
        flash("There is no active reminder for that medication right now.")
        return redirect(url_for("home"))

    return redirect(url_for("reminder_detail", reminder_id=reminder_item.id))


@app.route("/reminders/<int:reminder_id>")
@login_required
def reminder_detail(reminder_id):
    check_reminders()
    reminder_item = next((item for item in get_active_reminders(current_user.id) if item.id == reminder_id), None)

    if reminder_item is None:
        flash("Reminder not found or no longer active.")
        return redirect(url_for("home"))

    form = ConfirmDoseForm()
    return render_template("reminder.html", title="Reminder", reminder=reminder_item, form=form)


@app.route("/reminders/<int:reminder_id>/taken", methods=["POST"])
@login_required
def reminder_taken(reminder_id):
    reminder_item = mark_reminder_taken(reminder_id, current_user.id)

    if reminder_item is None:
        flash("Reminder could not be updated.")
    else:
        flash("Recorded: Taken.")

    return redirect(url_for("history"))


@app.route("/reminders/<int:reminder_id>/skipped", methods=["POST"])
@login_required
def reminder_skipped(reminder_id):
    reminder_item = mark_reminder_skipped(reminder_id, current_user.id)

    if reminder_item is None:
        flash("Reminder could not be marked as skipped.")
    else:
        flash("Recorded: Skipped.")

    return redirect(url_for("history"))


@app.route("/reminders/<int:reminder_id>/remind-later", methods=["POST"])
@login_required
def reminder_later(reminder_id):
    minutes = request.form.get("minutes", type=int)
    reminder_item = snooze_reminder(reminder_id, current_user.id, minutes)

    if reminder_item is None:
        flash("Unable to schedule a later reminder.")
    else:
        flash(f"Recorded: Remind me later. New reminder due at {format_datetime(reminder_item.due_at)}.")

    return redirect(url_for("home"))


@app.route("/history")
@login_required
def history():
    check_reminders()
    logs = load_logs(current_user.id)
    return render_template("history.html", title="History", logs=logs)


@app.route("/history/clear", methods=["GET", "POST"])
@login_required
def clear_history():
    clear_user_logs(current_user.id)
    flash("History cleared.")
    return redirect(url_for("history"))


@app.route("/exercise")
@login_required
def exercise():
    logs = get_exercise_logs(current_user.id)
    summary = get_weekly_exercise_summary(current_user.id)
    return render_template("exercise.html", title="Exercise", logs=logs, summary=summary)


@app.route("/exercise/complete", methods=["POST"])
@login_required
def exercise_complete():
    complete_today_exercise(current_user.id)
    flash("Today's exercise has been recorded.")
    return redirect(url_for("exercise"))


@app.route("/carer/status")
@login_required
def carer_status():
    check_reminders()
    make_sure_user_has_carer_code(current_user)

    daily_status = get_daily_status(current_user.id)
    medication_summary = get_weekly_summary(current_user.id)
    exercise_summary = get_weekly_exercise_summary(current_user.id)

    return render_template(
        "carer_status.html",
        title="Carer Status",
        daily_status=daily_status,
        medication_summary=medication_summary,
        exercise_summary=exercise_summary,
    )


@app.route("/carer/<carer_code>")
def carer_portal(carer_code):
    user = User.query.filter_by(carer_code=carer_code).first()

    if user is None:
        flash("Carer access code was not found.")
        return redirect(url_for("carer_login"))

    check_reminders()

    daily_status = get_daily_status(user.id)
    medication_summary = get_weekly_summary(user.id)
    exercise_summary = get_weekly_exercise_summary(user.id)
    notifications = get_recent_notifications(user.id, limit=5)

    return render_template(
        "carer_portal.html",
        title="Carer Portal",
        patient=user,
        daily_status=daily_status,
        medication_summary=medication_summary,
        exercise_summary=exercise_summary,
        notifications=notifications,
    )


@app.route("/api/carer/status/<carer_code>")
def api_carer_status(carer_code):
    user = User.query.filter_by(carer_code=carer_code).first()

    if user is None:
        return jsonify({"error": "Carer access code was not found."}), 404

    check_reminders()

    return jsonify(
        {
            "patient": user.username,
            "medications": get_daily_status(user.id),
            "weekly_medication_summary": get_weekly_summary(user.id),
            "weekly_exercise_summary": get_weekly_exercise_summary(user.id),
        }
    )


@app.route("/weekly-summary/view")
@login_required
def weekly_summary_view():
    check_reminders()
    summary = get_weekly_summary(current_user.id)
    exercise_summary = get_weekly_exercise_summary(current_user.id)
    return render_template("weeklysummary.html", title="Weekly Summary", summary=summary, exercise_summary=exercise_summary)


@app.route("/share-report")
@login_required
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


@app.route("/share-report/email-gp", methods=["POST"])
@login_required
def email_gp_report():
    if not current_user.gp_email:
        flash("Please add a GP email address in account settings first.")
        return redirect(url_for("weekly_summary_view"))

    summary = get_weekly_summary(current_user.id)
    delivery = send_gp_summary(current_user, summary, current_user.gp_email)
    flash(delivery["message"])
    return redirect(url_for("weekly_summary_view"))


if __name__ == "__main__":
    app.run(debug=True)

