# BTC GDL Message Bot - Architecture Map

## Overview
The bot is built with a modular architecture in Python, following a **Dispatcher-Service-Tool** pattern. It uses natural language processing to bridge the gap between community management and Google Calendar.

## File Map

```mermaid
graph TD
    subgraph Bot Layer
        Main[main.py] --> Handlers[bot/handlers/]
        Handlers --> UserH[user.py]
        Handlers --> AdminH[admin.py]
    end

    subgraph Core Logic
        AdminH --> Promoter[core/promoter.py]
        Promoter --> Prompts[core/prompts.py]
    end

    subgraph Service Layer
        AdminH --> OpenAI[services/openai_service.py]
        Promoter --> OpenAI
    end

    subgraph Data & Tools
        OpenAI --> API_OpenAI((OpenAI API))
        Promoter --> CalAPI[tools/local/calendar_api.py]
        CalAPI --> API_Google((Google Calendar API))
        Handlers --> DataMan[tools/local/data_manager.py]
        DataMan --> JSON[(data/*.json)]
    end
```

### Component Details

### 1. **Bot Layer (`main.py`, `bot/`)**
- **`main.py`**: The entry point. Initializes the `Application`, registers all command and message handlers, and starts the background job for calendar polling.
- **`bot/handlers/user.py`**: Logic for public subscribers (Menu, Events list, Start).
- **`bot/handlers/admin.py`**: The complex state machine for community managers. Handles text/voice intent detection, draft refinement, and event orchestration.

### 2. **Core Layer (`core/`)**
- **`core/promoter.py`**: The "brain" of the automated marketing. It polls the calendar, detects new events, and initiates the "draft -> refine -> publish" workflow.
- **`core/prompts.py`**: Central repository for AI personas and system instructions (Intent detection, Copywriting, DALL-E flyers).

### 3. **Service Layer (`services/`)**
- **`services/openai_service.py`**: Abstracted wrapper for OpenAI. Handles GPT-4o text generation, Whisper transcription, OpenAI TTS-1 speech synthesis, and image generation.

### 4. **Tools & Data Layer (`tools/`, `data/`)**
- **`tools/local/calendar_api.py`**: Low-level wrapper for Google Calendar v3. Handles OAuth2/Service Account auth and CRUD operations on events.
- **`tools/local/data_manager.py`**: Manages persistent JSON storage with atomic writes to prevent data corruption.
- **`data/`**: Storage for subscribers, reminder rules, log history, and staged drafts.

## Design Principles
- **Natural Language First:** Prioritizes voice notes and plain chat over rigid slash commands.
- **Resilient Polling:** Uses a robust JobQueue to ensure reminders are never missed even if the bot restarts.
- **Context Preservation:** Staged drafts and pending promotions are saved to disk, allowing the admin to finish a task hours later.
- **Surgical Logging:** Structured logs for tracing AI decisions and API interactions.
