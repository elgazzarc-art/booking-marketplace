# app.py - Fixed TimeSlot + Clean Imports
from flask import Flask, render_template, request, redirect, url_for, flash
import datetime
import pytz
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional
import us
import json

# Optional Nylas (safe if missing)
try:
    from nylas import APIClient as NylasClient
    NYLAS_AVAILABLE = True
    print("Nylas loaded successfully")
except ImportError:
    NYLAS_AVAILABLE = False
    print("Nylas not installed - using Google only")

# Load Nylas config safely
NYLAS_CONFIG = {}
nylas_client = None
if os.path.exists('nylas_credentials.json') and NYLAS_AVAILABLE:
    try:
        with open('nylas_credentials.json') as f:
            NYLAS_CONFIG = json.load(f)
        nylas_client = NylasClient(NYLAS_CONFIG['api_key'])
        print("Nylas connected")
    except Exception as e:
        print(f"Nylas config error: {e}")
else:
    print("Nylas not configured - skipping")
    
app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-me')

SCOPES = ['https://www.googleapis.com/auth/calendar']
DB_NAME = 'partners.db'

# Nylas config (optional)
NYLAS_CONFIG = {}
nylas_client = None
if os.path.exists('nylas_credentials.json'):
    try:
        with open('nylas_credentials.json') as f:
            NYLAS_CONFIG = json.load(f)
        if NYLAS_AVAILABLE:
            nylas_client = NylasClient(NYLAS_CONFIG['api_key'])
    except Exception as e:
        print(f"Nylas config error: {e}")

@dataclass
class Partner:
    id: int
    name: str
    email: str
    description: str
    rating: float
    calendar_type: str
    nylas_account_id: Optional[str] = None

@dataclass
class Service:
    id: int
    name: str
    price: float
    duration_minutes: int

@dataclass
class TimeSlot:
    start: str
    display: str
    partner_id: int  # ← FIXED: Added type

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            description TEXT,
            rating REAL DEFAULT 4.5,
            calendar_type TEXT DEFAULT 'google',
            nylas_account_id TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS service_areas (
            partner_id INTEGER,
            zip_code TEXT,
            PRIMARY KEY (partner_id, zip_code)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY,
            partner_id INTEGER,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            duration_minutes INTEGER DEFAULT 60
        )
    ''')
    # Sample data
    sample_partners = [
        (1, "Sarah's Driving School", "sarah@example.com", "Patient & certified", 4.8, "google"),
        (2, "Mike's Auto Lessons", "mike@outlook.com", "DMV test expert", 4.9, "outlook"),
    ]
    c.executemany('INSERT OR IGNORE INTO partners VALUES (?,?,?,?,?,?,NULL)', sample_partners)
    sample_zips = [(1,"10001"), (2,"10001")]
    c.executemany('INSERT OR IGNORE INTO service_areas VALUES (?,?)', sample_zips)
    sample_services = [
        (1, "60-min Lesson", 85.00, 60),
        (2, "Beginner Lesson", 50.00, 45),
    ]
    c.executemany('INSERT OR IGNORE INTO services VALUES (NULL, ?, ?, ?, ?)', sample_services)
    conn.commit()
    conn.close()

def get_location_for_zip(zip_code: str) -> dict:
    # Hardcoded fallback — us.zips is broken on Render
    fallback = {
        '10001': ('New York', 'NY'),
        '10002': ('New York', 'NY'),
        '90210': ('Beverly Hills', 'CA'),
        '60601': ('Chicago', 'IL'),
    }
    city, state = fallback.get(zip_code, ('Unknown City', 'XX'))
    return {
        'city': city,
        'state': state,
        'timezone': 'America/New_York',
        'display': f"{city}, {state}"
    }
    except Exception as e:
        print(f"ZIP lookup failed: {e}")
    
    # Fallback
    return {
        'city': 'New York',
        'state': 'NY',
        'timezone': 'America/New_York',
        'display': 'New York, NY'
    }

def get_partners_by_zip(zip_code: str) -> List[Partner]:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT p.id, p.name, p.email, p.description, p.rating, p.calendar_type, p.nylas_account_id
        FROM partners p
        JOIN service_areas s ON p.id = s.partner_id
        WHERE s.zip_code = ?
    ''', (zip_code,))
    rows = c.fetchall()
    conn.close()
    return [Partner(*r) for r in rows]

