from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

METER_TYPES = {
    'water': {'label': 'Wasser', 'unit': 'm³', 'icon': 'droplet', 'color': '#0d6efd'},
    'gas':   {'label': 'Gas',    'unit': 'm³', 'icon': 'fire',    'color': '#fd7e14'},
    'power': {'label': 'Strom',  'unit': 'kWh','icon': 'lightning-charge', 'color': '#ffc107'},
    'heat':  {'label': 'Wärme',  'unit': 'GJ', 'icon': 'thermometer-half', 'color': '#dc3545'},
}

FIELD_TYPES = {
    'number':  {'label': 'Zahl',       'icon': 'hash'},
    'text':    {'label': 'Text',        'icon': 'fonts'},
    'boolean': {'label': 'Ja / Nein',  'icon': 'toggle-on'},
    'select':  {'label': 'Auswahl',    'icon': 'list'},
}


class Meter(db.Model):
    __tablename__ = 'meters'

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), nullable=False)
    meter_type   = db.Column(db.String(20),  nullable=False)
    meter_number = db.Column(db.String(50),  nullable=True)
    location     = db.Column(db.String(100), nullable=True)
    notes        = db.Column(db.Text,        nullable=True)
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)
    active       = db.Column(db.Boolean,     default=True)
    import_mask      = db.Column(db.Text, nullable=True)
    import_field_map = db.Column(db.Text, nullable=True)  # JSON {"E":"value","A":1,"B":2}

    readings = db.relationship('Reading', backref='meter', lazy=True,
                               cascade='all, delete-orphan',
                               order_by='Reading.read_at')
    fields   = db.relationship('MeterField', backref='meter', lazy=True,
                               cascade='all, delete-orphan',
                               order_by='MeterField.sort_order')

    @property
    def type_info(self):
        return METER_TYPES.get(self.meter_type, {
            'label': self.meter_type, 'unit': '', 'icon': 'speedometer2', 'color': '#6c757d'
        })

    @property
    def latest_reading(self):
        return db.session.query(Reading).filter_by(meter_id=self.id)\
                         .order_by(Reading.read_at.desc()).first()

    @property
    def total_consumption(self):
        readings = sorted(self.readings, key=lambda r: r.read_at)
        if len(readings) < 2:
            return None
        return round(readings[-1].value - readings[0].value, 3)


class MeterField(db.Model):
    """A custom field definition for a meter (e.g. temperature, pressure)."""
    __tablename__ = 'meter_fields'

    id         = db.Column(db.Integer, primary_key=True)
    meter_id   = db.Column(db.Integer, db.ForeignKey('meters.id'), nullable=False)
    label      = db.Column(db.String(100), nullable=False)
    field_type = db.Column(db.String(20),  nullable=False, default='number')
    unit       = db.Column(db.String(30),  nullable=True)
    options    = db.Column(db.Text,        nullable=True)  # JSON list for 'select'
    required   = db.Column(db.Boolean,     default=False)
    sort_order = db.Column(db.Integer,     default=0)

    values = db.relationship('ReadingValue', backref='field',
                             cascade='all, delete-orphan')

    @property
    def options_list(self):
        if self.options:
            try:
                return json.loads(self.options)
            except (ValueError, TypeError):
                return []
        return []

    def to_dict(self):
        return {
            'id':         self.id,
            'label':      self.label,
            'field_type': self.field_type,
            'unit':       self.unit or '',
            'options':    self.options_list,
            'required':   self.required,
        }


class Reading(db.Model):
    __tablename__ = 'readings'

    id         = db.Column(db.Integer, primary_key=True)
    meter_id   = db.Column(db.Integer, db.ForeignKey('meters.id'), nullable=False)
    value      = db.Column(db.Float,   nullable=False)
    read_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    image_path = db.Column(db.String(255), nullable=True)
    notes      = db.Column(db.Text,    nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    extra_values = db.relationship('ReadingValue', backref='reading',
                                   cascade='all, delete-orphan', lazy=True)

    @property
    def consumption_since_previous(self):
        prev = db.session.query(Reading)\
                         .filter(Reading.meter_id == self.meter_id,
                                 Reading.read_at < self.read_at)\
                         .order_by(Reading.read_at.desc()).first()
        if prev is None:
            return None
        days = (self.read_at - prev.read_at).days or 1
        diff = round(self.value - prev.value, 3)
        return {'amount': diff, 'days': days, 'per_day': round(diff / days, 4)}


class ReadingValue(db.Model):
    """Stores one custom-field value for a reading."""
    __tablename__ = 'reading_values'

    id         = db.Column(db.Integer, primary_key=True)
    reading_id = db.Column(db.Integer, db.ForeignKey('readings.id'), nullable=False)
    field_id   = db.Column(db.Integer, db.ForeignKey('meter_fields.id'), nullable=False)
    value      = db.Column(db.Text,    nullable=True)
