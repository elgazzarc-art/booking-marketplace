# app.py - Multi-Calendar Booking Marketplace
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import datetime
import pytz
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Dict, Optional
import us
import json

# Nylas (unified calendar API for Google, Outlook, iCloud, etc.)
try:
    from nylas import APIClient as NylasClient
    NYLAS_AVAILABLE = True
except ImportError:
    NYLAS_AVAILABLE = False
    print("Nylas not installed - using Google only")

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-me')

SCOPES = ['https://www.googleapis.com/auth/calendar']
DB_NAME = 'partners.db'

# Load Nylas config if available
NYLAS_CONFIG = {}
if os.path.exists('nylas_credentials.json'):
    try:
        with open('nylas_credentials.json') as f:
            NYLAS_CONFIG = json.load(f)
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
    calendar_type: str  # google, outlook, apple, custom
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
    partner_id
