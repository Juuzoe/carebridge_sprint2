from datetime import datetime, timedelta

import sqlalchemy as sa

from extensions import db
from mailer import send_reminder_email
from models import DoseLog, MedicationSchedule, ReminderNotification, ReminderPrompt

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIME_FORMAT = "%H:%M"


def local_now():
    return datetime.now().replace(microsecond=0)


def format_datetime(value):
    return value.strftime(DATETIME_FORMAT)


def format_time(value):
    return value.strftime(TIME_FORMAT)


def _schedule_due_datetime(schedule, day):
    return datetime.combine(day, schedule.scheduled_time).replace(microsecond=0)


def _build_notification_message(reminder):
    schedule = reminder.schedule
    due_text = format_datetime(reminder.due_at)
    if reminder.stage == "initial":
        return f"Time to take {schedule.med_name} ({schedule.dosage}). Scheduled for {due_text}."
    if reminder.stage == "followup":
        return f"{schedule.med_name} was not answered within 10 minutes. Follow-up reminder due at {due_text}."
    return f"{schedule.med_name} was snoozed. Reminder due at {due_text}."


def _create_notification(reminder, channel, message, now):
    existing = db.session.scalar(
        sa.select(ReminderNotification).where(
            ReminderNotification.reminder_id == reminder.id,
            ReminderNotification.channel == channel,
        )
    )
    if existing is not None:
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


def _log_result(reminder, status, now):
    existing = db.session.scalar(
        sa.select(DoseLog).where(
            DoseLog.user_id == reminder.user_id,
            DoseLog.schedule_id == reminder.schedule_id,
            DoseLog.dose_date == reminder.dose_date,
        )
    )
    if existing is not None:
        existing.status = status
        existing.logged_at = now
        existing.reminder_id = reminder.id
        return existing

    entry = DoseLog(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        reminder_id=reminder.id,
        dose_date=reminder.dose_date,
        scheduled_for=reminder.original_due_at,
        logged_at=now,
        status=status,
    )
    db.session.add(entry)
    return entry


def _supersede_related_pending_reminders(reminder, now):
    siblings = db.session.scalars(
        sa.select(ReminderPrompt).where(
            ReminderPrompt.user_id == reminder.user_id,
            ReminderPrompt.schedule_id == reminder.schedule_id,
            ReminderPrompt.dose_date == reminder.dose_date,
            ReminderPrompt.status == "pending",
            ReminderPrompt.id != reminder.id,
        )
    ).all()
    for sibling in siblings:
        sibling.status = "superseded"
        sibling.responded_at = now


def ensure_daily_reminders(now=None):
    now = now or local_now()
    schedules = db.session.scalars(
        sa.select(MedicationSchedule).where(MedicationSchedule.active.is_(True))
    ).all()
    for schedule in schedules:
        due_at = _schedule_due_datetime(schedule, now.date())
        if now < due_at:
            continue

        existing = db.session.scalar(
            sa.select(ReminderPrompt).where(
                ReminderPrompt.schedule_id == schedule.id,
                ReminderPrompt.dose_date == now.date().isoformat(),
                ReminderPrompt.stage == "initial",
            )
        )
        if existing is not None:
            continue

        reminder = ReminderPrompt(
            user_id=schedule.user_id,
            schedule_id=schedule.id,
            dose_date=now.date().isoformat(),
            stage="initial",
            status="pending",
            due_at=due_at,
            original_due_at=due_at,
            expires_at=due_at + timedelta(minutes=10),
            created_at=now,
        )
        db.session.add(reminder)


def dispatch_due_notifications(now=None):
    now = now or local_now()
    due_reminders = db.session.scalars(
        sa.select(ReminderPrompt).where(
            ReminderPrompt.status == "pending",
            ReminderPrompt.due_at <= now,
        )
    ).all()

    for reminder in due_reminders:
        message = _build_notification_message(reminder)
        _create_notification(reminder, "in_app", message, now)

        if reminder.email_sent_at is None:
            recipient = reminder.schedule.email or reminder.user.email
            send_reminder_email(reminder, recipient, message)
            reminder.email_sent_at = now


