from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, ValidationError

from app.assets.forms import TrDateField, _blank_to_none, _strip
from app.models.vehicle import MAINTENANCE_TYPE_LABELS, METER_LABELS, STATUS_LABELS
from app.services.vehicles import MAX_METER, normalize_identifier, parse_money_kurus

DEFAULT_VEHICLE_TYPES = ["Binek araç", "Kamyonet", "Kamyon", "Minibüs", "Otobüs", "Ekskavatör", "Yükleyici",
                         "Greyder", "Silindir", "Dozer", "Traktör", "Diğer"]


class TrIntegerField(IntegerField):
    """Geçersiz tam sayı mesajını Türkçe verir."""

    def process_formdata(self, valuelist):
        if not valuelist or not str(valuelist[0]).strip():
            self.data = None
            return
        try:
            self.data = int(str(valuelist[0]).strip())
        except ValueError:
            self.data = None
            raise ValueError("Tam sayı girin.") from None


_METER_RANGE = NumberRange(min=0, max=MAX_METER, message=f"0 ile {MAX_METER:,} arasında olmalı.".replace(",", "."))


class VehicleForm(FlaskForm):
    vehicle_type = StringField("Araç / makine türü", filters=[_strip],
                               validators=[DataRequired(message="Tür zorunludur."), Length(max=60, message="En fazla 60 karakter.")])
    identifier = StringField("Plaka veya envanter kodu", filters=[normalize_identifier],
                             validators=[DataRequired(message="Plaka veya envanter kodu zorunludur."),
                                         Length(max=30, message="En fazla 30 karakter.")])
    brand = StringField("Marka", filters=[_blank_to_none], validators=[Optional(), Length(max=60, message="En fazla 60 karakter.")])
    model = StringField("Model", filters=[_blank_to_none], validators=[Optional(), Length(max=60, message="En fazla 60 karakter.")])
    model_year = TrIntegerField("Model yılı", validators=[Optional()])
    meter_type = SelectField("Sayaç türü", choices=list(METER_LABELS.items()), validate_choice=False)
    current_meter = TrIntegerField("Güncel sayaç", default=0,
                                   validators=[InputRequired(message="Güncel sayaç zorunludur."), _METER_RANGE])
    status = SelectField("Durum", choices=list(STATUS_LABELS.items()), default="active", validate_choice=False)
    description = TextAreaField("Açıklama", filters=[_blank_to_none],
                                validators=[Optional(), Length(max=1000, message="En fazla 1000 karakter.")])
    submit = SubmitField("Kaydet")

    def __init__(self, *args, lock_meter_type=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock_meter_type = lock_meter_type

    def validate_meter_type(self, field):
        if field.data not in METER_LABELS:
            raise ValidationError("Geçerli bir sayaç türü seçin.")

    def validate_status(self, field):
        if field.data not in STATUS_LABELS:
            raise ValidationError("Geçerli bir durum seçin.")

    def vehicle_data(self) -> dict:
        return {name: self[name].data for name in (
            "vehicle_type", "identifier", "brand", "model", "model_year", "meter_type", "current_meter",
            "status", "description")}


class MaintenanceForm(FlaskForm):
    maintenance_type = SelectField("Bakım türü", choices=list(MAINTENANCE_TYPE_LABELS.items()), validate_choice=False)
    maintenance_date = TrDateField("Bakım tarihi", validators=[InputRequired(message="Bakım tarihi zorunludur.")])
    meter_reading = TrIntegerField("Bakım sırasındaki sayaç", validators=[InputRequired(message="Sayaç değeri zorunludur."), _METER_RANGE])
    description = TextAreaField("Yapılan işlemler", filters=[_strip],
                                validators=[DataRequired(message="Yapılan işlem açıklaması zorunludur."),
                                            Length(max=1000, message="En fazla 1000 karakter.")])
    provider = StringField("Servis / bakım sağlayıcı", filters=[_blank_to_none],
                           validators=[Optional(), Length(max=120, message="En fazla 120 karakter.")])
    cost = StringField("Maliyet (₺)", filters=[_strip], validators=[Optional(), Length(max=20, message="Çok uzun.")])
    next_date = TrDateField("Sonraki bakım tarihi", validators=[Optional()])
    next_meter = TrIntegerField("Sonraki bakım sayacı", validators=[Optional(), _METER_RANGE])
    notes = TextAreaField("Ek notlar", filters=[_blank_to_none],
                          validators=[Optional(), Length(max=1000, message="En fazla 1000 karakter.")])
    submit = SubmitField("Bakımı kaydet")

    def validate_maintenance_type(self, field):
        if field.data not in MAINTENANCE_TYPE_LABELS:
            raise ValidationError("Geçerli bir bakım türü seçin.")

    def validate_cost(self, field):
        try:
            parse_money_kurus(field.data)
        except ValueError as error:
            raise ValidationError(str(error)) from None

    def maintenance_data(self) -> dict:
        return {
            "maintenance_type": self.maintenance_type.data, "maintenance_date": self.maintenance_date.data,
            "meter_reading": self.meter_reading.data, "description": self.description.data,
            "provider": self.provider.data, "cost_kurus": parse_money_kurus(self.cost.data),
            "next_date": self.next_date.data, "next_meter": self.next_meter.data, "notes": self.notes.data,
        }


class VoidForm(FlaskForm):
    reason = TextAreaField("İptal gerekçesi", filters=[_strip],
                           validators=[DataRequired(message="İptal gerekçesi zorunludur."),
                                       Length(min=5, max=500, message="Gerekçe 5-500 karakter olmalı.")])
    submit = SubmitField("Bakım kaydını iptal et")
