# Isolated Payment Test

This guide verifies NWC invoice creation and settlement detection with a
low-value invoice before automated invoices are enabled for real members.

The test invoice is isolated from production membership payment state. It does
not require changing a real member's plan or marking a real billing period as
unpaid.

## 1. Edit the private environment file

On the Orange Pi:

```bash
sudo -u btcgdl nano /opt/btcgdl-message-bot/.env
```

Add or update these settings:

```env
PAYMENT_VERIFICATION_ENABLED=true
PAYMENT_NWC_URI=YOUR_FULL_PRIVATE_NWC_URI

PAYMENT_TEST_INVOICE_AMOUNT_SATS=100
PAYMENT_TEST_STATE_FILE=data/payment_test_state.json
PAYMENT_INVOICE_EXPIRY_SECONDS=259200

# Prevent normal member invoices during this test.
ENABLE_PAYMENT_REMINDERS=false

# Use a shorter interval temporarily during testing.
PAYMENT_VERIFICATION_POLL_INTERVAL_SECONDS=60
```

Replace `YOUR_FULL_PRIVATE_NWC_URI` with the complete NWC URI. Never share the
URI in GitHub, Telegram groups, screenshots, logs, or support messages.

The test amount must be between 1 and 100 sats. The default is 100 sats.

## 2. Restart and inspect the service

```bash
sudo systemctl restart btcgdl-message-bot
sudo systemctl status btcgdl-message-bot --no-pager
sudo journalctl -u btcgdl-message-bot -n 50 --no-pager
```

Do not print the complete `.env` file because it contains the private NWC URI.

## 3. Create the test invoice

Open a private chat with the bot and send:

```text
/paymenttest
```

Only the primary administrator can use this command. The bot sends one message
containing the QR code, invoice details, and the full copyable invoice for the
configured test amount.

If QR generation or delivery fails, the bot sends one text fallback containing
the full invoice. It does not send a duplicate message after a successful QR
delivery.

The command does not require a membership plan and does not modify November's
real payment status.

## 4. Pay the invoice

Copy or scan the Lightning invoice using a wallet connected to the network used
by the NWC connection.

Check the current test state with:

```text
/paymenttest status
```

The status command shows the test ID, state, amount, creation time, expiration,
last verification time, settlement time, payment hash, and full invoice. It
also sends the invoice QR code when the invoice is available. If QR delivery
fails, the same details are sent as one text message.

To stop an unpaid test without deleting its record, use:

```text
/paymenttest cancel
```

To archive the current test and make room for a new one, use:

```text
/paymenttest reset
```

Both commands preserve the test history and affect only the isolated payment
test state.

With the temporary 60-second polling interval, settlement should normally be
detected within about one or two minutes after the payment is confirmed.

The bot should send a private confirmation after settlement.

The confirmation is sent only when the wallet reports an explicit settled or
paid state for the exact tracked payment hash and amount, and returns a valid
payment preimage whose SHA-256 matches that hash. A settled label by itself is
not sufficient. Missing, malformed, or mismatched lookup data remains
unmatched and does not produce a paid confirmation.

This proof requirement protects the bot from incorrectly marking an invoice as
paid when the NWC service returns an incomplete, stale, or incorrectly mapped
lookup response.

## 5. Confirm the safety boundary

The test must not:

- Change any real member's payment status.
- Change the November billing period.
- Send invoices or messages to members.
- Send messages to groups.
- Trigger normal payment reminders.

Test data is stored in `data/payment_test_state.json`. Production membership
payment data remains in `data/payment_state.json`.

## 6. Restore normal settings

After the test succeeds, edit the environment file again:

```bash
sudo -u btcgdl nano /opt/btcgdl-message-bot/.env
```

Restore normal values:

```env
ENABLE_PAYMENT_REMINDERS=true
PAYMENT_VERIFICATION_POLL_INTERVAL_SECONDS=300
```

Restart the service:

```bash
sudo systemctl restart btcgdl-message-bot
sudo systemctl status btcgdl-message-bot --no-pager
```

Keep `PAYMENT_VERIFICATION_ENABLED=true` only when the NWC connection is ready
and automated invoice creation for unpaid members is intentionally enabled.