def expire_ignored_initial_reminders(now=None):
    now = now or local_now()
    ignored = db.session.scalars(
        sa.select(ReminderPrompt).where(
            ReminderPrompt.stage == "initial",
            ReminderPrompt.status == "pending",
            ReminderPrompt.expires_at <= now,
        )
    ).all()

    for reminder in ignored:
        reminder.status = "superseded"
        reminder.responded_at = now

        existing_followup = db.session.scalar(
            sa.select(ReminderPrompt).where(
                ReminderPrompt.parent_id == reminder.id,
                ReminderPrompt.stage == "followup",
            )
        )
        if existing_followup is not None:
            continue

        followup_due_at = reminder.original_due_at + timedelta(hours=1)
        if followup_due_at < now:
            followup_due_at = now

        followup = ReminderPrompt(
            user_id=reminder.user_id,
            schedule_id=reminder.schedule_id,
            parent_id=reminder.id,
            dose_date=reminder.dose_date,
            stage="followup",
            status="pending",
            due_at=followup_due_at,
            original_due_at=reminder.original_due_at,
            expires_at=followup_due_at + timedelta(minutes=10),
            created_at=now,
        )
        db.session.add(followup)


def expire_secondary_reminders(now=None):
    now = now or local_now()
    ignored = db.session.scalars(
        sa.select(ReminderPrompt).where(
            ReminderPrompt.stage.in_(("followup", "snoozed")),
            ReminderPrompt.status == "pending",
            ReminderPrompt.expires_at <= now,
        )
    ).all()

    for reminder in ignored:
        reminder.status = "missed"
        reminder.responded_at = now
        _log_result(reminder, "missed", now)
        _create_notification(
            reminder,
            "status",
            f"{reminder.schedule.med_name} was marked as missed.",
            now,
        )


def run_reminder_engine(now=None):
    now = now or local_now()
    ensure_daily_reminders(now)
    expire_ignored_initial_reminders(now)
    expire_secondary_reminders(now)
    dispatch_due_notifications(now)
    db.session.commit()


def mark_reminder_taken(reminder_id, user_id, now=None):
    now = now or local_now()
    reminder = db.session.get(ReminderPrompt, reminder_id)
    if reminder is None or reminder.user_id != user_id or reminder.status != "pending":
        return None

    reminder.status = "taken"
    reminder.responded_at = now
    _supersede_related_pending_reminders(reminder, now)
    _log_result(reminder, "taken", now)
    _create_notification(
        reminder,
        "status",
        f"{reminder.schedule.med_name} was recorded as taken.",
        now,
    )
    db.session.commit()
    return reminder


def snooze_reminder(reminder_id, user_id, minutes, now=None):
    now = now or local_now()
    if minutes not in (30, 60, 120):
        return None

    reminder = db.session.get(ReminderPrompt, reminder_id)
    if reminder is None or reminder.user_id != user_id:
        return None
    if reminder.status != "pending" or reminder.stage != "initial":
        return None

    reminder.status = "superseded"
    reminder.responded_at = now

    snoozed = ReminderPrompt(
        user_id=reminder.user_id,
        schedule_id=reminder.schedule_id,
        parent_id=reminder.id,
        dose_date=reminder.dose_date,
        stage="snoozed",
        status="pending",
        due_at=now + timedelta(minutes=minutes),
        original_due_at=reminder.original_due_at,
        expires_at=now + timedelta(minutes=minutes + 10),
        created_at=now,
    )
    db.session.add(snoozed)
    db.session.flush()
    _create_notification(
        snoozed,
        "status",
        f"{snoozed.schedule.med_name} was snoozed for {minutes} minutes.",
        now,
    )
    db.session.commit()
    return snoozed


