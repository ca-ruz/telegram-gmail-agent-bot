# Group Notification Routing

Group notification categories let the bot deliver different notification types
to different Telegram groups. User DMs are controlled separately by each user's
subscription preferences.

## Categories

- `events`: calendar reminders and manual event promotions.
- `payments`: public membership payment reminders.
- `coworking`: weekly coworking invitations.

A group may belong to more than one category.

## Configuration

The assignments are stored in the runtime file configured by:

```dotenv
GROUP_NOTIFICATION_CATEGORIES_FILE=data/group_notification_categories.json
```

The file is generated automatically and must not be committed. On first start,
existing broadcast groups are assigned to `events` and `coworking`, while
`MEMBERS_GROUP_ID` is assigned to `payments`.

## Administrator Commands

Run these commands from the private administrator chat:

```text
/groupnotifications
/groupnotifications add events -100123456789
/groupnotifications remove events -100123456789
/groupnotifications add payments -100123456789
```

The list command shows stored group names, usernames when available, numeric
IDs, and every category assignment. The command also accepts `eventos` and
`pagos` as category aliases.

`/addgroup` adds a group to the legacy broadcast list and initially enables its
`events` and `coworking` categories. `/removegroup` removes those two category
assignments but does not remove a separate `payments` assignment.
