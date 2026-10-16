from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models.ticket import CATEGORY_LABELS, PRIORITY_LABELS, PRIORITY_NORMAL, STATUS_LABELS


def _strip(value):
    return (value or "").strip()


def _blank_to_none(value):
    value = (value or "").strip()
    return value or None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TicketForm(FlaskForm):
    title = StringField("Başlık", filters=[_strip],
                        validators=[DataRequired(message="Başlık zorunludur."), Length(max=150, message="En fazla 150 karakter.")])
    description = TextAreaField("Açıklama", filters=[_strip],
                                validators=[DataRequired(message="Açıklama zorunludur."), Length(max=2000, message="En fazla 2000 karakter.")])
    category = SelectField("Kategori", choices=list(CATEGORY_LABELS.items()), validate_choice=False)
    location = StringField("Yer / konum", filters=[_strip],
                           validators=[DataRequired(message="Yer / konum zorunludur."), Length(max=120, message="En fazla 120 karakter.")])
    priority = SelectField("Öncelik", choices=list(PRIORITY_LABELS.items()), default=PRIORITY_NORMAL, validate_choice=False)
    submit = SubmitField("Talebi gönder")

    def validate_category(self, field):
        if field.data not in CATEGORY_LABELS:
            raise ValidationError("Geçerli bir kategori seçin.")

    def validate_priority(self, field):
        if field.data not in PRIORITY_LABELS:
            raise ValidationError("Geçerli bir öncelik seçin.")


class StatusForm(FlaskForm):
    """Hedef durum seçenekleri route'ta, kullanıcının o talepte yapabileceği geçişlerle sınırlanır."""

    status = SelectField("Yeni durum", validate_choice=False)
    note = TextAreaField("İşlem notu", filters=[_blank_to_none],
                         validators=[Optional(), Length(max=500, message="En fazla 500 karakter.")])
    submit = SubmitField("Durumu kaydet")

    def validate_status(self, field):
        if field.data not in dict(self.status.choices):
            raise ValidationError("Geçerli bir durum seçin.")


class AssignForm(FlaskForm):
    assignee_id = SelectField("Sorumlu", coerce=_int_or_none, validate_choice=False)
    note = TextAreaField("İşlem notu", filters=[_blank_to_none],
                         validators=[Optional(), Length(max=500, message="En fazla 500 karakter.")])
    submit = SubmitField("Ata")

    def validate_assignee_id(self, field):
        if field.data not in dict(self.assignee_id.choices):
            raise ValidationError("Listeden etkin bir kullanıcı seçin.")
