from app import app
from core import format_datetime, get_user_logs, local_now, run_reminder_engine
from extensions import db
from models import MedicationSchedule, User


def print_schedules(user):
    print("\nMedication schedules:")
    schedules = (
        db.session.query(MedicationSchedule)
        .filter_by(user_id=user.id)
        .order_by(MedicationSchedule.scheduled_time.asc())
        .all()
    )
    if not schedules:
        print("  No medications configured.")
        return
    for schedule in schedules:
        print(f"  {schedule.id}) {schedule.med_name} - {schedule.dosage} at {schedule.scheduled_time.strftime('%H:%M')}")


def main():
    username = input("Enter your username: ").strip()
    if not username:
        print("Username is required.")
        return

    with app.app_context():
        user = db.session.query(User).filter_by(username=username).first()
        if user is None:
            print("User not found.")
            return

        run_reminder_engine()
        print(f"CareBridge console summary at {format_datetime(local_now())}")
        print_schedules(user)
        print("\nLatest history:")
        for log in get_user_logs(user.id)[:10]:
            print(f"- {log['logged_at']} | {log['med_name']} | {log['status']} | scheduled {log['scheduled_for']}")


if __name__ == "__main__":
    main()
