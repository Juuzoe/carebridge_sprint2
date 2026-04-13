from datetime import date, datetime

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
