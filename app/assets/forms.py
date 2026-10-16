import re

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, ValidationError

from app.extensions import db
from app.models.asset import STATUS_IN_STORAGE, STATUS_LABELS, STATUS_IN_USE, Asset
from app.services.assets import RETURN_STATUSES
from app.utils import today_local

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,49}$")
DEFAULT_CATEGORIES = ["Bilgisayar", "Monitör", "Yazıcı", "Masa", "Sandalye", "Dolap", "Telefon", "Diğer"]


def _strip(value):
    return (value or "").strip()


def _upper(value):
    return (value or "").strip().upper()


def _blank_to_none(value):
    value = (value or "").strip()
    return value or None


def _upper_or_none(value):
    value = (value or "").strip().upper()
    return value or None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TrDateField(DateField):
    """Geçersiz tarih mesajını Türkçe verir."""

    def process_formdata(self, valuelist):
        if not valuelist:
            return
        try:
            super().process_formdata(valuelist)
        except ValueError as exc:
            raise ValueError("Geçerli bir tarih girin.") from exc


def asset_status_choices():
    """Elle seçilebilen durumlar. 'Kullanımda' yalnızca zimmetle oluşur."""
    return [(k, v) for k, v in STATUS_LABELS.items() if k != STATUS_IN_USE]


class AssetForm(FlaskForm):
    code = StringField("Demirbaş kodu", filters=[_upper],
                       validators=[DataRequired(message="Demirbaş kodu zorunludur."), Length(max=50, message="En fazla 50 karakter.")])
    name = StringField("Eşya adı", filters=[_strip],
                       validators=[DataRequired(message="Eşya adı zorunludur."), Length(max=120, message="En fazla 120 karakter.")])
    category = StringField("Kategori", filters=[_strip],
                           validators=[DataRequired(message="Kategori zorunludur."), Length(max=80, message="En fazla 80 karakter.")])
    brand = StringField("Marka", filters=[_blank_to_none], validators=[Optional(), Length(max=80, message="En fazla 80 karakter.")])
    model = StringField("Model", filters=[_blank_to_none], validators=[Optional(), Length(max=80, message="En fazla 80 karakter.")])
    serial_number = StringField("Seri numarası", filters=[_upper_or_none],
                                validators=[Optional(), Length(max=100, message="En fazla 100 karakter.")])
    location = StringField("Bulunduğu oda / konum", filters=[_blank_to_none],
                           validators=[Optional(), Length(max=120, message="En fazla 120 karakter.")])
    acquisition_date = TrDateField("Edinim tarihi", validators=[Optional()])
    status = SelectField("Durum", default=STATUS_IN_STORAGE, validate_choice=False)
    description = TextAreaField("Açıklama", filters=[_blank_to_none],
                                validators=[Optional(), Length(max=1000, message="En fazla 1000 karakter.")])
    submit = SubmitField("Kaydet")

    def __init__(self, *args, asset_id=None, lock_status=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.asset_id = asset_id
        if lock_status:
            del self.status  # zimmetli demirbaşın durumu elle değiştirilemez
        else:
            self.status.choices = asset_status_choices()

    def validate_code(self, field):
        if not CODE_RE.match(field.data or ""):
            raise ValidationError("Kod 2-50 karakter olmalı; harf, rakam, nokta, tire, alt çizgi ve eğik çizgi içerebilir.")
        query = db.select(Asset.id).where(Asset.code == field.data)
        if self.asset_id is not None:
            query = query.where(Asset.id != self.asset_id)
        if db.session.scalar(query) is not None:
            raise ValidationError("Bu demirbaş kodu zaten kullanılıyor.")

    def validate_serial_number(self, field):
        if not field.data:
            return
        query = db.select(Asset.id).where(Asset.serial_number == field.data)
        if self.asset_id is not None:
            query = query.where(Asset.id != self.asset_id)
        if db.session.scalar(query) is not None:
            raise ValidationError("Bu seri numarası başka bir demirbaşta kayıtlı.")

    def validate_acquisition_date(self, field):
        if field.data and field.data > today_local():
            raise ValidationError("Edinim tarihi gelecekte olamaz.")

    def validate_status(self, field):
        if field.data not in dict(asset_status_choices()):
            raise ValidationError("Geçerli bir durum seçin.")


class AssignForm(FlaskForm):
    assignee_id = SelectField("Personel", coerce=_int_or_none, validate_choice=False)
    start_date = TrDateField("Zimmet başlangıç tarihi", default=today_local,
                             validators=[InputRequired(message="Başlangıç tarihi zorunludur.")])
    note = TextAreaField("Teslim notu", filters=[_blank_to_none],
                         validators=[Optional(), Length(max=500, message="En fazla 500 karakter.")])
    submit = SubmitField("Zimmet ver")

    def validate_assignee_id(self, field):
        if field.data not in dict(self.assignee_id.choices):
            raise ValidationError("Listeden bir personel seçin.")


class ReturnForm(FlaskForm):
    return_date = TrDateField("İade tarihi", default=today_local,
                              validators=[InputRequired(message="İade tarihi zorunludur.")])
    status_after = SelectField("İade sonrası durum", default=STATUS_IN_STORAGE, validate_choice=False)
    note = TextAreaField("İade notu", filters=[_blank_to_none],
                         validators=[Optional(), Length(max=500, message="En fazla 500 karakter.")])
    submit = SubmitField("İadeyi kaydet")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_after.choices = [(k, STATUS_LABELS[k]) for k in RETURN_STATUSES]

    def validate_status_after(self, field):
        if field.data not in RETURN_STATUSES:
            raise ValidationError("Geçerli bir durum seçin.")
