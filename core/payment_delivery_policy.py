"""Fail-closed delivery policy for membership payment reminders."""

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)
PAYMENT_DELIVERY_MODES = frozenset({"off", "pilot", "full"})
MEMBER_ID_PATTERN = re.compile(r"member-\d+")


@dataclass(frozen=True)
class PaymentDeliveryPolicy:
    """Describe which members may receive scheduled payment messages."""

    mode: str = "off"
    member_ids: frozenset[str] = frozenset()

    def allows_member(self, member_id):
        """Return whether delivery is permitted for the member ID."""
        return self.mode == "full" or (
            self.mode == "pilot" and str(member_id) in self.member_ids
        )

    @property
    def allows_broadcasts(self):
        """Permit payment-group and payment-admin messages only in full mode."""
        return self.mode == "full"


def resolve_payment_delivery_policy(config, members):
    """Validate operator settings and return an off policy on any ambiguity."""
    if not isinstance(config, dict) or not isinstance(members, dict):
        logger.warning("Payment delivery is off: configuration or roster is invalid.")
        return PaymentDeliveryPolicy()

    raw_mode = config.get("PAYMENT_DELIVERY_MODE", "off")
    raw_member_ids = config.get("PAYMENT_PILOT_MEMBER_IDS", "")
    if not isinstance(raw_mode, str) or not isinstance(raw_member_ids, str):
        logger.warning("Payment delivery is off: invalid delivery settings.")
        return PaymentDeliveryPolicy()

    mode = raw_mode.strip().lower()
    if mode not in PAYMENT_DELIVERY_MODES:
        logger.warning("Payment delivery is off: unknown delivery mode.")
        return PaymentDeliveryPolicy()
    if mode == "off":
        return PaymentDeliveryPolicy()
    if mode == "full":
        if raw_member_ids.strip():
            logger.warning("Payment delivery is off: clear the pilot allowlist before full mode.")
            return PaymentDeliveryPolicy()
        return PaymentDeliveryPolicy(mode="full")

    member_ids = [member_id.strip() for member_id in raw_member_ids.split(",")]
    if (
        len(member_ids) != 1
        or MEMBER_ID_PATTERN.fullmatch(member_ids[0]) is None
        or member_ids[0] not in members
    ):
        logger.warning("Payment delivery is off: pilot requires one existing member ID.")
        return PaymentDeliveryPolicy()
    return PaymentDeliveryPolicy(mode="pilot", member_ids=frozenset(member_ids))
