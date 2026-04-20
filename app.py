import os
from datetime import datetime, timedelta, date

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import login_user, current_user, login_required, logout_user
from urllib.parse import urlsplit

import sqlalchemy as sa
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from extensions import db, mail, login_manager
from forms import ConfirmDoseForm, LoginForm, RegistrationForm
from core import add_log, clear_logs, load_logs, get_weekly_summary
from models import Schedule, seed_schedules, User
from mailer import send_email


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
app.config["GP_EMAIL"] = "gp@carebridge.bot"

mail.init_app(app)
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = "login"

scheduler = BackgroundScheduler()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class ReminderEvent(db.Model):
    __tablename__ = "reminder_event"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey("schedule.id"), nullable=False)

    event_type = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")

    scheduled_for = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    parent_event_id = db.Column(db.Integer, db.ForeignKey("reminder_event.id"), nullable=True)
    delay_minutes = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    responded_at = db.Column(db.DateTime, nullable=True)

    email_sent = db.Column(db.Boolean, nullable=False, default=False)
    in_app_sent = db.Column(db.Boolean, nullable=False, default=False)

    schedule = db.relationship("Schedule", backref="reminder_events")
    user = db.relationship("User", backref="reminder_events")
    parent_event = db.relationship("ReminderEvent", remote_side=[id], backref="child_events")


class InAppNotification(db.Model):
    __tablename__ = "in_app_notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reminder_event_id = db.Column(db.Integer, db.ForeignKey("reminder_event.id"), nullable=False)

    message = db.Column(db.String(255), nullable=False)
    link_url = db.Column(db.String(255), nullable=False)

    is_read = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    reminder_event = db.relationship("ReminderEvent", backref="notifications")
    user = db.relationship("User", backref="in_app_notifications")


def now_local() -> datetime:
    return datetime.now()


def parse_time_string(time_string: str):
    return datetime.strptime(time_string.strip(), "%H:%M").time()


def combine_date_and_schedule_time(target_date: date, time_string: str) -> datetime:
    return datetime.combine(target_date, parse_time_string(time_string))


def get_schedules_dict():
    out = {}
    for s in Schedule.query.order_by(Schedule.id).all():
        out[s.id] = {
            "med_name": s.med_name,
            "dosage": s.dosage,
            "time_of_day": s.time_of_day,
        }
    return out


def ensure_db():
    with app.app_context():
        db.create_all()
        if Schedule.query.count() == 0:
            seed_schedules()
            db.session.commit()


def get_active_notifications_for_current_user():
    if not current_user.is_authenticated:
        return []

    return db.session.scalars(
        sa.select(InAppNotification).where(
            InAppNotification.user_id == current_user.id,
            InAppNotification.is_active.is_(True),
        ).order_by(InAppNotification.created_at.desc())
    ).all()


