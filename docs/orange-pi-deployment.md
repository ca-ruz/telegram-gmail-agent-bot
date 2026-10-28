# Orange Pi Deployment

This guide describes a generic deployment of the bot on a Debian-based Orange
Pi or similar ARM Linux device. Replace the placeholder repository, service,
user, and directory names with values appropriate for your deployment.

Never place real tokens, Telegram IDs, private URLs, passwords, or service
credentials in this document or in Git. Keep them in the private `.env` file
and protected runtime files on the device.

## Assumptions

The examples use these placeholders:

```text
Repository: https://github.com/ORG/REPOSITORY.git
Service user: botuser
Application directory: /opt/community-bot
Systemd service: community-bot.service
```

The device should run a current Debian-based operating system with network
access and a stable system clock.

## Install System Packages

Update the package index and install the runtime tools:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3 python3-pip python3-venv
```

Create a dedicated unprivileged service account and application directory:

```bash
sudo adduser --system --group --home /opt/community-bot botuser
sudo install -d -o botuser -g botuser /opt/community-bot
```

## Install the Application

Clone the repository as the service user:

```bash
sudo -u botuser git clone https://github.com/ORG/REPOSITORY.git /opt/community-bot
cd /opt/community-bot
```

Create the virtual environment and install the application dependencies:

```bash
sudo -u botuser python3 -m venv /opt/community-bot/.venv
sudo -u botuser /opt/community-bot/.venv/bin/python -m pip install --upgrade pip
sudo -u botuser /opt/community-bot/.venv/bin/pip install \
  -r /opt/community-bot/requirements.txt
```

## Configure Private Runtime Files

Create the private environment file from the repository template:

```bash
sudo -u botuser cp /opt/community-bot/.env.example /opt/community-bot/.env
sudo chmod 600 /opt/community-bot/.env
sudo -u botuser editor /opt/community-bot/.env
```

Set deployment-specific values, including the bot token, administrator ID,
notification settings, event source, and optional service URLs. Do not commit
`.env`.

If the bot uses Google Calendar, copy the service-account credential file to
the application directory using a secure transfer method:

```bash
sudo install -o botuser -g botuser -m 600 /path/to/google_calendar.json \
  /opt/community-bot/google_calendar.json
```

Do not paste credential contents into chat, tickets, logs, or documentation.

## Optional Tailscale Access

Tailscale can provide private administrative access without exposing SSH or
the bot service publicly. Install it using the official instructions for the
device operating system, then authenticate the device:

```bash
sudo tailscale up
tailscale ip -4
```

Use the resulting private network address only through the approved tailnet.
Do not record the device address in this repository.

## Create the Systemd Service

Create `/etc/systemd/system/community-bot.service` with generic deployment
values:

```ini
[Unit]
Description=Community Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/opt/community-bot
EnvironmentFile=/opt/community-bot/.env
ExecStart=/opt/community-bot/.venv/bin/python /opt/community-bot/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Load and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now community-bot.service
sudo systemctl status community-bot.service --no-pager
```

The service should run as the unprivileged service user and read the private
`.env` file from the application directory. Avoid running the bot as root.

## First Validation

Check the Python installation and application imports as the service user:

```bash
sudo -u botuser /opt/community-bot/.venv/bin/python --version
sudo -u botuser /opt/community-bot/.venv/bin/python -m compileall \
  /opt/community-bot
```

Then inspect the service log for startup errors:

```bash
sudo journalctl -u community-bot.service -n 80 --no-pager
```

After startup, verify the bot from its private administrator chat. Keep any
credentials and runtime JSON contents out of screenshots and support requests.

## Security Checklist

- Use a dedicated unprivileged service account.
- Keep `.env`, Google credentials, and `data/` runtime files out of Git.
- Restrict private files to the service user where possible.
- Use Tailscale or another approved private network for administration.
- Do not expose bot tokens, user IDs, group IDs, private URLs, or logs publicly.
- Keep the operating system and Python dependencies updated through a planned
  maintenance process.
