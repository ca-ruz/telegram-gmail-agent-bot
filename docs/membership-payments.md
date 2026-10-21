# Membership Payments

This guide covers the manual membership-payment workflow. It assumes the bot is
already installed and running with a valid `.env` file.

## Configuration

Set these values in the private `.env` file:

```dotenv
PAYMENT_ADMIN_ID=telegram_user_id_for_the_payment_admin
MEMBERS_FILE=data/members.json
PAYMENT_STATE_FILE=data/payment_state.json
MEMBERS_GROUP_ID=-1001234567890
PAYMENT_REMINDER_HOUR=9
ENABLE_PAYMENT_REMINDERS=true
```

`TELEGRAM_ADMIN_ID` is the primary administrator. That account and
`PAYMENT_ADMIN_ID` may manage payment records. `MEMBERS_GROUP_ID` is the one
Telegram group that receives anonymous public payment reminders. It is not the
same as the general broadcast-group list.

The reminder hour uses `America/Mexico_City`. The payment and member JSON files
are runtime data and must remain outside Git.

## Initial Setup

1. Add the bot to the membership group.
2. Give it permission to send messages in that group.
3. Find the group chat ID and set `MEMBERS_GROUP_ID` in `.env`.
4. Set `PAYMENT_ADMIN_ID` to the Telegram user ID of the payment administrator.
5. Restart the bot and run `/status` as the primary administrator.
6. Confirm that payment reminders are enabled and the membership group is configured.

If `MEMBERS_GROUP_ID` is empty or zero, public reminders are skipped. Personal
reminders can still be enabled later when the scheduler is configured.

## Managing Members

The primary administrator or payment administrator can use `/members`:

```text
/members list
/members add | Name | Telegram ID
/members update | member-id | Name | Telegram ID
/members deactivate | member-id
```

The bot assigns IDs such as `member-001`. A member must have a valid Telegram
ID to receive private reminders. Deactivating a member excludes them from new
payment reminders without deleting their historical roster record.

## Reviewing Payments

Run:

```text
/payments
```

The bot shows the current billing period and separates paid members from
pending members. Each member has a button to toggle their payment status.

Marking a member as paid records the UTC timestamp and stops future reminders
for that member. Marking them pending clears the payment timestamp and makes
them eligible for a later due stage.

## Reminder Schedule

Public reminders are sent to `MEMBERS_GROUP_ID` at the configured local hour:

- 7 days before the first day of the billing month.
- 2 days before the first day.
- 1 day before the first day.
- On the first day of the month.
- Five days after the first day for overdue payments.

Public messages do not include member names or payment details. The bot stores
each successful public stage in `payment_state.json` so scheduler restarts do
not resend it.

Private reminders begin on the first day and may be sent again at the overdue
stage. They are sent only when all of these conditions are true:

- The member is active.
- The member is unpaid for the current period.
- The member has a valid Telegram ID.
- The member's `payments` notification preference is enabled.
- That member and reminder stage have not already been delivered.

Failed deliveries are not marked as complete and can be retried by the next
scheduler run.

## Preferences and Privacy

Users control payment notifications through `/suscripciones`. Disabling the
payment category prevents personal payment DMs; it does not remove the user
from the administrator's roster or change their payment status.

The public group receives only an anonymous reminder. Do not put names,
Telegram IDs, payment amounts, or payment notes in public reminder text.

## Updating a Running Installation

From the deployed checkout, as the service account or deployment user:

```bash
git switch main
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt
sudo systemctl restart btcgdl-message-bot
sudo systemctl status btcgdl-message-bot --no-pager
sudo journalctl -u btcgdl-message-bot -n 50 --no-pager
```

Do not overwrite `.env`, `data/members.json`, or `data/payment_state.json` while
updating. Back up runtime data before intentionally changing the deployment.

## Troubleshooting

- No public reminder: verify `MEMBERS_GROUP_ID`, bot permissions, and `ENABLE_PAYMENT_REMINDERS=true`.
- No private reminder: verify the member is active, unpaid, has the correct Telegram ID, and has `payments` enabled.
- Repeated reminder after a restart: inspect `data/payment_state.json` and confirm the service user can write to the `data/` directory.
- Wrong time: check the device timezone and `PAYMENT_REMINDER_HOUR`; the schedule is evaluated in `America/Mexico_City`.
- Check logs with `journalctl`; never paste `.env` or runtime JSON contents into public issues.
