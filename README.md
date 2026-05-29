# BTC GDL Message Bot

A Telegram bot designed to manage event reminders for the Bitcoin Guadalajara (BTC GDL) community by synchronizing with a Google Calendar.

## Features

*   **Automated Reminders:** Notifies subscribers at configurable intervals (1 week, 3 days, 24 hours, 1 hour before).
*   **Smart Formatting:** Professional HTML-formatted messages with 12h AM/PM time and clean date display.
*   **Link Extraction:** Automatically finds registration links in the Google Calendar event description.
*   **Timezone Aware:** Correctly handles and displays times for Guadalajara (`America/Mexico_City`).
*   **Deduplication:** Ensures users don't receive redundant notifications for the same event.
*   **Admin Broadcast:** Allows the administrator to send manual updates to all subscribers.

## Prerequisites

*   Python 3.13+
*   A Telegram Bot Token (from [@BotFather](https://t.me/botfather))
*   Google Cloud Project with **Calendar API** enabled.
*   A Google **Service Account JSON key**.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/btcgdl-message-bot.git
    cd btcgdl-message-bot
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install python-telegram-bot google-api-python-client google-auth-httplib2 google-auth python-dotenv
    ```

4.  **Configure Environment Variables:**
    Copy the template and fill in your credentials:
    ```bash
    cp .env.example .env
    ```
    Edit `.env` with your values:
    *   `TELEGRAM_BOT_TOKEN`: Your bot API key.
    *   `TELEGRAM_ADMIN_ID`: Your Telegram numeric user ID.
    *   `GOOGLE_CALENDAR_ID`: Your Gmail or the ID of the shared calendar.

5.  **Google Calendar Credentials:**
    Place your Service Account JSON file in the root directory and name it `google_calendar.json`.

## Usage

### Running the Bot
Ensure you are inside your virtual environment, then run:
```bash
python main.py
```

### Testing the Calendar Connection
You can verify that your Google credentials and Calendar ID are correct by running:
```bash
python test_calendar.py
```

## Internal Files (Ignored by Git)
*   `.env`: Sensitive credentials.
*   `google_calendar.json`: Google API private keys.
*   `data/subscribers.json`: List of active subscriber IDs.
*   `data/sent_reminders.json`: History of sent notifications.
*   `data/notified_promos.json`: History of events already notified to the admin.

## License
MIT License.
