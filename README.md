# BTC GDL Message Bot

A Telegram bot designed to manage event reminders for the Bitcoin Guadalajara (BTC GDL) community by synchronizing with a Google Calendar.

## Features

*   **Automated Reminders:** Notifies subscribers at configurable intervals (1 week, 3 days, 24 hours, 1 hour before).
*   **Smart Formatting:** Professional HTML-formatted messages with 12h AM/PM time and clean date display.
*   **Link Extraction:** Automatically finds registration links in the Google Calendar event description.
*   **Timezone Aware:** Correctly handles and displays times for Guadalajara (`America/Mexico_City`).
*   **Deduplication:** Ensures users don't receive redundant notifications for the same event.
*   **AI Flyer Drafts:** Generates admin-approved promo copy and flyers with OpenAI.
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
    *   `OPENAI_API_KEY`: Your OpenAI Platform API key.
    *   `OPENAI_IMAGE_MODEL`: Flyer image model. Start with `gpt-image-1-mini`.
    *   `OPENAI_IMAGE_QUALITY`: Flyer quality. Start with `medium` for cost control.

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

### Admin Prompt History
Generated flyer prompts are stored locally in `data/prompt_history.json`.

*   `/checkprompt`: Show the most recent image prompt.
*   `/checkprompts`: Show the last 5 image prompts.
*   `/helpadmin`: Show the admin command list.
*   `/pendingpromo`: Show the currently staged promo with publish/delete buttons.

## Internal Files (Ignored by Git)
*   `.env`: Sensitive credentials.
*   `google_calendar.json`: Google API private keys.
*   `data/subscribers.json`: List of active subscriber IDs.
*   `data/sent_reminders.json`: History of sent notifications.
*   `data/notified_promos.json`: History of events already notified to the admin.
*   `data/prompt_history.json`: History of generated flyer prompts.
*   `data/pending_promos.json`: Restart-safe staged promo waiting for publication.

## License
MIT License.
