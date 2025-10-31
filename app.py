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
import json
from nylas import Client as NylasClient  # Nylas for multi-provider

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your-secret-key-change-me-here'
SCOPES = ['https://www.googleapis.com/auth/calendar']
DB_NAME = 'partners.db'

# Load Nylas credentials
with open('nylas_credentials.json') as f:
    NYLAS_CONFIG = json.load(f)

nylas = NylasClient(NYLAS_CONFIG['api_key'])

@dataclass
class Partner:
    id: int
    name: str
    email: str
    description: str
    rating: float
    calendar_type: str
    nylas_account_id: str = None  # Nylas account ID for sync

@dataclass
class TimeSlot:
    start: str
    display: str
    partner_id: int

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
    # Sample data
    sample_partners = [
        (1, "Sarah's Driving School", "sarah@example.com", "Patient & certified", 4.8, "google"),
        (2, "Mike's Auto Lessons", "mike@outlook.com", "DMV test expert", 4.9, "outlook"),
        (3, "Apple Driving Co", "lisa@icloud.com", "Eco-friendly lessons", 4.7, "apple"),
    ]
    c.executemany('INSERT OR IGNORE INTO partners VALUES (?,?,?,?,?,?,NULL)', sample_partners)
    sample_zips = [(1,"10001"), (2,"10001"), (3,"10001")]
    c.executemany('INSERT OR IGNORE INTO service_areas VALUES (?,?)', sample_zips)
    conn.commit()
    conn.close()

# --- Multi-Calendar Adapter ---
class CalendarAdapter:
    def get_events(self, partner, date, tz): raise NotImplementedError
    def create_event(self, partner, slot, name, email): raise NotImplementedError
    def connect_account(self, partner): raise NotImplementedError

class NylasAdapter(CalendarAdapter):
    def connect_account(self, partner):
        # Nylas Hosted Auth URL
        auth_url = nylas.authentication_url(
            f"{NYLAS_CONFIG['client_id']}",
            scopes=['calendar'],
            login_hint=partner.email
        )
        return auth_url

    def get_events(self, partner, date, tz):
        events = nylas.events.where(
            calendar_id=partner.nylas_account_id,
            starts_after=date.isoformat(),
            ends_before=(date + datetime.timedelta(days=1)).isoformat()
        ).all()
        return events

    def create_event(self, partner, slot, name, email):
        event = {
            'title': f'Booking: {name}',
            'description': f'Email: {email}',
            'when': {'start_time': slot, 'end_time': slot + datetime.timedelta(hours=1)}
        }
        created = nylas.events.create(partner.nylas_account_id, event)
        return created

# --- Provider-Specific Adapters ---
def get_adapter(calendar_type):
    if calendar_type == 'nylas':  # Unified for all
        return NylasAdapter()
    # Native fallback
    from code import NylasAdapter as Default
    return Default()

# --- ZIP → CITY ---
def get_location_for_zip(zip_code: str) → dict:
    try:
        zip_info = us.zips.get(zip_code)
        if zip_info:
            return {'city': zip_info.city, 'state': zip_info.state, 'timezone': 'America/New_York', 'display': f"{zip_info.city}, {zip_info.state}"}
    except:
        pass
    return {'city': 'Unknown', 'state': 'XX', 'timezone': 'America/New_York', 'display': 'Unknown'}

# --- Partners by ZIP ---
def get_partners_by_zip(zip_code: str) → List[Partner]:
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

# --- Availability ---
def get_available_slots(partner: Partner, date, local_tz_str: str) → List[TimeSlot]:
    adapter = get_adapter(partner.calendar_type)
    events = adapter.get_events(partner, date, local_tz_str)
    # Merge with free slots (9-5 PM)
    slots = []
    local_tz = pytz.timezone(local_tz_str)
    for hour in range(9, 17):
        slot_start = local_tz.localize(datetime.datetime.combine(date, datetime.time(hour, 0)))
        # Check if booked
        is_booked = any(slot_start < event.end_time and slot_start + datetime.timedelta(hours=1) > event.start_time for event in events)
        if not is_booked:
            slots.append(TimeSlot(slot_start.isoformat(), slot_start.strftime('%I:%M %p'), partner.id))
    return slots

# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        zip_code = request.form['zip_code'].strip()
        date_str = request.form['date']
        return redirect(url_for('search', zip=zip_code, date=date_str))
    today = datetime.date.today().strftime('%Y-%m-%d')
    return render_template('index.html', today=today)

@app.route('/search')
def search():
    zip_code = request.args.get('zip')
    date_str = request.args.get('date')
    if not zip_code or len(zip_code) != 5:
        flash("Invalid ZIP")
        return redirect(url_for('index'))
    selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    partners = get_partners_by_zip(zip_code)
    location = get_location_for_zip(zip_code)
    local_tz_str = location['timezone']
    availability = {}
    for partner in partners:
        slots = get_available_slots(partner, selected_date, local_tz_str)
        if slots:
            availability[partner.id] = {'partner': partner, 'slots': slots}
    return render_template('results.html', availability=availability, zip_code=zip_code, date=selected_date.strftime('%A, %B %d'), location=location['display'])

@app.route('/book', methods=['GET', 'POST'])
def book():
    if request.method == 'GET':
        # ... (your existing GET logic)
        return render_template('book.html', ...)
    # POST
    # ... (your existing POST logic)
    adapter = get_adapter(calendar_type)
    created = adapter.create_event(partner, slot_dt, name, email)
    flash("Booked! Event ID: " + created.id)
    return redirect(url_for('success'))

@app.route('/join', methods=['GET', 'POST'])
def join():
    if request.method == 'POST':
        # ... (your existing POST logic)
        # Add Nylas account ID after auth
        pass
    return render_template('join.html')

# --- Nylas Webhook for Sync ---
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.json
    # Handle Calendar changes from Nylas
    # Block slot on your site
    flash("Slot updated from Calendar!")
    return 'OK', 200

# --- INIT ---
init_db()
