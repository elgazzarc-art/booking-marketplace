# app.py
from flask import Flask, render_template, request, redirect, url_for, flash
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime
import pytz
import os
import sqlite3
from dataclasses import dataclass
from typing import List
import us

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your-secret-key-change-me-here'
SCOPES = ['https://www.googleapis.com/auth/calendar']
DB_NAME = 'partners.db'

# --- DATA MODELS ---
@dataclass
class Partner:
    id: int
    name: str
    email: str
    description: str
    rating: float
    calendar_type: str
    token_path: str
    services: list = None  # Will hold list of services

@dataclass
class TimeSlot:
    start: str
    display: str
    partner_id: int

# --- DATABASE ---
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
            calendar_type TEXT DEFAULT 'google'
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
        (2, "Mike's Auto Lessons", "mike@example.com", "DMV test expert", 4.9, "google"),
    ]
    c.executemany('INSERT OR IGNORE INTO partners VALUES (?,?,?,?,?,?)', sample_partners)
    sample_zips = [(1,"10001"), (1,"10002"), (2,"10001")]
    c.executemany('INSERT OR IGNORE INTO service_areas VALUES (?,?)', sample_zips)
    sample_services = [
        (1, "30-min Lesson", 45.00, 30),
        (1, "60-min Lesson", 85.00, 60),
        (1, "Test Prep Package", 200.00, 120),
        (2, "Beginner Lesson", 50.00, 45),
        (2, "Highway Training", 95.00, 60),
    ]
    c.executemany('INSERT OR IGNORE INTO services VALUES (NULL, ?, ?, ?, ?)', sample_services)
    conn.commit()
    conn.close()

# --- GOOGLE AUTH ---
def get_service_for_partner(token_path: str):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)

# --- ZIP → CITY ---
def get_location_for_zip(zip_code: str) -> dict:
    if zip_code in ['10001', '10002']:
        return {'city': 'New York', 'state': 'NY', 'timezone': 'America/New_York', 'display': 'New York, NY'}
    return {'city': 'Unknown', 'state': 'XX', 'timezone': 'America/New_York', 'display': 'Unknown'}

# --- PARTNERS & SERVICES ---
def get_partners_by_zip(zip_code: str) -> List[Partner]:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT p.id, p.name, p.email, p.description, p.rating, p.calendar_type
        FROM partners p
        JOIN service_areas s ON p.id = s.partner_id
        WHERE s.zip_code = ?
    ''', (zip_code,))
    rows = c.fetchall()
    partners = []
    for r in rows:
        partner = Partner(
            id=r[0], name=r[1], email=r[2], description=r[3],
            rating=r[4], calendar_type=r[5], token_path=f"token_{r[0]}.json"
        )
        c.execute('SELECT id, name, price, duration_minutes FROM services WHERE partner_id = ?', (r[0],))
        partner.services = [type('Service', (), {'id': s[0], 'name': s[1], 'price': s[2], 'duration': s[3]})() for s in c.fetchall()]
        partners.append(partner)
    conn.close()
    return partners

# --- AVAILABILITY ---
def get_available_slots(partner: Partner, date, local_tz_str: str) -> List[TimeSlot]:
    # ... (same as before, simplified for brevity)
    local_tz = pytz.timezone(local_tz_str)
    slots = []
    for hour in range(9, 17):
        slot_start_local = local_tz.localize(datetime.datetime.combine(date, datetime.time(hour, 0)))
        slots.append(TimeSlot(
            start=slot_start_local.isoformat(),
            display=slot_start_local.strftime('%I:%M %p'),
            partner_id=partner.id
        ))
    return slots

# --- ROUTES ---
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
    if not zip_code or len(zip_code) != 5:
        flash("Invalid zip")
        return redirect(url_for('index'))
    try:
        selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        flash("Invalid date")
        return redirect(url_for('index'))

    partners = get_partners_by_zip(zip_code)
    if not partners:
        flash("No instructors in this area")
        return redirect(url_for('index'))

    location = get_location_for_zip(zip_code)
    local_tz_str = location['timezone']
    availability = {}
    for partner in partners:
        slots = get_available_slots(partner, selected_date, local_tz_str)
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

    # POST → Save & go to payment
    if not request.form.get('learner_permit'):
        flash("You must confirm Learner's Permit")
        return redirect(request.url)
    meet_location = request.form['meet_location'].strip()
    if not meet_location:
        flash("Enter meet location")
        return redirect(request.url)

    # Save to session or DB later
    flash("Details saved! Proceeding to payment...")
    return redirect(url_for('confirm',
                            slot=request.form['slot'],
                            partner_id=request.form['partner_id'],
                            service_id=request.form['service_id'],
                            meet_location=meet_location))

@app.route('/confirm')
def confirm():
    return render_template('confirm.html')

# --- JOIN ROUTE (MULTI-ZIP + CALENDAR TYPE) ---
@app.route('/join', methods=['GET', 'POST'])
def join():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip()
        desc = request.form['description'].strip()
        zip_input = request.form['zip_codes'].strip()
        calendar_type = request.form['calendar_type']

        if not all([name, email, desc, zip_input, calendar_type]):
            flash("Please fill all fields")
            return render_template('join.html')

        raw_zips = [z.strip() for z in zip_input.split(',')]
        valid_zips = [z for z in raw_zips if len(z) == 5 and z.isdigit()]

        if not valid_zips:
            flash("At least one valid 5-digit zip code required")
            return render_template('join.html')

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT INTO partners (name, email, description, rating, calendar_type) 
            VALUES (?, ?, ?, 4.5, ?)
        ''', (name, email, desc, calendar_type))
        partner_id = c.lastrowid
        zip_tuples = [(partner_id, zip_code) for zip_code in valid_zips]
        c.executemany('INSERT OR IGNORE INTO service_areas (partner_id, zip_code) VALUES (?, ?)', zip_tuples)
        conn.commit()
        conn.close()

        flash(f"Welcome {name}! You're live in {len(valid_zips)} zip codes with {calendar_type} sync.")
        return redirect(url_for('index'))

    return render_template('join.html')
    
# --- INIT ---
init_db()

