from app import app
from core import format_datetime, get_user_logs, local_now, run_reminder_engine
from models import MedicationSchedule, User


def print_schedules(user):
    print("\nMedication schedules:")

    schedules = MedicationSchedule.query.filter_by(user_id=user.id).order_by(
        MedicationSchedule.scheduled_time.asc()
    ).all()

    if not schedules:
        print("No medications have been added yet.")
        return

    for schedule in schedules:
        time_text = schedule.scheduled_time.strftime("%H:%M")
        print(f"{schedule.id}) {schedule.med_name} - {schedule.dosage} at {time_text}")


def print_history(user):
    print("\nLatest history:")
    logs = get_user_logs(user.id)

    if not logs:
        print("No history yet.")
        return

    for log in logs[:10]:
        print(f"{log['logged_at']} | {log['med_name']} | {log['status']} | scheduled {log['scheduled_for']}")


def main():
    username = input("Enter your username: ").strip()

    if not username:
        print("Username is required.")
        return

    with app.app_context():
        user = User.query.filter_by(username=username).first()

        if user is None:
            print("User not found.")
            return

        run_reminder_engine()
        print(f"CareBridge console summary at {format_datetime(local_now())}")
        print_schedules(user)
        print_history(user)


if __name__ == "__main__":
    main()

