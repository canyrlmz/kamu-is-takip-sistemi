from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, SelectMultipleField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError

from app.extensions import db
from app.models.user import User
from app.permissions import (
    GRANTABLE,
    MAX_SENIORITY,
    MIN_SENIORITY,
    PERMISSIONS,
    ROLE_ARIZA_KONTROL,
    ROLE_LABELS,
    SENIORITY_LABELS,
)
from app.services.users import ASSIGNABLE_ROLES
from app.services.passwords import (
    validate_password_strength,
    validate_username as check_username_format,
)


def _strip_lower(value):
    return (value or "").strip().lower()


def _strip(value):
    return (value or "").strip()


def _role_choices():
    return [(role, ROLE_LABELS[role]) for role in ASSIGNABLE_ROLES]


def _seniority_choices():
    return [(str(level), SENIORITY_LABELS[level]) for level in range(MIN_SENIORITY, MAX_SENIORITY + 1)]


def _permission_choices():
    return [(key, PERMISSIONS[key][1]) for key in PERMISSIONS if key in GRANTABLE]


class _UserFieldsMixin:
    """Rol, kıdem ve izin alanları (oluşturma ve düzenleme formlarında ortaktır).

    Bu alanlar yalnızca BİÇİM doğrulaması yapar; rol tavanı, süper admin ve tek aktif süper admin gibi iş
    kuralları sunucuda `app.services.users` tarafından (formdan bağımsız olarak) uygulanır.
    """

    role = SelectField("Rol", choices=_role_choices(), default=ROLE_ARIZA_KONTROL, validate_choice=False)
    seniority_level = SelectField("Kıdem seviyesi", choices=_seniority_choices(), default=str(MIN_SENIORITY),
                                  validate_choice=False)
    permissions = SelectMultipleField("Modül izinleri", choices=_permission_choices(), validate_choice=False)

    def validate_role(self, field):
        if field.data not in ASSIGNABLE_ROLES:
            raise ValidationError("Geçerli bir rol seçin.")

    def validate_seniority_level(self, field):
        if field.data not in dict(_seniority_choices()):
            raise ValidationError(f"Kıdem seviyesi {MIN_SENIORITY}-{MAX_SENIORITY} arasında olmalıdır.")

    def validate_permissions(self, field):
        unknown = [p for p in (field.data or []) if p not in dict(_permission_choices())]
        if unknown:
            raise ValidationError("Geçersiz veya atanamayan bir izin seçildi.")


class UserCreateForm(_UserFieldsMixin, FlaskForm):
    """Yeni personel hesabı formu. Yeni hesap her zaman etkindir; süper admin rolü bu formdan verilemez."""

    username = StringField(
        "Kullanıcı adı",
        filters=[_strip_lower],
        validators=[DataRequired(message="Kullanıcı adı zorunludur."), Length(max=64, message="En fazla 64 karakter.")],
    )
    full_name = StringField(
        "Ad soyad",
        filters=[_strip],
        validators=[DataRequired(message="Ad soyad zorunludur."), Length(max=120, message="En fazla 120 karakter.")],
    )
    password = PasswordField(
        "Geçici parola",
        validators=[DataRequired(message="Parola zorunludur."), Length(max=128, message="En fazla 128 karakter.")],
    )
    password_confirm = PasswordField(
        "Parola (tekrar)",
        validators=[DataRequired(message="Parolayı tekrar girin."), EqualTo("password", message="Parolalar eşleşmiyor.")],
    )
    submit = SubmitField("Hesabı oluştur")

    def validate_username(self, field):
        errors = check_username_format(field.data)
        if errors:
            raise ValidationError(errors[0])
        if db.session.scalar(db.select(User.id).where(User.username == field.data)) is not None:
            raise ValidationError("Bu kullanıcı adı zaten kullanılıyor.")

    def validate_password(self, field):
        errors = validate_password_strength(field.data, self.username.data)
        if errors:
            raise ValidationError(" ".join(errors))


class UserEditForm(_UserFieldsMixin, FlaskForm):
    """Mevcut hesabın ad, rol, kıdem, izin ve aktiflik düzenlemesi. Kullanıcı adı ve parola burada değişmez."""

    full_name = StringField(
        "Ad soyad",
        filters=[_strip],
        validators=[DataRequired(message="Ad soyad zorunludur."), Length(max=120, message="En fazla 120 karakter.")],
    )
    active = BooleanField("Hesap etkin")
    submit = SubmitField("Kaydet")
