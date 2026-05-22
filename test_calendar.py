from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Load environment variables
load_dotenv()

SERVICE_ACCOUNT_FILE = "google_calendar.json"
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES,
)

service = build("calendar", "v3", credentials=credentials)


now = datetime.now(timezone.utc).isoformat()

events_result = service.events().list(
    calendarId=CALENDAR_ID,
    timeMin=now,
    maxResults=5,
    singleEvents=True,
    orderBy="startTime",
).execute()

events = events_result.get("items", [])

if not events:
    print("No upcoming events found.")
else:
    print("Upcoming events:")
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        print(f"- {start} | {event['summary']}")
