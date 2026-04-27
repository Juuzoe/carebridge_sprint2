from flask import current_app, render_template
from flask_mail import Message

from extensions import mail


def send_email(subject, sender, recipients, text_body, html_body):
    message = Message(subject, sender=sender, recipients=recipients)
    message.body = text_body
    message.html = html_body
    mail.send(message)


def print_email_fallback(message):
    print(f"[CareBridge] {message}", flush=True)


def send_reminder_email(reminder, recipient, message):
    recipient = (recipient or "").strip()

    if not recipient:
        print_email_fallback(message)
        return {
            "delivered": False,
            "channel": "email_fallback",
            "message": "No reminder email is saved, so the reminder was printed in the terminal.",
        }

    subject = f"CareBridge reminder: {reminder.schedule.med_name}"
    sender = current_app.config["MAIL_DEFAULT_SENDER"]
    text_body = render_template("email/reminder.txt", reminder=reminder, message=message)
    html_body = render_template("email/reminder.html", reminder=reminder, message=message)

    try:
        send_email(subject, sender, [recipient], text_body, html_body)
        return {
            "delivered": True,
            "channel": "email",
            "message": f"Email reminder sent to {recipient}.",
        }
    except Exception:
        print_email_fallback(f"Reminder for {recipient}: {message}")
        return {
            "delivered": False,
            "channel": "email_fallback",
            "message": "Email could not be sent, so the reminder was printed in the terminal.",
        }


def send_carer_alert(reminder, dose_log, recipient):
    recipient = (recipient or "").strip()

    if not recipient:
        return {
            "delivered": False,
            "channel": "carer_alert",
            "message": "No carer email is saved, so no carer alert was sent.",
        }

    subject = "CareBridge missed medication alert"
    sender = current_app.config["MAIL_DEFAULT_SENDER"]
    text_body = render_template("email/carer_alert.txt", reminder=reminder, dose_log=dose_log)
    html_body = render_template("email/carer_alert.html", reminder=reminder, dose_log=dose_log)

    try:
        send_email(subject, sender, [recipient], text_body, html_body)
        return {
            "delivered": True,
            "channel": "carer_email",
            "message": f"Carer alert sent to {recipient}.",
        }
    except Exception:
        print_email_fallback(f"Carer alert for {recipient}: {dose_log.username}")
        return {
            "delivered": False,
            "channel": "carer_email_fallback",
            "message": "Carer alert could not be sent, so it was printed in the terminal.",
        }


def send_gp_summary(user, summary, recipient):
    recipient = (recipient or "").strip()

    if not recipient:
        return {
            "delivered": False,
            "channel": "gp_summary",
            "message": "No GP email address is saved.",
        }

    subject = "CareBridge weekly medication summary"
    sender = current_app.config["MAIL_DEFAULT_SENDER"]
    text_body = render_template("email/gp_summary.txt", user=user, summary=summary)
    html_body = render_template("email/gp_summary.html", user=user, summary=summary)

    try:
        send_email(subject, sender, [recipient], text_body, html_body)
        return {
            "delivered": True,
            "channel": "gp_email",
            "message": f"Weekly summary sent to GP at {recipient}.",
        }
    except Exception:
        print_email_fallback(f"GP summary for {recipient}: {summary}")
        return {
            "delivered": False,
            "channel": "email_fallback",
            "message": "GP summary could not be sent, so it was printed in the terminal.",
        }

