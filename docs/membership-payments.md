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
PAYMENT_ADVANCE_REMINDER_DAYS=3
PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS=2
ENABLE_PAYMENT_REMINDERS=true
```

`TELEGRAM_ADMIN_ID` is the primary administrator. That account and
`PAYMENT_ADMIN_ID` may manage payment records. `MEMBERS_GROUP_ID` is the one
Telegram group that receives anonymous public payment reminders. It is not the
same as the general broadcast-group list.

The reminder hour uses `America/Mexico_City`. The payment and member JSON files
are runtime data and must remain outside Git.

The advance reminder lead time defaults to three days. No `.env` change is
required when using that default; set `PAYMENT_ADVANCE_REMINDER_DAYS` only if
the lead time needs to be different. The five-day grace period and public
reminder frequency are built into the scheduler.

After each successful public reminder, the bot waits the configured number of
hours before privately notifying `PAYMENT_ADMIN_ID`. The follow-up includes a
`Revisar pagos` button that opens the authorized review screen for the same
billing period. Set the delay to `0` to send the follow-up during the same
scheduler check as the public reminder.

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

The bot shows the available billing periods and separates paid members from
pending members for the selected period. Each member has a button to toggle
their payment status. A follow-up button opens the period that caused that
follow-up, even if a newer period is already active.

Marking a member as paid records the UTC timestamp and stops future reminders
for that member. Marking them pending clears the payment timestamp and makes
them eligible for a later due stage.

## Reminder Schedule

All reminders are evaluated at the configured local hour in
`America/Mexico_City`:

- Three days before the first day of a billing period, send one private
  reminder to each unpaid member and one anonymous reminder to the configured
  payment groups.
- On the first day of the month, send a private reminder for the new period and
  an anonymous reminder to the configured payment groups. If the previous
  period is still unpaid, include it in the same combined reminder.
- From the second through the fifth day, send a private reminder each day for
  unpaid current and previous periods. No additional public reminder is sent
  during this grace period.
- On day six, send one public reminder for the first week.
- During the second week, send public reminders on days eight and eleven.
- During the third week, send public reminders on days fifteen, seventeen, and
  nineteen.
- During the final week, send public reminders on days twenty-two, twenty-four,
  twenty-six, and twenty-eight.

Private reminders continue daily for active unpaid members after the grace
period. A member who is marked paid is excluded from later reminders for that
period. If the same member has more than one unpaid period, the bot combines
those periods into one private message.

Public messages do not include member names or payment details. The bot stores
each successful public reminder in `payment_state.json` so scheduler restarts
do not resend it. Daily private reminders are also tracked by date, so a
reminder is sent at most once per member and period per day. When several
public period reminders are due at the same scheduler run, the bot combines
them into one anonymous group message while preserving separate period state.

The payment administrator follow-up is sent only after the corresponding
public reminder succeeds. Each public stage produces at most one follow-up;
failed deliveries remain eligible for a later scheduler retry. Follow-ups are
private and never sent to the membership group or regular subscribers.

Private reminders for each period are sent only when all of these conditions
are true:

- The member is active.
- The member is unpaid for the current period.
- The period is current, upcoming within the advance window, or a previous
  period with an unpaid balance.
- The member has a valid Telegram ID.
- The member's `payments` notification preference is enabled.
- That member has not already received the reminder for the current date.

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
The bot migrates legacy single-period payment state automatically and keeps the
current and next billing periods available after startup.

## Troubleshooting

- No public reminder: verify `MEMBERS_GROUP_ID`, bot permissions, and `ENABLE_PAYMENT_REMINDERS=true`.
- No private reminder: verify the member is active, unpaid, has the correct Telegram ID, and has `payments` enabled.
- No payment-admin follow-up: verify `PAYMENT_ADMIN_ID`,
  `PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS`, and that the public reminder was
  delivered successfully.
- Repeated reminder after a restart: inspect `data/payment_state.json` and confirm the service user can write to the `data/` directory.
- Wrong time: check the device timezone, `PAYMENT_REMINDER_HOUR`, and
  `PAYMENT_ADVANCE_REMINDER_DAYS`; the schedule is evaluated in
  `America/Mexico_City`.
- Check logs with `journalctl`; never paste `.env` or runtime JSON contents into public issues.
