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
COWORKING_LOCATION=Ubicación por confirmar
COWORKING_DESCRIPTION=Un espacio para trabajar, aprender y conectar con la comunidad.
COWORKING_VISIT_URL=
COWORKING_DAY_PASS_URL=
COWORKING_MEMBERSHIP_URL=
```

The content settings are:

- `COWORKING_SPACE_NAME`: the name shown in the invitation heading.
- `COWORKING_LOCATION`: where people can visit the space.
- `COWORKING_DESCRIPTION`: the short explanation of what visitors can do.
- `COWORKING_VISIT_URL`: optional page with visiting information.
- `COWORKING_DAY_PASS_URL`: optional page for day-pass details.
- `COWORKING_MEMBERSHIP_URL`: optional page for membership details.

Leave an optional URL empty when that information is not available. The bot
will omit that link from the invitation.

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
