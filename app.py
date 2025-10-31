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
import us  # zip → state → timezone

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your-secret-key-change-me-here'  # CHANGE THIS!
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
    calendar_type: str  # NEW: google, outlook, apple, custom
    token_path: str

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
    # Sample data with calendar_type
    sample_partners = [
        (1, "Sarah's Plumbing", "your-email@gmail.com", "Fast & reliable plumbing", 4.8, "google"),
        (2, "Mike's Electric", "your-email@gmail.com", "Same-day service", 4.9, "google"),
        (3, "CleanPro NYC", "your-email@gmail.com", "Eco-friendly cleaning", 4.7, "google"),
    ]
    c.executemany('INSERT OR IGNORE INTO partners VALUES (?,?,?,?,?,?)', sample_partners)
    sample_zips = [(1,"10001"), (1,"10002"), (2,"10001"), (3,"10001")]
    c.executemany('INSERT OR IGNORE INTO service_areas VALUES (?,?)', sample_zips)
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

# --- ZIP → CITY + STATE + TIMEZONE (ROBUST) ---
ZIP_CITY_MAP = {
    '94596': {'city': 'Walnut Creek', 'state': 'CA', 'tz': 'America/Los_Angeles'},
    '10001': {'city': 'New York', 'state': 'NY', 'tz': 'America/New_York'},
    '90210': {'city': 'Beverly Hills', 'state': 'CA', 'tz': 'America/Los_Angeles'},
    '60601': {'city': 'Chicago', 'state': 'IL', 'tz': 'America/Chicago'},
    '33139': {'city': 'Miami Beach', 'state': 'FL', 'tz': 'America/New_York'},
    '98101': {'city': 'Seattle', 'state': 'WA', 'tz': 'America/Los_Angeles'},
    '78701': {'city': 'Austin', 'state': 'TX', 'tz': 'America/Chicago'},
    '30301': {'city': 'Atlanta', 'state': 'GA', 'tz': 'America/New_York'},
    '94102': {'city': 'San Francisco', 'state': 'CA', 'tz': 'America/Los_Angeles'},
    '02108': {'city': 'Boston', 'state': 'MA', 'tz': 'America/New_York'},
    # Add more as needed
}

def get_location_for_zip(zip_code: str) -> dict:
    # 1. Try us package
    try:
        zip_info = us.zips.get(zip_code)
        if zip_info and zip_info.city and zip_info.state:
            state = zip_info.state
            city = zip_info.city
            tz_map = {
                'NY': 'America/New_York', 'CA': 'America/Los_Angeles', 'TX': 'America/Chicago',
                'FL': 'America/New_York', 'IL': 'America/Chicago', 'PA': 'America/New_York',
                'OH': 'America/New_York', 'GA': 'America/New_York', 'NC': 'America/New_York',
                'MI': 'America/New_York', 'WA': 'America/Los_Angeles', 'MA': 'America/New_York',
            }
            timezone = tz_map.get(state, 'America/New_York')
            return {
                'city': city,
                'state': state,
                'timezone': timezone,
                'display': f"{city}, {state}"
            }
    except:
        pass

    # 2. Try manual map
    if zip_code in ZIP_CITY_MAP:
        data = ZIP_CITY_MAP[zip_code]
        return {
            'city': data['city'],
            'state': data['state'],
            'timezone': data['tz'],
            'display': f"{data['city']}, {data['state']}"
        }

    # 3. Final fallback
    return {
        'city': 'Unknown City',
        'state': 'XX',
        'timezone': 'America/New_York',
        'display': 'Unknown Location'
    }
# --- PARTNERS BY ZIP ---
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
    conn.close()
    return [Partner(
        id=r[0], name=r[1], email=r[2], description=r[3],
        rating=r[4], calendar_type=r[5], token_path=f"token_{r[0]}.json"
    ) for r in rows]