@app.context_processor
def inject_active_notifications():
    return {
        "active_notifications": get_active_notifications_for_current_user()
    }


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.username == form.username.data)
        )
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password')
            return redirect(url_for('login'))

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('home')
        return redirect(next_page)

    return render_template('login.html', title='Sign In', form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Congratulations, you are now a registered user!')
        return redirect(url_for('login'))

    return render_template('register.html', title='Register', form=form)


def active_or_future_event_exists(schedule_id: int, user_id: int) -> bool:
    existing = db.session.scalar(
        sa.select(ReminderEvent).where(
            ReminderEvent.schedule_id == schedule_id,
            ReminderEvent.user_id == user_id,
            ReminderEvent.status.in_(["pending", "sent"]),
        ).limit(1)
    )
    return existing is not None


def create_first_prompt_for_user(schedule: Schedule, user: User, when_dt: datetime) -> ReminderEvent:
    event = ReminderEvent(
        user_id=user.id,
        schedule_id=schedule.id,
        event_type="first_prompt",
        status="pending",
        scheduled_for=when_dt,
        expires_at=when_dt + timedelta(minutes=10),
    )
    db.session.add(event)
    db.session.commit()
    return event


def schedule_next_day_first_prompt(schedule: Schedule, user: User) -> ReminderEvent:
    tomorrow = now_local().date() + timedelta(days=1)
    next_dt = combine_date_and_schedule_time(tomorrow, schedule.time_of_day)
    return create_first_prompt_for_user(schedule, user, next_dt)


def ensure_daily_events_for_user(user: User) -> None:
    schedules = Schedule.query.order_by(Schedule.id).all()
    current_dt = now_local()
    today = current_dt.date()

    for schedule in schedules:
        if active_or_future_event_exists(schedule.id, user.id):
            continue

        scheduled_today = combine_date_and_schedule_time(today, schedule.time_of_day)

        if scheduled_today > current_dt:
            create_first_prompt_for_user(schedule, user, scheduled_today)
        else:
            tomorrow = today + timedelta(days=1)
            scheduled_tomorrow = combine_date_and_schedule_time(tomorrow, schedule.time_of_day)
            create_first_prompt_for_user(schedule, user, scheduled_tomorrow)

    db.session.commit()


def send_email_or_terminal(recipient: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    try:
        send_email(
            subject=subject,
            sender=app.config["MAIL_DEFAULT_SENDER"],
            recipients=[recipient],
            text_body=text_body,
            html_body=html_body,
        )
        print(f"[EMAIL SENT] To: {recipient} | Subject: {subject}")
    except Exception as e:
        print("=" * 80)
        print("[EMAIL FALLBACK TO TERMINAL]")
        print(f"TO: {recipient}")
        print(f"SUBJECT: {subject}")
        print(text_body)
        print(f"Reason real email failed: {e}")
        print("=" * 80)


def create_in_app_notification_for_event(event: ReminderEvent) -> None:
    existing = db.session.scalar(
        sa.select(InAppNotification).where(
            InAppNotification.reminder_event_id == event.id,
            InAppNotification.is_active.is_(True),
        ).limit(1)
    )
    if existing:
        return

    if event.event_type == "first_prompt":
        message = f"Medication reminder: {event.schedule.med_name} ({event.schedule.dosage})"
    else:
        message = f"Follow-up reminder: please confirm {event.schedule.med_name} was taken"

    notification = InAppNotification(
        user_id=event.user_id,
        reminder_event_id=event.id,
        message=message,
        link_url=url_for("respond_to_reminder_event", event_id=event.id),
        is_read=False,
        is_active=True,
    )
    db.session.add(notification)
    event.in_app_sent = True


def deactivate_notifications_for_event(event_id: int) -> None:
    notifications = db.session.scalars(
        sa.select(InAppNotification).where(
            InAppNotification.reminder_event_id == event_id,
            InAppNotification.is_active.is_(True),
        )
    ).all()

    for notification in notifications:
        notification.is_active = False
        notification.is_read = True


def send_due_event_notifications(event: ReminderEvent) -> None:
    subject = "CareBridge Medication Reminder"

    if event.event_type == "first_prompt":
        body = (
            f"Medication reminder for {event.user.username}\n\n"
            f"Medication: {event.schedule.med_name}\n"
            f"Dosage: {event.schedule.dosage}\n"
            f"Scheduled time: {event.schedule.time_of_day}\n\n"
            f"Options:\n"
            f"- Taken\n"
            f"- Remind me later\n"
        )
    else:
        body = (
            f"Follow-up medication reminder for {event.user.username}\n\n"
            f"Medication: {event.schedule.med_name}\n"
            f"Dosage: {event.schedule.dosage}\n\n"
            f"This follow-up only allows:\n"
            f"- Taken\n"
        )

    recipient = event.user.email or app.config["GP_EMAIL"]
    send_email_or_terminal(recipient=recipient, subject=subject, text_body=body)
    event.email_sent = True


def record_taken_and_reset(event: ReminderEvent) -> None:
    event.status = "taken"
    event.responded_at = now_local()

    deactivate_notifications_for_event(event.id)

    add_log(event.schedule_id, event.user.username, "taken")
    schedule_next_day_first_prompt(event.schedule, event.user)

    db.session.commit()


def record_missed_and_reset(event: ReminderEvent) -> None:
    event.status = "missed"
    event.responded_at = now_local()

    deactivate_notifications_for_event(event.id)

    add_log(event.schedule_id, event.user.username, "missed")
    schedule_next_day_first_prompt(event.schedule, event.user)

    db.session.commit()


def create_remind_later_followup(parent_event: ReminderEvent, delay_minutes: int) -> ReminderEvent:
    followup_time = now_local() + timedelta(minutes=delay_minutes)

    child = ReminderEvent(
        user_id=parent_event.user_id,
        schedule_id=parent_event.schedule_id,
        event_type="remind_later_followup",
        status="pending",
        scheduled_for=followup_time,
        expires_at=followup_time + timedelta(minutes=10),
        parent_event_id=parent_event.id,
        delay_minutes=delay_minutes,
    )
    db.session.add(child)

    parent_event.status = "superseded"
    parent_event.responded_at = now_local()
    deactivate_notifications_for_event(parent_event.id)

    db.session.commit()
    return child


def create_auto_followup_after_ignored_first_prompt(parent_event: ReminderEvent) -> ReminderEvent:
    followup_time = now_local() + timedelta(hours=1)

    child = ReminderEvent(
        user_id=parent_event.user_id,
        schedule_id=parent_event.schedule_id,
        event_type="auto_followup",
        status="pending",
        scheduled_for=followup_time,
        expires_at=followup_time + timedelta(minutes=10),
        parent_event_id=parent_event.id,
    )
    db.session.add(child)

    parent_event.status = "superseded"
    parent_event.responded_at = now_local()
    deactivate_notifications_for_event(parent_event.id)

    db.session.commit()
    return child


def process_due_reminders() -> None:
    with app.app_context():
        current_dt = now_local()

        due_events = db.session.scalars(
            sa.select(ReminderEvent).where(
                ReminderEvent.status == "pending",
                ReminderEvent.scheduled_for <= current_dt,
            )
        ).all()

        for event in due_events:
            create_in_app_notification_for_event(event)
            send_due_event_notifications(event)
            event.status = "sent"

        db.session.commit()


def process_expired_reminders() -> None:
    with app.app_context():
        current_dt = now_local()

        expired_events = db.session.scalars(
            sa.select(ReminderEvent).where(
                ReminderEvent.status == "sent",
                ReminderEvent.expires_at <= current_dt,
            )
        ).all()

        for event in expired_events:
            if event.event_type == "first_prompt":
                create_auto_followup_after_ignored_first_prompt(event)
            else:
                record_missed_and_reset(event)

        db.session.commit()


def start_scheduler() -> None:
    if scheduler.running:
        return

    scheduler.add_job(
        func=process_due_reminders,
        trigger=IntervalTrigger(seconds=30),
        id="process_due_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        func=process_expired_reminders,
        trigger=IntervalTrigger(seconds=30),
        id="process_expired_reminders",
        replace_existing=True,
    )
    scheduler.start()


ensure_db()

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    start_scheduler()


@app.route("/")
@login_required
def home():
    ensure_daily_events_for_user(current_user)
    return render_template("home.html", schedules=get_schedules_dict())


@app.route("/reminder/<int:schedule_id>", methods=["GET"])
@login_required
def reminder(schedule_id: int):
    schedule = db.session.get(Schedule, schedule_id)
    if not schedule:
        flash("Schedule not found.")
        return redirect(url_for("home"))

    open_event = db.session.scalar(
        sa.select(ReminderEvent).where(
            ReminderEvent.schedule_id == schedule_id,
            ReminderEvent.user_id == current_user.id,
            ReminderEvent.status.in_(["pending", "sent"]),
        ).order_by(ReminderEvent.created_at.desc())
    )

    if open_event is None:
        open_event = ReminderEvent(
            user_id=current_user.id,
            schedule_id=schedule.id,
            event_type="first_prompt",
            status="sent",
            scheduled_for=now_local(),
            expires_at=now_local() + timedelta(minutes=10),
            email_sent=False,
            in_app_sent=False,
        )
        db.session.add(open_event)
        db.session.commit()

        create_in_app_notification_for_event(open_event)
        send_due_event_notifications(open_event)
        db.session.commit()

    return redirect(url_for("respond_to_reminder_event", event_id=open_event.id))


@app.route("/reminder/respond/<int:event_id>", methods=["GET", "POST"])
@login_required
def respond_to_reminder_event(event_id: int):
    event = db.session.get(ReminderEvent, event_id)

    if not event or event.user_id != current_user.id:
        flash("Reminder not found.")
        return redirect(url_for("home"))

    if event.status not in ["pending", "sent"]:
        flash("This reminder is no longer active.")
        return redirect(url_for("history"))

    form = ConfirmDoseForm()

    if request.method == "GET" and hasattr(form, "username"):
        form.username.data = current_user.username

    if form.validate_on_submit():
        if form.taken.data:
            record_taken_and_reset(event)
            flash("Recorded: Taken.")
            return redirect(url_for("history"))

        if event.event_type == "first_prompt" and form.remind_later.data:
            return redirect(url_for("choose_remind_later_delay", event_id=event.id))

        flash("Please choose a valid action.")
        return redirect(url_for("respond_to_reminder_event", event_id=event.id))

    allow_remind_later = (event.event_type == "first_prompt")

    return render_template(
        "reminder.html",
        sched=event.schedule,
        schedule_id=event.schedule_id,
        form=form,
        event=event,
        allow_remind_later=allow_remind_later,
    )


@app.route("/reminder/<int:event_id>/later", methods=["GET", "POST"])
@login_required
def choose_remind_later_delay(event_id: int):
    event = db.session.get(ReminderEvent, event_id)

    if not event or event.user_id != current_user.id:
        flash("Reminder not found.")
        return redirect(url_for("home"))

    if event.status not in ["pending", "sent"] or event.event_type != "first_prompt":
        flash("This reminder can no longer be postponed.")
        return redirect(url_for("home"))

    if request.method == "POST":
        selected_value = request.form.get("delay_minutes", "").strip()

        if selected_value not in {"30", "60", "120"}:
            flash("Please choose 30 minutes, 1 hour, or 2 hours.")
            return redirect(url_for("choose_remind_later_delay", event_id=event.id))

        delay_minutes = int(selected_value)
        create_remind_later_followup(event, delay_minutes)
        flash(f"Okay — we will remind you again in {delay_minutes} minutes.")
        return redirect(url_for("home"))

    return render_template("remind_later.html", event=event)


@app.route("/notification/<int:notification_id>/open")
@login_required
def open_notification(notification_id: int):
    notification = db.session.get(InAppNotification, notification_id)

    if not notification or notification.user_id != current_user.id:
        flash("Notification not found.")
        return redirect(url_for("home"))

    notification.is_read = True
    db.session.commit()

    return redirect(notification.link_url)


@app.route("/history")
@login_required
def history():
    logs = load_logs()
    return render_template("history.html", logs=logs)


@app.route("/history/clear")
def clear_history():
    clear_logs()
    flash("History cleared.")
    return redirect(url_for("history"))


@app.route("/weekly-summary/view")
@login_required
def weekly_summary_view():
    summary = get_weekly_summary(current_user.username)
    return render_template("weekly_summary.html", summary=summary)


@app.route("/share-report")
@login_required
def share_report():
    summary = get_weekly_summary(current_user.username)

    report = (
        f"Weekly Medication Summary\n\n"
        f"Total doses: {summary['total']}\n"
        f"Taken: {summary['taken']}\n"
        f"Missed: {summary['missed']}\n"
        f"Escalations: {summary['escalations']}\n"
        f"Adherence: {summary['adherence']}%\n"
    )

    return report, 200, {
        "Content-Type": "text/plain",
        "Content-Disposition": "attachment; filename=report.txt"
    }


if __name__ == "__main__":
    app.run(debug=True)
