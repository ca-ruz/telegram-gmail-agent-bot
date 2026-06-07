# BTC GDL Message Bot

A Telegram bot designed to manage community event reminders and automated AI-powered marketing for Bitcoin Guadalajara (BTC GDL).

## Features

*   **Natural Language Assistant:** Manage your community hands-free. The bot understands plain Spanish for adding, editing, or deleting events (optimized for talk-to-text dictation).
*   **Calendar Management:** Create, edit, and delete Google Calendar events directly from Telegram with the `/addevent`, `/editevent`, and `/deleteevent` commands.
*   **Proactive AI Marketing:** Automatically detects new calendar events and offers to generate AI-powered flyers and Telegram posts using GPT-4o and DALL-E 3.
*   **Interactive Refinement:** Don't like a draft? Just reply to it with instructions (e.g., "Make it more professional") and the AI will regenerate it.
*   **Automated Reminders:** Notifies subscribers at configurable intervals (1 week, 3 days, 24 hours, 1 hour before).
*   **Smart Formatting:** Professional HTML-formatted messages with 12h AM/PM time and clean date display.
*   **Link Extraction:** Automatically finds registration links in the Google Calendar event description.
*   **Timezone Aware:** Correctly handles and displays times for Guadalajara (`America/Mexico_City`).
*   **Multi-Channel Broadcast:** One-click publishing to all community groups and individual subscribers.
*   **Deduplication:** Ensures users don't receive redundant notifications for the same event and filters recurring series to show only the closest instance.

## Prerequisites

*   Python 3.13+
*   A Telegram Bot Token (from [@BotFather](https://t.me/botfather))
*   Google Cloud Project with **Calendar API** enabled and **Writer** access granted to the Service Account.
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
    pip install -r requirements.txt
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
    *   `OPENAI_IMAGE_MODEL`: Flyer image model (`gpt-image-1-mini`).
    *   `OPENAI_IMAGE_QUALITY`: Flyer quality (`medium`).
    *   `OPENAI_IMAGE_SIZE`: Flyer dimensions (`1024x1536`).

5.  **Google Calendar Credentials:**
    Place your Service Account JSON file in the root directory and name it `google_calendar.json`. Ensure the service account email has **"Make changes to events"** permissions on your calendar.

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

### Admin Commands
*   `/addevent [desc]`: Create a new event using natural language.
*   `/editevent`: Interactively edit an existing event.
*   `/deleteevent`: Interactively delete an event from the calendar.
*   `/draft`: Manually trigger a promo generation for an upcoming event.
*   `/pendingpromo`: View and publish the currently staged draft.
*   `/status`: Check bot health and configurations.
*   `/checkprompt`: Show the most recent image prompt.
*   `/checkprompts`: Show the last 5 image prompts.
*   `/helpadmin`: Show all available admin tools.

**💡 Pro Tip:** You can also just talk to the bot! Try saying *"Agrega un meetup para mañana a las 5pm"* or *"Cómo va el status?"* without any slashes.

## Internal Files (Ignored by Git)
*   `.env`: Sensitive credentials.
*   `google_calendar.json`: Google API private keys.
*   `data/subscribers.json`: List of active subscriber IDs.
*   `data/sent_reminders.json`: History of sent notifications.
*   `data/notified_promos.json`: History of events already notified to the admin.
*   `data/prompt_history.json`: History of generated flyer prompts.
*   `data/pending_promos.json`: Restart-safe staged promo waiting for publication.
*   `data/bot.log`: Structured application logs.

## License
MIT License.
