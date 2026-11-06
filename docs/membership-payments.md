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
PAYMENT_VERIFICATION_ENABLED=false
PAYMENT_NWC_URI=
PAYMENT_INVOICE_AMOUNT_MSAT=0
PAYMENT_INVOICE_EXPIRY_SECONDS=604800
PAYMENT_VERIFICATION_POLL_INTERVAL_SECONDS=300
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

Automated invoice verification is disabled by default. To enable it, set
`PAYMENT_VERIFICATION_ENABLED=true`, provide the dedicated `PAYMENT_NWC_URI`,
and set a positive `PAYMENT_INVOICE_AMOUNT_MSAT`. The bot creates or reuses one
invoice per unpaid member and billing period in private reminders. A separate
verification job checks pending invoices using
`PAYMENT_VERIFICATION_POLL_INTERVAL_SECONDS`; it marks the matching period as
paid only after NWC reports that exact invoice as settled. Wallet errors,
expired invoices, and hash mismatches do not mark a member paid, and the manual
`/payments` workflow remains available.

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

## Automated Payment Verification

Issue #16 implements optional payment automation through a receive-only Nostr
Wallet Connect (NWC) connection to a Core Lightning node, using the
[Daywalker cln-nip47 plugin](https://github.com/daywalker90/cln-nip47).

### Payment administrator setup

The payment administrator does not need to install Alby Hub for this design.
Alby's NWC relay is a relay service that can carry NIP-47 messages between the
Core Lightning plugin and the bot. The current public relay endpoint is
`wss://relay.getalby.com/v1`. Alby documents that the relay is intended for
NWC wallet services and does not require relay authentication. See the
[Alby NWC Relay guide](https://guides.getalby.com/developer-guide/developer-guide/nostr-wallet-connect-api/building-nwc-wallet-services/alby-nwc-relay).

The node operator should:

1. Confirm that the node is running Core Lightning on the intended network and
   has enough inbound liquidity to receive the membership payments. See the
   [Core Lightning receiving-payments FAQ](https://docs.corelightning.org/docs/faq).
2. Install a release of `cln-nip47` that matches the node's architecture. The
   plugin publishes binaries for common Linux architectures and can also be
   built from source.
3. Configure the plugin's `nip47-relays` option with
   `wss://relay.getalby.com/v1`, or use another compatible relay if the
   deployment later requires a private one.
4. Start the plugin through Core Lightning and confirm it is active with the
   Core Lightning `plugin list` command. See the [Core Lightning plugin
   reference](https://docs.corelightning.org/reference/plugin).
5. Create a dedicated connection for the bot with a descriptive label and a
   zero spending budget:

   ```text
   lightning-cli nip47-create membership-bot 0
   ```

   Do not set an interval when creating a receive-only connection. The plugin
   documents `budget_msat=0` as disabling outgoing payments for that connection.
6. Transfer the generated NWC connection URI to the bot operator through a
   secure channel. Treat it like a wallet credential: never paste it into an
   issue, chat, log, screenshot, or committed file.
7. Keep the connection dedicated to this bot. The plugin notes that NWC
   notifications are delivered to all NWC connections, so unrelated clients
   should not share this connection.

After configuring the `.env` values above, restart the service and verify the
configuration with `/status`. Start with a small test amount and confirm that
the private reminder contains an invoice, the invoice is paid from another
Lightning wallet, and the matching member-period changes to paid after the next
verification poll. Keep the feature disabled until the node operator and
primary administrator approve the test.

The bot will use the connection for invoice creation and invoice lookup. It
does not need the node's IP address, CLN RPC port, port forwarding, or direct
RPC credentials. Both the node and bot connect to the relay, while the plugin
uses the local CLN RPC interface on the node.

The Daywalker plugin documents budget control and supported methods, but does
not document a per-connection method allowlist in `nip47-create`. Therefore,
`budget_msat=0` is the required spending safeguard; the bot must only call the
receive-side methods it needs and the connection URI must remain secret. If a
strict method allowlist is required later, that should be evaluated separately
or enforced by a dedicated proxy.

The public Alby relay is suitable for initial testing, but it is rate-limited
and operated by a third party. A private relay can be introduced later for
stronger operational independence. The relay transports encrypted NWC
messages, but the bot must still reconcile invoice status with `lookup_invoice`
after restarts or missed notifications.

### Recommended flow

1. Create one unique invoice for each active member and billing period.
2. Send the invoice to the member by private message.
3. Persist the invoice identifier, payment hash, member ID, billing period,
   amount, creation time, expiration time, and current status.
4. Detect settlement through the NWC `payment_received` notification when
   available, then confirm the invoice with `lookup_invoice`.
5. Mark the matching member and period as paid only after the node confirms the
   invoice is settled.
6. Reconcile unpaid and pending invoices after restarts so a missed
   notification cannot leave a paid member marked as pending.

The NWC connection should be scoped to `make_invoice` and `lookup_invoice`,
with `get_info` and payment notifications enabled only if needed. It must not
grant payment or other spending methods. A receive-only connection with
`budget_msat=0` is the preferred setup. The NWC URI is a wallet credential and
must remain only in the private `.env`; it must never be committed, logged, or
included in tests.

The [NIP-47 specification](https://github.com/nostr-protocol/nips/blob/master/47.md)
defines the scoped wallet methods and invoice lookup states. Core Lightning's
[`listinvoices` RPC](https://docs.corelightning.org/reference/listinvoices)
also exposes invoice status, payment hash, expiration, and settlement data for
reconciliation.

### BOLT12 and zap receipts

BOLT12 offers are reusable payment requests, but they are not required for the
first automated membership workflow. They should be considered later, after the
unique-invoice flow is stable and the desired recurring-payment behavior is
clear. See the [BOLT12 specification](https://github.com/lightning/bolts/blob/master/12-offer-encoding.md).

NIP-57 zap receipts are a separate integration. A generic invoice created by
NWC does not automatically produce a zap receipt; zap receipts require the
LNURL/NIP-57 flow and a signed zap request embedded in the invoice metadata.
See the [NIP-57 specification](https://github.com/nostr-protocol/nips/blob/master/57.md).

### Security and rollout requirements

- Use a dedicated receive-only NWC connection and, where practical, a private
  relay. The plugin documents that notifications are delivered to all NWC
  connections, so unrelated connections should not be used for this workflow.
- Keep manual payment controls available as a fallback during rollout.
- Test unpaid, settled, expired, duplicate, late, wrong-amount, unmatched, and
  restart-reconciliation cases with mocked NWC responses.
- Perform a small real test payment before enabling automated membership billing.
- Never mark an invoice paid from a screenshot, user-provided preimage, or
  other unverified client input.
