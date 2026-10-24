# Telegram Metadata

The bot keeps readable Telegram user and group metadata in a separate runtime
file. Numeric Telegram IDs remain the canonical identifiers used for delivery,
notification preferences, permissions, and membership records.

## Configuration

Set the metadata path in `.env` when a different location is needed:

```dotenv
TELEGRAM_METADATA_FILE=data/telegram_metadata.json
```

The generated JSON file is runtime data and is ignored by Git. Do not commit it
or paste it into public issues.

## Captured Metadata

The bot records the latest values Telegram provides:

- Users: numeric ID, display name, first name, last name, and username.
- Groups: numeric chat ID, title, username, and chat type.

User metadata is refreshed when a user opens `/start`, opens
`/suscripciones`, or uses a private inline-menu action. Group metadata is
refreshed when an administrator runs `/addgroup` inside that group.

The existing `data/subscribers.json` and `data/groups.json` files remain the
authoritative ID lists. Metadata is descriptive only; changing a display name
does not change a subscription or a broadcast target.

## Administrator Views

Use these commands in the private administrator chat:

```text
/groups
/users
```

`/groups` shows each configured group with its readable name, username when
available, and numeric ID. `/users` shows known users, their usernames and IDs,
and whether each user is currently subscribed to event notifications.

Older group entries that only have a numeric ID remain usable and are shown as
`Grupo sin nombre` until the bot receives metadata for them again. Likewise,
users already present in the subscriber list remain visible even if they have
not yet opened the bot after this feature was deployed.
