from datetime import date, datetime, timedelta

from extensions import db
from mailer import send_carer_alert, send_reminder_email
from models import DoseLog, ExerciseLog, ReminderNotification, ReminderPrompt, Schedule, User


DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIME_FORMAT = "%H:%M"


def local_now():
    return datetime.now().replace(microsecond=0)


def format_datetime(value):
    if value is None:
        return ""
    return value.strftime(DATETIME_FORMAT)


def format_time(value):
    if value is None:
        return ""
    return value.strftime(TIME_FORMAT)


def resolve_user(user_identifier):
    if user_identifier is None:
        return None

    if isinstance(user_identifier, int):
        return User.query.get(user_identifier)

    return User.query.filter_by(username=str(user_identifier).strip()).first()


def schedule_due_datetime(schedule, day_value):
    return datetime.combine(day_value, schedule.scheduled_time).replace(microsecond=0)


def build_notification_message(reminder):
    med_name = reminder.schedule.med_name
    dosage = reminder.schedule.dosage
    due_text = format_datetime(reminder.due_at)

    if reminder.stage == "initial":
        return f"Time to take {med_name} ({dosage}). Scheduled for {due_text}."

    if reminder.stage == "followup":
        return f"{med_name} was not answered. Follow-up reminder due at {due_text}."

    return f"{med_name} was snoozed. Reminder due at {due_text}."


def create_notification(reminder, channel, message, now):
    existing = ReminderNotification.query.filter_by(
        reminder_id=reminder.id,
        channel=channel,
        message=message,
    ).first()

    if existing:
        return existing

    notification = ReminderNotification(
        user_id=reminder.user_id,
        reminder_id=reminder.id,
        channel=channel,
        message=message,
        created_at=now,
    )
    db.session.add(notification)
    return notification


def log_dose(reminder, status, now):
    existing = DoseLog.query.filter_by(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        day=reminder.day,
    ).first()

    if existing:
        existing.status = status
        existing.when = now
        existing.reminder_id = reminder.id
        return existing

    entry = DoseLog(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        reminder_id=reminder.id,
        when=now,
        day=reminder.day,
        username=reminder.user.username,
        status=status,
    )
    db.session.add(entry)
    return entry


def should_alert_carer(user_id):
    logs = DoseLog.query.filter_by(user_id=user_id).order_by(DoseLog.when.desc()).limit(2).all()

    if len(logs) < 2:
        return False

    return logs[0].status == "missed" and logs[1].status == "missed"


def send_carer_alert_if_needed(reminder, dose_log, now):
    if not reminder.user.carer_email:
        return

    if not should_alert_carer(reminder.user_id):
        return

    delivery = send_carer_alert(reminder, dose_log, reminder.user.carer_email)
    create_notification(reminder, delivery["channel"], delivery["message"], now)


def close_other_pending_reminders(reminder, now):
    reminders = ReminderPrompt.query.filter_by(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        day=reminder.day,
        status="pending",
    ).all()

    for other in reminders:
        if other.id != reminder.id:
            other.status = "superseded"
            other.responded_at = now


def ensure_daily_reminders(now=None):
    now = now or local_now()
    schedules = Schedule.query.filter_by(active=True).all()

    for schedule in schedules:
        due_at = schedule_due_datetime(schedule, now.date())
        if now < due_at:
            continue

        existing = ReminderPrompt.query.filter_by(
            schedule_id=schedule.id,
            day=now.date().isoformat(),
            stage="initial",
        ).first()

        if existing:
            continue

        reminder = ReminderPrompt(
            user_id=schedule.user_id,
            schedule_id=schedule.id,
            day=now.date().isoformat(),
            stage="initial",
            status="pending",
            due_at=due_at,
            original_due_at=due_at,
            expires_at=due_at + timedelta(minutes=10),
            created_at=now,
        )
        db.session.add(reminder)


def send_due_notifications(now=None):
    now = now or local_now()
    reminders = ReminderPrompt.query.filter(
        ReminderPrompt.status == "pending",
        ReminderPrompt.due_at <= now,
    ).all()

    for reminder in reminders:
        message = build_notification_message(reminder)
        create_notification(reminder, "in_app", message, now)

        if reminder.email_sent_at is None:
            delivery = send_reminder_email(reminder, reminder.user.email, message)
            create_notification(reminder, delivery["channel"], delivery["message"], now)
            reminder.email_sent_at = now


