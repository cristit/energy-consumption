import os
import base64
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_from_directory)
from werkzeug.utils import secure_filename
from sqlalchemy import func
from models import db, Meter, Reading, MeterField, ReadingValue, METER_TYPES, FIELD_TYPES
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

BERLIN = ZoneInfo('Europe/Berlin')

_version_file = os.path.join(os.path.dirname(__file__), 'VERSION')
APP_VERSION = open(_version_file).read().strip() if os.path.exists(_version_file) else '0.0.0'

def now_berlin():
    return datetime.now(BERLIN)

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

@app.template_filter('berlin')
def berlin_filter(dt, fmt='%d.%m.%Y %H:%M'):
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BERLIN).strftime(fmt)


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


@app.context_processor
def inject_globals():
    return {
        'meter_types': METER_TYPES,
        'field_types': FIELD_TYPES,
        'now':         now_berlin(),
        'app_version': APP_VERSION,
    }


# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    meters = Meter.query.filter_by(active=True).all()
    stats = []
    for m in meters:
        readings = sorted(m.readings, key=lambda r: r.read_at)
        monthly = None
        if len(readings) >= 2:
            last = readings[-1]
            prev = readings[-2]
            days = max((last.read_at - prev.read_at).days, 1)
            diff = last.value - prev.value
            monthly = round(diff / days * 30, 3)
        stats.append({'meter': m, 'monthly_est': monthly})
    return render_template('dashboard.html', stats=stats)


# ── Meters ─────────────────────────────────────────────────────────────────────
@app.route('/meters')
def meters():
    all_meters = Meter.query.order_by(Meter.created_at.desc()).all()
    return render_template('meters.html', meters=all_meters)


@app.route('/meters/add', methods=['GET', 'POST'])
def add_meter():
    if request.method == 'POST':
        meter = Meter(
            name        = request.form['name'].strip(),
            meter_type  = request.form['meter_type'],
            meter_number= request.form.get('meter_number', '').strip(),
            location    = request.form.get('location', '').strip(),
            notes       = request.form.get('notes', '').strip(),
        )
        db.session.add(meter)
        db.session.commit()
        flash(f'Zähler „{meter.name}" angelegt. Weitere Felder können jetzt eingerichtet werden.', 'success')
        return redirect(url_for('edit_meter', meter_id=meter.id))
    return render_template('meter_form.html', meter=None)


