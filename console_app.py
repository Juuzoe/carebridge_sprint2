from app import app
from core import add_log, already_logged_today, clear_logs, load_logs
from models import Schedule


def print_schedules():
    print("\nMedication schedules:")
    for s in Schedule.query.order_by(Schedule.id).all():
        print(f"  {s.id}) {s.med_name} — {s.dosage} ({s.time_of_day})")


def print_logs(logs):
    if not logs:
        print("\nNo actions yet.")
        return
    print("\nHistory (newest first):")
    for item in logs[:30]:
        print(f"- {item['when']} | {item['username']} | {item['med_name']} | {item['status']}")


def pick_schedule_id():
    try:
        return int(input("Enter schedule id: ").strip())
    except ValueError:
        return None


def main():
    username = input("Enter your name: ").strip()
    if not username:
        print("Name is required.")
        return

    with app.app_context():
        while True:
            print("\nCareBridge (Console)")
            print("1) View schedules")
            print("2) Log TAKEN")
            print("3) Log SKIPPED")
            print("4) Remind me later")
            print("5) View history")
            print("6) Clear history")
            print("0) Exit")

            choice = input("Choose: ").strip()
            logs = load_logs()

            if choice == "1":
                print_schedules()

            elif choice in ("2", "3", "4"):
                print_schedules()
                sid = pick_schedule_id()
                if sid is None or Schedule.query.get(sid) is None:
                    print("Invalid schedule id.")
                    continue

                if already_logged_today(sid, username):
                    print("Already logged for today (for this user).")
                    continue

                if choice == "2":
                    status = "taken"
                elif choice == "3":
                    status = "skipped"
                else:
                    status = "remind_later"

                add_log(sid, username, status)
                print(f"Recorded: {status}")

            elif choice == "5":
                print_logs(logs)

            elif choice == "6":
                clear_logs()
                print("History cleared.")

            elif choice == "0":
                break

            else:
                print("Unknown option.")


if __name__ == "__main__":
    main()


