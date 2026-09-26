from __future__ import annotations

from typing import Any

VALID_CTAS = {"open_ended", "binary_yes_no", "none"}
VALID_SEND_AS = {"vera", "merchant_on_behalf"}
VALID_REPLY_ACTIONS = {"send", "wait", "end"}


def validate_action(
    action: dict[str, Any],
    category: dict[str, Any],
    sent_bodies: set[str] | None = None,
) -> tuple[bool, str]:
    required = {
        "conversation_id",
        "merchant_id",
        "send_as",
        "trigger_id",
        "template_name",
        "template_params",
        "body",
        "cta",
        "suppression_key",
        "rationale",
    }
    missing = sorted(field for field in required if field not in action)
    if missing:
        return False, f"missing fields: {', '.join(missing)}"
    if not isinstance(action["body"], str) or not action["body"].strip():
        return False, "body must be non-empty"
    if action["send_as"] not in VALID_SEND_AS:
        return False, "invalid send_as"
    if action["cta"] not in VALID_CTAS:
        return False, "invalid cta"
    if not isinstance(action["template_params"], list):
        return False, "template_params must be a list"
    if not isinstance(action["rationale"], str) or not action["rationale"].strip():
        return False, "rationale must be non-empty"
    if sent_bodies is not None and action["body"] in sent_bodies:
        return False, "duplicate body"

    taboo = (category.get("voice") or {}).get("vocab_taboo") or (category.get("voice") or {}).get("taboos") or []
    body_lower = action["body"].casefold()
    for phrase in taboo:
        if str(phrase).casefold() in body_lower:
            return False, f"taboo phrase: {phrase}"
    return True, ""


def validate_reply(reply: dict[str, Any]) -> tuple[bool, str]:
    action = reply.get("action")
    if action not in VALID_REPLY_ACTIONS:
        return False, "invalid action"
    if action == "send":
        if not isinstance(reply.get("body"), str) or not reply["body"].strip():
            return False, "send body must be non-empty"
        if reply.get("cta") not in VALID_CTAS:
            return False, "send cta is invalid"
    if action == "wait":
        wait_seconds = reply.get("wait_seconds")
        if not isinstance(wait_seconds, int) or wait_seconds <= 0:
            return False, "wait_seconds must be positive"
    if not isinstance(reply.get("rationale"), str) or not reply["rationale"].strip():
        return False, "rationale must be non-empty"
    return True, ""


def fallback_action(
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer_id: str | None,
    conversation_id: str,
) -> dict[str, Any]:
    identity = merchant.get("identity") or {}
    name = identity.get("owner_first_name") or identity.get("name") or "there"
    kind = str(trigger.get("kind") or "engagement").replace("_", " ")
    send_as = "merchant_on_behalf" if customer_id else "vera"
    return {
        "conversation_id": conversation_id,
        "merchant_id": merchant.get("merchant_id", ""),
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_id": trigger.get("id", ""),
        "template_name": "vera_safe_fallback_v1",
        "template_params": [name, kind],
        "body": f"Hi {name}, I have a {kind} update for you. Would you like the relevant next step?",
        "cta": "open_ended",
        "suppression_key": trigger.get("suppression_key") or trigger.get("id", ""),
        "rationale": "Used a minimal grounded fallback because the composed action failed validation.",
    }