@app.route('/meters/<int:meter_id>/edit', methods=['GET', 'POST'])
def edit_meter(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    if request.method == 'POST':
        meter.name         = request.form['name'].strip()
        meter.meter_type   = request.form['meter_type']
        meter.meter_number = request.form.get('meter_number', '').strip()
        meter.location     = request.form.get('location', '').strip()
        meter.notes        = request.form.get('notes', '').strip()
        meter.active       = 'active' in request.form
        db.session.commit()
        flash(f'Zähler „{meter.name}" wurde aktualisiert.', 'success')
        return redirect(url_for('edit_meter', meter_id=meter.id))
    return render_template('meter_form.html', meter=meter)


@app.route('/meters/<int:meter_id>/delete', methods=['POST'])
def delete_meter(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    name = meter.name
    db.session.delete(meter)
    db.session.commit()
    flash(f'Zähler „{name}" wurde gelöscht.', 'warning')
    return redirect(url_for('meters'))


# ── Meter Fields ───────────────────────────────────────────────────────────────
@app.route('/meters/<int:meter_id>/fields/add', methods=['POST'])
def add_meter_field(meter_id):
    Meter.query.get_or_404(meter_id)
    import json as _json
    options_raw = request.form.get('options', '').strip()
    options_json = None
    if options_raw:
        options_json = _json.dumps([o.strip() for o in options_raw.split(',') if o.strip()])
    max_order = db.session.query(db.func.max(MeterField.sort_order))\
                          .filter_by(meter_id=meter_id).scalar() or 0
    field = MeterField(
        meter_id   = meter_id,
        label      = request.form['label'].strip(),
        field_type = request.form.get('field_type', 'number'),
        unit       = request.form.get('unit', '').strip() or None,
        options    = options_json,
        required   = 'required' in request.form,
        sort_order = max_order + 1,
    )
    db.session.add(field)
    db.session.commit()
    flash(f'Feld „{field.label}" wurde hinzugefügt.', 'success')
    return redirect(url_for('edit_meter', meter_id=meter_id) + '#fields')


@app.route('/meters/<int:meter_id>/fields/<int:field_id>/edit', methods=['POST'])
def edit_meter_field(meter_id, field_id):
    field = MeterField.query.filter_by(id=field_id, meter_id=meter_id).first_or_404()
    import json as _json
    options_raw = request.form.get('options', '').strip()
    field.label      = request.form['label'].strip()
    field.field_type = request.form.get('field_type', 'number')
    field.unit       = request.form.get('unit', '').strip() or None
    field.options    = _json.dumps([o.strip() for o in options_raw.split(',') if o.strip()]) if options_raw else None
    field.required   = 'required' in request.form
    db.session.commit()
    flash(f'Feld „{field.label}" wurde aktualisiert.', 'success')
    return redirect(url_for('edit_meter', meter_id=meter_id) + '#fields')


@app.route('/meters/<int:meter_id>/fields/<int:field_id>/delete', methods=['POST'])
def delete_meter_field(meter_id, field_id):
    field = MeterField.query.filter_by(id=field_id, meter_id=meter_id).first_or_404()
    label = field.label
    db.session.delete(field)
    db.session.commit()
    flash(f'Feld „{label}" wurde gelöscht.', 'warning')
    return redirect(url_for('edit_meter', meter_id=meter_id) + '#fields')


@app.route('/api/meters/<int:meter_id>/fields')
def api_meter_fields(meter_id):
    fields = MeterField.query.filter_by(meter_id=meter_id)\
                             .order_by(MeterField.sort_order).all()
    return jsonify([f.to_dict() for f in fields])


# ── Readings ───────────────────────────────────────────────────────────────────
@app.route('/readings')
def readings():
    meter_id = request.args.get('meter_id', type=int)
    query = Reading.query.join(Meter)
    if meter_id:
        query = query.filter(Reading.meter_id == meter_id)
    all_readings = query.order_by(Reading.read_at.desc()).limit(200).all()
    meters = Meter.query.filter_by(active=True).all()
    return render_template('readings.html', readings=all_readings,
                           meters=meters, selected_meter=meter_id)


@app.route('/readings/add', methods=['GET', 'POST'])
def add_reading():
    meters = Meter.query.filter_by(active=True).all()
    preselect = request.args.get('meter_id', type=int)

    if request.method == 'POST':
        meter_id = int(request.form['meter_id'])
        value    = float(request.form['value'].replace(',', '.'))
        read_at_naive = datetime.strptime(request.form['read_at'], '%Y-%m-%dT%H:%M')
        read_at = read_at_naive.replace(tzinfo=BERLIN).astimezone(timezone.utc).replace(tzinfo=None)
        notes    = request.form.get('notes', '').strip()

        image_path = None
        file = request.files.get('image')
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(
                f"{meter_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            )
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_path = filename

        reading = Reading(meter_id=meter_id, value=value,
                          read_at=read_at, notes=notes, image_path=image_path)
        db.session.add(reading)
        db.session.flush()  # get reading.id before commit

        for field in MeterField.query.filter_by(meter_id=meter_id)\
                                     .order_by(MeterField.sort_order).all():
            key = f'field_{field.id}'
            if field.field_type == 'boolean':
                val = '1' if key in request.form else '0'
            else:
                val = request.form.get(key, '').strip()
            if val != '':
                db.session.add(ReadingValue(reading_id=reading.id,
                                            field_id=field.id, value=val))

        db.session.commit()
        flash('Ablesung wurde gespeichert.', 'success')
        return redirect(url_for('readings', meter_id=meter_id))

    return render_template('add_reading.html', meters=meters, preselect=preselect,
                           now=now_berlin().strftime('%Y-%m-%dT%H:%M'))


@app.route('/readings/<int:reading_id>/delete', methods=['POST'])
def delete_reading(reading_id):
    reading = Reading.query.get_or_404(reading_id)
    meter_id = reading.meter_id
    if reading.image_path:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], reading.image_path))
        except OSError:
            pass
    db.session.delete(reading)
    db.session.commit()
    flash('Ablesung wurde gelöscht.', 'warning')
    return redirect(url_for('readings', meter_id=meter_id))


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ── Analysis ───────────────────────────────────────────────────────────────────
@app.route('/analysis')
def analysis():
    meters = Meter.query.filter_by(active=True).all()
    return render_template('analysis.html', meters=meters)


@app.route('/api/chart-data/<int:meter_id>')
def chart_data(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    months_back = request.args.get('months', 12, type=int)
    since = datetime.utcnow() - timedelta(days=months_back * 31)

    readings = Reading.query.filter(
        Reading.meter_id == meter_id,
        Reading.read_at >= since
    ).order_by(Reading.read_at).all()

    labels, values, consumptions = [], [], []
    for i, r in enumerate(readings):
        labels.append(r.read_at.strftime('%d.%m.%Y'))
        values.append(r.value)
        if i > 0:
            consumptions.append(round(r.value - readings[i-1].value, 3))
        else:
            consumptions.append(0)

    return jsonify({
        'meter': meter.name,
        'unit': meter.type_info['unit'],
        'color': meter.type_info['color'],
        'labels': labels,
        'values': values,
        'consumptions': consumptions,
    })


@app.route('/api/monthly-summary/<int:meter_id>')
def monthly_summary(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    readings = Reading.query.filter_by(meter_id=meter_id)\
                            .order_by(Reading.read_at).all()

    monthly = {}
    for i in range(1, len(readings)):
        r     = readings[i]
        prev  = readings[i-1]
        key   = r.read_at.strftime('%Y-%m')
        label = r.read_at.strftime('%b %Y')
        diff  = round(r.value - prev.value, 3)
        if key not in monthly:
            monthly[key] = {'label': label, 'total': 0}
        monthly[key]['total'] = round(monthly[key]['total'] + diff, 3)

    sorted_months = sorted(monthly.items())
    return jsonify({
        'unit': meter.type_info['unit'],
        'color': meter.type_info['color'],
        'labels': [v['label'] for _, v in sorted_months],
        'values': [v['total'] for _, v in sorted_months],
    })


# ── Version ────────────────────────────────────────────────────────────────────
@app.route('/api/version')
def version():
    return jsonify({'version': APP_VERSION})


# ── Init ───────────────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
