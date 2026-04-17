from datetime import date, datetime, timedelta

from extensions import db
from models import DoseLog



def load_logs():
    rows = DoseLog.query.order_by(DoseLog.when.desc()).all()
    out = []
    for r in rows:
        out.append(
            {
                "when": r.when.strftime("%Y-%m-%d %H:%M:%S"),
                "day": r.day,
                "schedule_id": r.schedule_id,
                "med_name": r.schedule.med_name,
                "username": r.username,
                "status": r.status,
            }
        )
    return out


def already_logged_today(schedule_id: int, username: str) -> bool:
    today = date.today().isoformat()
    for log in DoseLog.query.filter_by(schedule_id=schedule_id, day=today).all():
        if log.username.lower() == username.lower():
            return True
    return False


def add_log(schedule_id: int, username: str, status: str) -> None:
    entry = DoseLog(
        when=datetime.utcnow(),
        day=date.today().isoformat(),
        schedule_id=schedule_id,
        username=username,
        status=status,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def clear_logs() -> None:
    DoseLog.query.delete()
    db.session.commit()


def get_weekly_summary(username: str = None):
    one_week_ago = datetime.now() - timedelta(days=7)

    query = DoseLog.query.filter(DoseLog.when >= one_week_ago)

    # Optional: filter per user
    if username:
        query = query.filter(DoseLog.username == username)

    logs = query.all()

    total = len(logs)
    taken = 0
    missed = 0
    escalations = 0

    for log in logs:
        if log.status == "taken":
            taken += 1
        elif log.status == "skipped":
            missed += 1
            escalations += 1
        elif log.status == "remind_later":
            escalations += 1

    adherence = (taken / total) * 100 if total > 0 else 0

    return {
        "total": total,
        "taken": taken,
        "missed": missed,
        "escalations": escalations,
        "adherence": round(adherence, 2)
    }

