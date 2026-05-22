import os
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configuration
SERVICE_ACCOUNT_FILE = "google_calendar.json"
GDL_TZ = ZoneInfo("America/Mexico_City")

def get_calendar_service(scopes):
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Missing {SERVICE_ACCOUNT_FILE}")
        
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES, # This will be passed from config or main
    )
    return build("calendar", "v3", credentials=credentials)

# Fixed to use the parameter instead of a global
def get_calendar_service_with_creds(scopes, cred_file):
    credentials = service_account.Credentials.from_service_account_file(
        cred_file,
        scopes=scopes,
    )
    return build("calendar", "v3", credentials=credentials)

def extract_link(description):
    if not description:
        return None
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', description)
    return urls[0] if urls else None

def friendly_delta(td):
    total_seconds = td.total_seconds()
    
    if total_seconds >= 86400:  # 1 day or more
        rounded_days = round(total_seconds / 86400)
        return f"{rounded_days} {'día' if rounded_days == 1 else 'días'}"
    
    if total_seconds >= 3600:  # 1 hour or more
        rounded_hours = round(total_seconds / 3600)
        return f"{rounded_hours} {'hora' if rounded_hours == 1 else 'horas'}"
    
    rounded_minutes = round(total_seconds / 60)
    if rounded_minutes > 0:
        return f"{rounded_minutes} {'minuto' if rounded_minutes == 1 else 'minutos'}"
    
    return "menos de 1 minuto"

def fetch_upcoming_events(service, calendar_id, days=8):
    now = datetime.now(timezone.utc)
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=days)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return events_result.get("items", []), now
