"""Membership plan definitions and normalization helpers."""

from decimal import Decimal, InvalidOperation
import logging
import re

from tools.local.data_manager import load_json

logger = logging.getLogger(__name__)

DEFAULT_MEMBERSHIP_PLANS = {
    "coworking": {
        "name": "Coworking space",
        "price_mxn": 1100,
    },
    "private_office": {
        "name": "Private office",
        "price_mxn": 5500,
    },
}


def normalize_membership_plan(plan):
    """Return a normalized plan key or an empty string when none is supplied."""
    if plan is None:
        return ""
    normalized = str(plan).strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if re.fullmatch(r"[a-z0-9_]+", normalized) else ""


def normalize_membership_quantity(quantity):
    """Return a JSON-safe positive membership quantity."""
    try:
        value = Decimal(str(quantity).strip())
    except (AttributeError, InvalidOperation, ValueError):
        raise ValueError("membership quantity must be numeric") from None

    if not value.is_finite() or value <= 0:
        raise ValueError("membership quantity must be positive")

    if value == value.to_integral_value():
        return int(value)
    return float(value)


def normalize_membership_plans(raw_plans):
    """Return valid plan definitions while ignoring malformed entries."""
    if not isinstance(raw_plans, dict):
        return {}

    plans = {}
    for raw_key, raw_plan in raw_plans.items():
        plan_key = normalize_membership_plan(raw_key)
        if not plan_key or not isinstance(raw_plan, dict):
            continue

        name = str(raw_plan.get("name", plan_key.replace("_", " "))).strip()
        if not name:
            continue
        try:
            price_mxn = normalize_membership_quantity(raw_plan.get("price_mxn"))
        except ValueError:
            logger.warning("Ignoring membership plan %s with invalid MXN price.", raw_key)
            continue
        plans[plan_key] = {"name": name, "price_mxn": price_mxn}
    return plans


def load_membership_plans(file_path):
    """Load configured membership plans, falling back to built-in generic plans."""
    raw_plans = load_json(file_path, DEFAULT_MEMBERSHIP_PLANS)
    plans = normalize_membership_plans(raw_plans)
    return plans or normalize_membership_plans(DEFAULT_MEMBERSHIP_PLANS)


def format_membership_plan(member, plans):
    """Return a readable plan and quantity label for a member record."""
    plan_key = normalize_membership_plan(member.get("membership_plan"))
    if not plan_key:
        return "Sin plan"

    plan = plans.get(plan_key, {}) if isinstance(plans, dict) else {}
    plan_name = str(plan.get("name", plan_key.replace("_", " ")))
    try:
        quantity = normalize_membership_quantity(member.get("membership_quantity", 1))
    except ValueError:
        quantity = 1
    if isinstance(quantity, float):
        quantity_label = f"{quantity:g}"
    else:
        quantity_label = str(quantity)
    return f"{plan_name} x{quantity_label}"
