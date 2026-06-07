import os
import re
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Initialize logger for this module
logger = logging.getLogger(__name__)

# Configuration constants
GDL_TZ = ZoneInfo("America/Mexico_City")

def get_calendar_service_with_creds(scopes, cred_file):
    """
    Initializes and returns a Google Calendar API service.
    
    Args:
        scopes (list): List of API scopes.
        cred_file (str): Path to the Google Service Account JSON file.
        
    Returns:
        Resource: A Google API service object.
    """
    if not os.path.exists(cred_file):
        logger.error(f"Google credentials file not found: {cred_file}")
        raise FileNotFoundError(f"Missing {cred_file}")
        
    credentials = service_account.Credentials.from_service_account_file(
        cred_file,
        scopes=scopes,
    )
    return build("calendar", "v3", credentials=credentials)

def extract_link(description):
    """
    Scans an event description for the first occurrence of a URL.
    
    Args:
        description (str): The event description text.
        
    Returns:
        str or None: The first URL found, or None if no link exists.
    """
    if not description:
        return None
    # Regex to capture standard http/https links, excluding trailing punctuation
    urls = re.findall(r'(https?://[^\s,]+)', description)
    if urls:
        # Clean trailing punctuation from the found URL (like periods or commas from sentence end)
        return urls[0].rstrip('.,')
    return None

def friendly_delta(td):
    """
    Converts a timedelta into a human-readable string in Spanish,
    rounding to the most significant unit (days, hours, or minutes).
    
    Args:
        td (timedelta): The time difference to format.
        
    Returns:
        str: A formatted string like "5 días", "3 horas", or "45 minutos".
    """
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
    """
    Fetches events from the Google Calendar within a specific time window.
    
    Args:
        service: The Google Calendar service object.
        calendar_id (str): The ID of the calendar to query.
        days (int): How many days into the future to check.
        
    Returns:
        tuple: (List of event objects, current UTC datetime)
    """
    now = datetime.now(timezone.utc)
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=days)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return events_result.get("items", []), now
    except Exception as e:
        logger.error(f"Failed to fetch events from Google Calendar: {e}")
        raise

def create_calendar_event(service, calendar_id, event_data):
    """
    Inserts a new event into the Google Calendar.
    
    Args:
        service: The Google Calendar service object.
        calendar_id (str): The ID of the calendar.
        event_data (dict): The event details (summary, location, start, end).
        
    Returns:
        dict: The created event object.
    """
    try:
        event = service.events().insert(calendarId=calendar_id, body=event_data).execute()
        logger.info(f"Event created: {event.get('htmlLink')}")
        return event
    except Exception as e:
        logger.error(f"Failed to create calendar event: {e}")
        raise