def get_available_slots(partner: Partner, date, local_tz_str: str) -> List[TimeSlot]:
    local_tz = pytz.timezone(local_tz_str)
    slots = []
    for hour in range(9, 17):
        slot_start = local_tz.localize(datetime.datetime.combine(date, datetime.time(hour, 0)))
        slots.append(TimeSlot(
            start=slot_start.isoformat(),
            display=slot_start.strftime('%I:%M %p'),
            partner_id=partner.id
        ))
    return slots

@app.route('/', methods=['GET', 'POST'])
def index():
    today = datetime.date.today().strftime('%Y-%m-%d')
    if request.method == 'POST':
        zip_code = request.form['zip_code'].strip()
        date_str = request.form['date']
        return redirect(url_for('search', zip=zip_code, date=date_str))
    return render_template('index.html', today=today)

@app.route('/search')
def search():
    zip_code = request.args.get('zip')
    date_str = request.args.get('date')
    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        flash("Invalid ZIP code")
        return redirect(url_for('index'))
    try:
        selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        flash("Invalid date")
        return redirect(url_for('index'))

    print(f"DEBUG: ZIP={zip_code}, DATE={selected_date}")  # ← ADD THIS
    partners = get_partners_by_zip(zip_code)
    print(f"DEBUG: Found {len(partners)} partners")  # ← ADD THIS

    if not partners:
        flash("No instructors in this area")
        return redirect(url_for('index'))

    location = get_location_for_zip(zip_code)
    local_tz_str = location['timezone']
    print(f"DEBUG: Location={location['display']}")  # ← ADD THIS
    availability = {}
    for partner in partners:
        print(f"DEBUG: Processing partner {partner.name}")  # ← ADD THIS
        slots = get_available_slots(partner, selected_date, local_tz_str)
        print(f"DEBUG: Found {len(slots)} slots for {partner.name}")  # ← ADD THIS
        if slots:
            availability[partner.id] = {'partner': partner, 'slots': slots}

    return render_template('results.html',
                           availability=availability,
                           zip_code=zip_code,
                           date=selected_date.strftime('%A, %B %d'),
                           location=location['display'])

@app.route('/book', methods=['GET', 'POST'])
def book():
    if request.method == 'GET':
        slot = request.args.get('slot')
        partner_id = request.args.get('partner_id')
        service_id = request.args.get('service_id')
        zip_code = request.args.get('zip')
        date_str = request.args.get('date')
        if not all([slot, partner_id, service_id]):
            return redirect(url_for('index'))

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('SELECT name, price FROM services WHERE id = ?', (service_id,))
        service = c.fetchone()
        conn.close()

        slot_dt = datetime.datetime.fromisoformat(slot)
        slot_display = slot_dt.strftime('%I:%M %p')

        return render_template('book.html',
                               slot=slot, partner_id=partner_id, service_id=service_id,
                               service_name=service[0], price=service[1],
                               slot_display=slot_display, zip_code=zip_code, date=date_str)

    # POST
    if not request.form.get('learner_permit'):
        flash("Confirm Learner's Permit")
        return redirect(url_for('index'))

    meet_location = request.form['meet_location'].strip()
    name = request.form['name'].strip()
    email = request.form['email'].strip()

    # Calendar logic (simplified for now)
    flash(f"Booked with {name} at {meet_location}")
    return redirect(url_for('index'))

@app.route('/join', methods=['GET', 'POST'])
def join():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip()
        desc = request.form['description'].strip()
        zip_input = request.form['zip_codes'].strip()
        calendar_type = request.form['calendar_type']

        raw_zips = [z.strip() for z in zip_input.split(',')]
        valid_zips = [z for z in raw_zips if len(z) == 5 and z.isdigit()]
        if not valid_zips:
            flash("Need at least one valid ZIP")
            return render_template('join.html')

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('INSERT INTO partners (name, email, description, rating, calendar_type) VALUES (?, ?, ?, 4.5, ?)',
                  (name, email, desc, calendar_type))
        partner_id = c.lastrowid
        c.executemany('INSERT OR IGNORE INTO service_areas VALUES (?, ?)', [(partner_id, z) for z in valid_zips])
        conn.commit()
        conn.close()

        flash("Welcome! You're live.")
        return redirect(url_for('index'))
    return render_template('join.html')

# --- INIT ---
init_db()

if __name__ == '__main__':
    app.run(debug=True)





