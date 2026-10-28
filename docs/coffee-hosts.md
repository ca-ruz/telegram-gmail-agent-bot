# Coffee Host Management

The administrator can manage the coffee-host roster and Saturday assignments
with `/coffeehost`. Assignments are stored using stable internal host IDs, but
the commands accept readable host names as well.

## Commands

```text
/coffeehost
/coffeehost add | Name | Telegram ID
/coffeehost remove | Name or host-id
/coffeehost YYYY-MM-DD Name 1,Name 2 | Venue | 11:00 | Notes
/coffeehost YYYY-MM-DD clear
```

Host names are matched case-insensitively. Existing host IDs, Telegram IDs, and
usernames recorded on a host are also accepted. When two active hosts share a
name, use the host ID shown in `/coffeehost` to disambiguate the assignment.

The `add` command creates a stable host ID automatically. Removing a host
deactivates the roster record so historical assignments remain readable.