def make_follow_up_reminders(now=None):
    now = now or local_now()
    reminders = ReminderPrompt.query.filter_by(stage="initial", status="pending").filter(
        ReminderPrompt.expires_at <= now
    ).all()

    for reminder in reminders:
        reminder.status = "superseded"
        reminder.responded_at = now

        existing = ReminderPrompt.query.filter_by(parent_id=reminder.id, stage="followup").first()
        if existing:
            continue

        followup_due_at = reminder.original_due_at + timedelta(hours=1)
        if followup_due_at < now:
            followup_due_at = now

        followup = ReminderPrompt(
            user_id=reminder.user_id,
            schedule_id=reminder.schedule_id,
            parent_id=reminder.id,
            day=reminder.day,
            stage="followup",
            status="pending",
            due_at=followup_due_at,
            original_due_at=reminder.original_due_at,
            expires_at=followup_due_at + timedelta(minutes=10),
            created_at=now,
        )
        db.session.add(followup)


def mark_missed_reminders(now=None):
    now = now or local_now()
    reminders = ReminderPrompt.query.filter(
        ReminderPrompt.stage.in_(["followup", "snoozed"]),
        ReminderPrompt.status == "pending",
        ReminderPrompt.expires_at <= now,
    ).all()

    for reminder in reminders:
        reminder.status = "missed"
        reminder.responded_at = now
        dose_log = log_dose(reminder, "missed", now)
        create_notification(
            reminder,
            "status",
            f"{reminder.schedule.med_name} was marked as missed.",
            now,
        )
        send_carer_alert_if_needed(reminder, dose_log, now)


def run_reminder_engine(now=None):
    now = now or local_now()
    ensure_daily_reminders(now)
    make_follow_up_reminders(now)
    mark_missed_reminders(now)
    send_due_notifications(now)
    db.session.commit()


def mark_reminder_taken(reminder_id, user_id, now=None):
    now = now or local_now()
    reminder = ReminderPrompt.query.get(reminder_id)

    if reminder is None or reminder.user_id != user_id or reminder.status != "pending":
        return None

    reminder.status = "taken"
    reminder.responded_at = now
    close_other_pending_reminders(reminder, now)
    log_dose(reminder, "taken", now)
    create_notification(reminder, "status", f"{reminder.schedule.med_name} was recorded as taken.", now)
    db.session.commit()
    return reminder


def mark_reminder_skipped(reminder_id, user_id, now=None):
    now = now or local_now()
    reminder = ReminderPrompt.query.get(reminder_id)

    if reminder is None or reminder.user_id != user_id or reminder.status != "pending":
        return None

    if reminder.email_sent_at is None:
        return None

    reminder.status = "missed"
    reminder.responded_at = now
    close_other_pending_reminders(reminder, now)
    dose_log = log_dose(reminder, "missed", now)
    create_notification(reminder, "status", f"{reminder.schedule.med_name} was marked as skipped.", now)
    send_carer_alert_if_needed(reminder, dose_log, now)
    db.session.commit()
    return reminder


def snooze_reminder(reminder_id, user_id, minutes, now=None):
    now = now or local_now()

    if minutes not in [30, 60, 120]:
        return None

    reminder = ReminderPrompt.query.get(reminder_id)
    if reminder is None or reminder.user_id != user_id or reminder.status != "pending":
        return None

    if reminder.stage != "initial":
        return None

    reminder.status = "superseded"
    reminder.responded_at = now

    snoozed = ReminderPrompt(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        parent_id=reminder.id,
        day=reminder.day,
        stage="snoozed",
        status="pending",
        due_at=now + timedelta(minutes=minutes),
        original_due_at=reminder.original_due_at,
        expires_at=now + timedelta(minutes=minutes + 10),
        created_at=now,
    )
    db.session.add(snoozed)
    db.session.flush()

    create_notification(
        snoozed,
        "status",
        f"{snoozed.schedule.med_name} was snoozed for {minutes} minutes.",
        now,
    )
    db.session.commit()
    return snoozed


def get_active_reminders(user_id, now=None):
    now = now or local_now()
    return ReminderPrompt.query.filter(
        ReminderPrompt.user_id == user_id,
        ReminderPrompt.status == "pending",
        ReminderPrompt.due_at <= now,
    ).order_by(ReminderPrompt.due_at.asc()).all()


def get_schedule_active_reminder(schedule_id, user_id, now=None):
    now = now or local_now()
    return ReminderPrompt.query.filter(
        ReminderPrompt.user_id == user_id,
        ReminderPrompt.schedule_id == schedule_id,
        ReminderPrompt.status == "pending",
        ReminderPrompt.due_at <= now,
    ).order_by(ReminderPrompt.due_at.asc()).first()


def get_recent_notifications(user_id, limit=8):
    return ReminderNotification.query.filter_by(user_id=user_id).order_by(
        ReminderNotification.created_at.desc()
    ).limit(limit).all()


def get_status_notifications(user_id, limit=3):
    return ReminderNotification.query.filter_by(user_id=user_id, channel="status").order_by(
        ReminderNotification.created_at.desc()
    ).limit(limit).all()


