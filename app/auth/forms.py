from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField(
        "Kullanıcı adı",
        validators=[DataRequired(message="Kullanıcı adı zorunludur."), Length(max=64)],
    )
    password = PasswordField(
        "Parola",
        validators=[DataRequired(message="Parola zorunludur."), Length(max=128)],
    )
    submit = SubmitField("Giriş yap")
