from datetime import datetime, time

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(63), unique=True, index=True, nullable=False)
    email = db.Column(db.String(119), unique=True, index=True, nullable=False)
    carer_email = db.Column(db.String(119), nullable=True)
    gp_email = db.Column(db.String(119), nullable=True)
    carer_code = db.Column(db.String(32), unique=True, index=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)

    schedules = db.relationship("Schedule", back_populates="user", cascade="all, delete-orphan")
    reminders = db.relationship("ReminderPrompt", back_populates="user", cascade="all, delete-orphan")
    notifications = db.relationship("ReminderNotification", back_populates="user", cascade="all, delete-orphan")
    dose_logs = db.relationship("DoseLog", back_populates="user", cascade="all, delete-orphan")
    exercise_logs = db.relationship("ExerciseLog", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Schedule(db.Model):
    __tablename__ = "schedule"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    med_name = db.Column(db.String(80), nullable=False)
    dosage = db.Column(db.String(80), nullable=False)
    scheduled_time = db.Column(db.Time, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", back_populates="schedules")
    reminders = db.relationship("ReminderPrompt", back_populates="schedule", cascade="all, delete-orphan")
    dose_logs = db.relationship("DoseLog", back_populates="schedule", cascade="all, delete-orphan")

    @property
    def time_of_day(self):
        return self.scheduled_time.strftime("%H:%M")


class ReminderPrompt(db.Model):
    __tablename__ = "reminder_prompt"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("schedule.id"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("reminder_prompt.id"), nullable=True)
    day = db.Column(db.String(10), index=True, nullable=False)
    stage = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, index=True)
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    original_due_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    responded_at = db.Column(db.DateTime, nullable=True)
    email_sent_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="reminders")
    schedule = db.relationship("Schedule", back_populates="reminders")
    parent = db.relationship("ReminderPrompt", remote_side=[id])
    notifications = db.relationship("ReminderNotification", back_populates="reminder", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("schedule_id", "day", "stage", name="uq_schedule_day_stage"),
    )


class ReminderNotification(db.Model):
    __tablename__ = "reminder_notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    reminder_id = db.Column(db.Integer, db.ForeignKey("reminder_prompt.id"), nullable=False, index=True)
    channel = db.Column(db.String(30), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    user = db.relationship("User", back_populates="notifications")
    reminder = db.relationship("ReminderPrompt", back_populates="notifications")


class DoseLog(db.Model):
    __tablename__ = "dose_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("schedule.id"), nullable=False, index=True)
    reminder_id = db.Column(db.Integer, db.ForeignKey("reminder_prompt.id"), nullable=True)
    when = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    day = db.Column(db.String(10), nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), nullable=False)

    user = db.relationship("User", back_populates="dose_logs")
    schedule = db.relationship("Schedule", back_populates="dose_logs")

    __table_args__ = (
        db.UniqueConstraint("user_id", "schedule_id", "day", name="uq_user_schedule_day"),
    )


class ExerciseLog(db.Model):
    __tablename__ = "exercise_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    exercise_date = db.Column(db.String(10), nullable=False, index=True)
    completed_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", back_populates="exercise_logs")

    __table_args__ = (
        db.UniqueConstraint("user_id", "exercise_date", name="uq_user_exercise_date"),
    )


DEFAULT_SCHEDULES = [
    {"med_name": "Aspirin", "dosage": "1 tablet", "scheduled_time": time(8, 0)},
    {"med_name": "Vitamin D", "dosage": "1 capsule", "scheduled_time": time(20, 0)},
]


def seed_schedules(user_id=None):
    if user_id is None:
        return

    existing = Schedule.query.filter_by(user_id=user_id).first()
    if existing:
        return

    for row in DEFAULT_SCHEDULES:
        db.session.add(
            Schedule(
                user_id=user_id,
                med_name=row["med_name"],
                dosage=row["dosage"],
                scheduled_time=row["scheduled_time"],
                active=True,
            )
        )



