from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TimeField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from models import User


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember Me")
    submit = SubmitField("Sign In")


class RegistrationForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=63)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=119)])
    password = PasswordField("Password", validators=[DataRequired()])
    password2 = PasswordField("Repeat Password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Register")

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data.strip()).first()
        if user is not None:
            raise ValidationError("Please use a different username.")

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data.strip()).first()
        if user is not None:
            raise ValidationError("Please use a different email address.")


class ConfirmDoseForm(FlaskForm):
    taken = SubmitField("Taken")
    skipped = SubmitField("Skipped")
    remind_later = SubmitField("Remind me later")


class MedicationForm(FlaskForm):
    med_name = StringField("Medication", validators=[DataRequired(), Length(max=80)])
    dosage = StringField("Dosage", validators=[DataRequired(), Length(max=80)])
    scheduled_time = TimeField("Scheduled time", format="%H:%M", validators=[DataRequired()])
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save medication")


class AccountSettingsForm(FlaskForm):
    email = StringField("Reminder email", validators=[DataRequired(), Email(), Length(max=119)])
    carer_email = StringField("Carer email", validators=[Optional(), Email(), Length(max=119)])
    gp_email = StringField("GP email", validators=[Optional(), Email(), Length(max=119)])
    submit = SubmitField("Update contact details")

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data.strip()).first()
        if user is not None and user.id != current_user.id:
            raise ValidationError("Please use a different email address.")