def get_active_reminders(user_id, now=None):
    now = now or local_now()
    return db.session.scalars(
        sa.select(ReminderPrompt)
        .where(
            ReminderPrompt.user_id == user_id,
            ReminderPrompt.status == "pending",
            ReminderPrompt.due_at <= now,
        )
        .order_by(ReminderPrompt.due_at.asc())
    ).all()


def serialize_reminder(reminder):
    return {
        "id": reminder.id,
        "schedule_id": reminder.schedule_id,
        "med_name": reminder.schedule.med_name,
        "dosage": reminder.schedule.dosage,
        "due_at": format_datetime(reminder.due_at),
        "stage": reminder.stage,
        "status": reminder.status,
        "allow_remind_later": reminder.stage == "initial",
        "detail_url": f"/reminders/{reminder.id}",
        "taken_url": f"/reminders/{reminder.id}/taken",
        "remind_later_url": f"/reminders/{reminder.id}/remind-later",
    }


def get_recent_notifications(user_id, limit=8):
    return db.session.scalars(
        sa.select(ReminderNotification)
        .where(ReminderNotification.user_id == user_id)
        .order_by(ReminderNotification.created_at.desc())
        .limit(limit)
    ).all()


def get_status_notifications(user_id, limit=3):
    return db.session.scalars(
        sa.select(ReminderNotification)
        .where(
            ReminderNotification.user_id == user_id,
            ReminderNotification.channel == "status",
        )
        .order_by(ReminderNotification.created_at.desc())
        .limit(limit)
    ).all()


def get_user_logs(user_id):
    rows = db.session.scalars(
        sa.select(DoseLog).where(DoseLog.user_id == user_id).order_by(DoseLog.logged_at.desc())
    ).all()
    return [
        {
            "logged_at": format_datetime(row.logged_at),
            "dose_date": row.dose_date,
            "scheduled_for": format_datetime(row.scheduled_for),
            "med_name": row.schedule.med_name,
            "status": row.status,
        }
        for row in rows
    ]


def clear_user_logs(user_id):
    db.session.execute(sa.delete(DoseLog).where(DoseLog.user_id == user_id))
    db.session.commit()


def get_weekly_summary(user_id):
    first_schedule = db.session.scalar(
        sa.select(MedicationSchedule)
        .where(MedicationSchedule.user_id == user_id)
        .order_by(MedicationSchedule.created_at.asc())
    )

    if first_schedule is None:
        return {
            "total": 0,
            "taken": 0,
            "missed": 0,
            "escalations": 0,
            "adherence": 0.0,
            "period_start": None,
            "period_end": None,
        }

    now = local_now()
    anchor = first_schedule.created_at.replace(microsecond=0)
    elapsed_days = max((now.date() - anchor.date()).days, 0)
    cycle_index = elapsed_days // 7
    period_start = anchor + timedelta(days=cycle_index * 7)
    period_end = period_start + timedelta(days=7)

    logs = db.session.scalars(
        sa.select(DoseLog).where(
            DoseLog.user_id == user_id,
            DoseLog.logged_at >= period_start,
            DoseLog.logged_at < period_end,
        )
    ).all()

    total = len(logs)
    taken = sum(1 for log in logs if log.status == "taken")
    missed = sum(1 for log in logs if log.status == "missed")
    escalations = db.session.scalar(
        sa.select(sa.func.count(ReminderPrompt.id)).where(
            ReminderPrompt.user_id == user_id,
            ReminderPrompt.created_at >= period_start,
            ReminderPrompt.created_at < period_end,
            ReminderPrompt.stage.in_(("followup", "snoozed")),
        )
    ) or 0
    adherence = round((taken / total) * 100, 2) if total else 0.0

    return {
        "total": total,
        "taken": taken,
        "missed": missed,
        "escalations": escalations,
        "adherence": adherence,
        "period_start": format_datetime(period_start),
        "period_end": format_datetime(period_end),
    }
