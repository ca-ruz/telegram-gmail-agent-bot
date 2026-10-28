# Coworking Invitations

The bot can send one weekly coworking invitation to configured broadcast
groups. The invitation content is configured through the private `.env` file so
each community can describe its own space and access options.

## Configuration

```dotenv
ENABLE_COWORKING_NOTIFICATIONS=true
COWORKING_STATE_FILE=data/coworking_state.json
COWORKING_WEEKDAY=2
COWORKING_HOUR=10
COWORKING_QUIET_WINDOW_HOURS=24
COWORKING_SPACE_NAME=Espacio de coworking de la comunidad
COWORKING_DESCRIPTION=Un espacio para trabajar, aprender y conectar con la comunidad.
COWORKING_VISIT_URL=
COWORKING_DAY_PASS_URL=
COWORKING_MEMBERSHIP_URL=
COMMUNITY_GROUP_URL=https://t.me/your_public_group_username
```

The content settings are:

- `COWORKING_SPACE_NAME`: the name shown in the invitation heading.
- `COWORKING_DESCRIPTION`: the short explanation of what visitors can do.
- `COWORKING_VISIT_URL`: optional page with visiting information.
- `COWORKING_DAY_PASS_URL`: optional page for day-pass details.
- `COWORKING_MEMBERSHIP_URL`: optional page for membership details.
- `COMMUNITY_GROUP_URL`: optional link to the community group for questions and
  additional coworking information. This shared setting is also used by event
  announcements.

Leave an optional URL empty when that information is not available. The bot
will omit that link from the invitation.

When `COMMUNITY_GROUP_URL` is configured, the invitation includes a clickable
community-group link below the coworking details. If it is empty, that link is
omitted without changing the rest of the message.

`COWORKING_WEEKDAY` uses Python weekday numbering: Monday is `0` and Sunday is
`6`. `COWORKING_HOUR` and the quiet-window check use
`America/Mexico_City`.

## Delivery Behavior

Coworking invitations are sent to every group managed through `/addgroup`.
They are group announcements, not private messages, and do not depend on a
user's coworking notification preference. The bot does not send a separate
copy to subscribers.

The scheduler sends at most one invitation per ISO week. It skips the weekly
send when another calendar event or active manual promotion is inside the
configured quiet window. If no groups are configured, the week is not marked as
sent. If delivery to any group fails, the weekly slot remains retryable on the
next scheduler check.