def load_logs(user_identifier=None):
    query = DoseLog.query.order_by(DoseLog.when.desc())
    user = resolve_user(user_identifier)

    if user is not None:
        query = query.filter_by(user_id=user.id)

    rows = query.all()
    out = []

    for row in rows:
        scheduled_for = ""
        if row.schedule:
            scheduled_for = f"{row.day} {format_time(row.schedule.scheduled_time)}"

        out.append(
            {
                "when": format_datetime(row.when),
                "logged_at": format_datetime(row.when),
                "day": row.day,
                "dose_date": row.day,
                "schedule_id": row.schedule_id,
                "med_name": row.schedule.med_name if row.schedule else "",
                "username": row.username,
                "scheduled_for": scheduled_for,
                "status": row.status,
            }
        )

    return out


def already_logged_today(schedule_id, user_identifier):
    user = resolve_user(user_identifier)
    if user is None:
        return False

    today = date.today().isoformat()
    existing = DoseLog.query.filter_by(
        user_id=user.id,
        schedule_id=schedule_id,
        day=today,
    ).first()
    return existing is not None


def add_log(schedule_id, user_identifier, status, reminder_id=None, when=None):
    user = resolve_user(user_identifier)
    schedule = Schedule.query.get(schedule_id)
    now = when or local_now()

    if user is None or schedule is None:
        return None

    day_value = now.date().isoformat()
    entry = DoseLog.query.filter_by(user_id=user.id, schedule_id=schedule_id, day=day_value).first()

    if entry is None:
        entry = DoseLog(
            user_id=user.id,
            schedule_id=schedule_id,
            reminder_id=reminder_id,
            when=now,
            day=day_value,
            username=user.username,
            status=status,
        )
        db.session.add(entry)
    else:
        entry.when = now
        entry.status = status
        entry.reminder_id = reminder_id

    db.session.commit()
    return entry


def clear_logs(user_identifier=None):
    user = resolve_user(user_identifier)

    if user is None and user_identifier is not None:
        return

    if user is None:
        DoseLog.query.delete()
    else:
        DoseLog.query.filter_by(user_id=user.id).delete()

    db.session.commit()


def clear_user_logs(user_id):
    clear_logs(user_id)


def get_daily_status(user_id):
    now = local_now()
    today = now.date().isoformat()
    schedules = Schedule.query.filter_by(user_id=user_id).order_by(Schedule.scheduled_time.asc()).all()
    rows = []

    for schedule in schedules:
        log = DoseLog.query.filter_by(user_id=user_id, schedule_id=schedule.id, day=today).first()

        if not schedule.active:
            status = "Inactive"
        elif log is not None:
            status = log.status.title()
        else:
            status = "Pending"

        rows.append(
            {
                "med_name": schedule.med_name,
                "dosage": schedule.dosage,
                "time": format_time(schedule.scheduled_time),
                "status": status,
            }
        )

    return rows


def complete_today_exercise(user_id):
    today = date.today().isoformat()
    existing = ExerciseLog.query.filter_by(user_id=user_id, exercise_date=today).first()

    if existing:
        return existing

    log = ExerciseLog(
        user_id=user_id,
        exercise_date=today,
        completed_at=local_now(),
    )
    db.session.add(log)
    db.session.commit()
    return log


def get_exercise_logs(user_id):
    return ExerciseLog.query.filter_by(user_id=user_id).order_by(ExerciseLog.completed_at.desc()).all()


def get_weekly_exercise_summary(user_id):
    now = local_now()
    period_start = now - timedelta(days=6)
    logs = ExerciseLog.query.filter(
        ExerciseLog.user_id == user_id,
        ExerciseLog.completed_at >= period_start,
        ExerciseLog.completed_at <= now,
    ).all()

    return {
        "completed_days": len(logs),
        "period_start": format_datetime(period_start),
        "period_end": format_datetime(now),
    }


def get_weekly_summary(user_identifier=None):
    query = DoseLog.query
    user = resolve_user(user_identifier)

    if user is not None:
        query = query.filter_by(user_id=user.id)

    period_end = local_now()
    period_start = period_end - timedelta(days=7)
    logs = query.filter(DoseLog.when >= period_start, DoseLog.when <= period_end).all()

    total = len(logs)
    taken = 0
    missed = 0

    for log in logs:
        if log.status == "taken":
            taken += 1
        elif log.status == "missed":
            missed += 1

    escalations = 0
    if user is not None:
        escalations = ReminderPrompt.query.filter(
            ReminderPrompt.user_id == user.id,
            ReminderPrompt.created_at >= period_start,
            ReminderPrompt.created_at <= period_end,
            ReminderPrompt.stage.in_(["followup", "snoozed"]),
        ).count()

    adherence = round((taken / total) * 100, 2) if total > 0 else 0.0

    return {
        "total": total,
        "taken": taken,
        "missed": missed,
        "escalations": escalations,
        "adherence": adherence,
        "period_start": format_datetime(period_start),
        "period_end": format_datetime(period_end),
    }
