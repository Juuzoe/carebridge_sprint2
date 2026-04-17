import os

from flask import Flask, flash, redirect, render_template, session, url_for, request

from extensions import db, mail
from forms import ConfirmDoseForm
from core import add_log, already_logged_today, clear_logs, load_logs, get_weekly_summary
from models import Schedule, seed_schedules
from mailer import send_email
from flask_login import login_user, current_user, login_required, logout_user
from urllib.parse import urlsplit
import sqlalchemy as sa
from app.forms import LoginForm, RegistrationForm
from app.models import User

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

#hey guys because im doing flask login, we may have to alter slight changes in code just to make sure the login stays consistent! thanks!

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar( #finds a row, turns it into a object then returns that object
            sa.select(User).where(User.username == form.username.data)) #searches the database for a user whose username matches the username entered in the login form.
        if user is None or not user.check_password(form.password.data): #if user doesnt exist or forgot password
            flash('Invalid username or password')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '': # keeps user safe by redirecting to home page if there is no next page
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


ensure_db()


@app.route("/")
@login_required
def home():
    return render_template("home.html", schedules=get_schedules_dict())


@app.route("/reminder/<int:schedule_id>", methods=["GET", "POST"])
def reminder(schedule_id: int):
    sched = get_schedules_dict().get(schedule_id)
    if not sched:
        flash("Schedule not found.")
        return redirect(url_for("home"))

    form = ConfirmDoseForm()

    if form.validate_on_submit():
        username = form.username.data.strip()
        session["username"] = username

        if form.taken.data:
            status = "taken"
        elif form.skipped.data:
            status = "skipped"
        else:
            status = "remind_later"

        if already_logged_today(schedule_id, username):
            flash("Already logged for today (for this user).")
            return redirect(url_for("history"))

        entry = add_log(schedule_id, username, status)

        if status in ("skipped", "remind_later"):
            try:
                send_email(
                    subject="CareBridge Alert: Missed or Late Medication",
                    sender=app.config["MAIL_DEFAULT_SENDER"],
                    recipients=[app.config["GP_EMAIL"]],
                    text_body=render_template(
                        "email/gp_alert.txt",
                        username=entry.username,
                        med_name=entry.schedule.med_name,
                        dosage=entry.schedule.dosage,
                        time_of_day=entry.schedule.time_of_day,
                        status=entry.status,
                        when_logged=entry.when.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    html_body=render_template(
                        "email/gp_alert.html",
                        username=entry.username,
                        med_name=entry.schedule.med_name,
                        dosage=entry.schedule.dosage,
                        time_of_day=entry.schedule.time_of_day,
                        status=entry.status,
                        when_logged=entry.when.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                flash("GP notification email generated.")
            except Exception as e:
                flash(f"Log saved, but GP email could not be generated: {e}")


        if status == "taken":
            flash("Recorded: Taken.")
        elif status == "skipped":
            flash("Recorded: Skipped.")
        else:
            flash("Recorded: Remind me later. (Prototype: no real timer)")

        return redirect(url_for("history"))

    if "username" in session:
        form.username.data = session["username"]

    return render_template("reminder.html", sched=sched, schedule_id=schedule_id, form=form)


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
        "Content-Disposition": "attachment; filename=report.txt" #turns text into downloadable txt file
    }


if __name__ == "__main__":
    app.run(debug=True)