# --- AVAILABILITY (Google only for now) ---
def get_available_slots(partner: Partner, date, local_tz_str: str) -> List[TimeSlot]:
    local_tz = pytz.timezone(local_tz_str)
    start_of_day_local = local_tz.localize(datetime.datetime.combine(date, datetime.time(0, 0)))
    end_of_day_local = start_of_day_local + datetime.timedelta(days=1)

    # Convert to UTC once
    start_utc = start_of_day_local.astimezone(pytz.UTC)
    end_utc = end_of_day_local.astimezone(pytz.UTC)

    try:
        service = get_service_for_partner(partner.token_path)
        events_result = service.events().list(
            calendarId=partner.email,
            timeMin=start_utc.isoformat(),
            timeMax=end_utc.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
    except Exception as e:
        print(f"Calendar error for {partner.name}: {e}")
        return []

    slots = []
    for hour in range(9, 17):
        slot_start_local = local_tz.localize(datetime.datetime.combine(date, datetime.time(hour, 0)))
        slot_end_local = slot_start_local + datetime.timedelta(hours=1)

        # Convert slot to UTC for comparison
        slot_start_utc = slot_start_local.astimezone(pytz.UTC)
        slot_end_utc = slot_end_local.astimezone(pytz.UTC)

        is_booked = False
        for event in events:
            if 'dateTime' not in event['start']:
                continue
            try:
                event_start_str = event['start']['dateTime'].replace('Z', '+00:00')
                event_end_str = event['end']['dateTime'].replace('Z', '+00:00')
                event_start = datetime.datetime.fromisoformat(event_start_str)
                event_end = datetime.datetime.fromisoformat(event_end_str)

                # Ensure event times are timezone-aware
                if event_start.tzinfo is None:
                    event_start = pytz.UTC.localize(event_start)
                if event_end.tzinfo is None:
                    event_end = pytz.UTC.localize(event_end)

                # Compare in UTC
                if slot_start_utc < event_end and slot_end_utc > event_start:
                    is_booked = True
                    break
            except Exception as e:
                print(f"Event parse error: {e}")
                continue

        if not is_booked:
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
        try:
            selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            if selected_date < datetime.date.today():
                flash("Please select a future date")
                return render_template('index.html', today=today)
        except:
            flash("Invalid date")
            return render_template('index.html', today=today)
        return redirect(url_for('search', zip=zip_code, date=date_str))
    return render_template('index.html', today=today)
    
@app.route('/search')
def search():
    zip_code = request.args.get('zip')
    date_str = request.args.get('date')
    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        flash("Invalid zip code")
        return redirect(url_for('index'))
    try:
        selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        flash("Invalid date")
        return redirect(url_for('index'))

    partners = get_partners_by_zip(zip_code)
    if not partners:
        flash(f"No partners serve {zip_code}")
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
                       location=location['display'],
                       city=location['city'],
                       state=location['state'])
@app.route('/book')
def book():
    slot_start = request.args.get('slot')
    partner_id = request.args.get('partner_id')
    name = request.args.get('name')
    email = request.args.get('email')
    zip_code = request.args.get('zip')
    date_str = request.args.get('date')

    if not all([slot_start, partner_id, name, email, zip_code, date_str]):
        flash("Missing information")
        return redirect(url_for('index'))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM partners WHERE id = ?', (partner_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        flash("Partner not found")
        return redirect(url_for('index'))

    partner = Partner(*row, token_path=f"token_{partner_id}.json")

    # FIXED: Use get_location_for_zip instead of old function
    location = get_location_for_zip(zip_code)
    local_tz_str = location['timezone']
    local_tz = pytz.timezone(local_tz_str)

    # Handle timezone-aware ISO string
    try:
        slot_dt = datetime.datetime.fromisoformat(slot_start)
        if slot_dt.tzinfo is None:
            slot_dt = local_tz.localize(slot_dt)
    except Exception as e:
        flash("Invalid time format")
        return redirect(url_for('index'))

    try:
        if partner.calendar_type == 'google':
            service = get_service_for_partner(partner.token_path)
            event = {
                'summary': f'Booking: {name}',
                'description': f'Email: {email}\nZip: {zip_code}',
                'start': {'dateTime': slot_dt.astimezone(pytz.UTC).isoformat(), 'timeZone': local_tz_str},
                'end': {'dateTime': (slot_dt + datetime.timedelta(hours=1)).astimezone(pytz.UTC).isoformat(), 'timeZone': local_tz_str},
            }
            service.events().insert(calendarId=partner.email, body=event).execute()
        elif partner.calendar_type == 'outlook':
            headers = get_outlook_service(partner)
            if not headers:
                flash("Outlook not connected")
                return redirect(url_for('index'))
            url = "https://graph.microsoft.com/v1.0/me/calendar/events"
            event = {
                "subject": f"Booking: {name}",
                "body": {"content": f"Email: {email}\nZip: {zip_code}", "contentType": "text"},
                "start": {"dateTime": slot_dt.isoformat(), "timeZone": local_tz_str},
                "end": {"dateTime": (slot_dt + datetime.timedelta(hours=1)).isoformat(), "timeZone": local_tz_str}
            }
            resp = requests.post(url, headers=headers, json=event)
            resp.raise_for_status()
        else:
            flash("Calendar type not supported")
            return redirect(url_for('index'))

        flash(f"Booked with {partner.name} at {slot_dt.strftime('%I:%M %p %Z')} in {location['city']}, {location['state']}!")
    except Exception as e:
        flash(f"Booking failed: {e}")

    return redirect(url_for('index'))
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

# --- INIT & RUN ---
# --- INIT DB ON STARTUP (Render) ---
init_db()

