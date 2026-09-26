from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_OPT_OUT_PHRASES = ("stop", "unsubscribe", "do not message", "don't message", "remove me")

_CLEAR_INTENT_PHRASES = (
    "yes",
    "go ahead",
    "send it",
    "let's do it",
    "let's go",
    "do it",
    "sure",
    "absolutely",
    "proceed",
    "ok go ahead",
    "yes please",
)

_AUTO_REPLY_PATTERNS = (
    "our team will respond shortly",
    "we will get back to you",
    "your message has been received",
    "thank you for contacting",
    "this is an automated",
    "auto-reply",
    "autoreply",
)


_merchant_auto_reply_counts: dict[str, int] = {}


def clear_conversation_counts() -> None:
    _merchant_auto_reply_counts.clear()


@dataclass
class ConversationState:
    merchant_id: str | None = None
    customer_id: str | None = None
    turns: list[dict[str, str]] = field(default_factory=list)
    sent_bodies: set[str] = field(default_factory=set)
    auto_reply_count: int = 0
    ended: bool = False


def next_reply(state: ConversationState, message: str) -> dict[str, Any]:
    """Process an incoming message and return the next action for the conversation."""
    if state.ended:
        return {
            "action": "end",
            "rationale": "Conversation has already ended and cannot be reopened.",
        }

    lowered = message.casefold()

    # Opt-out / Hostile detection
    if any(phrase in lowered for phrase in _OPT_OUT_PHRASES) or "spam" in lowered or "useless" in lowered:
        state.ended = True
        return {
            "action": "end",
            "rationale": "Merchant opted out or expressed hostile rejection; ending conversation.",
        }

    # Auto-reply / canned message detection
    if any(pattern in lowered for pattern in _AUTO_REPLY_PATTERNS):
        state.auto_reply_count += 1
        m_id = state.merchant_id or "default"
        _merchant_auto_reply_counts[m_id] = _merchant_auto_reply_counts.get(m_id, 0) + 1
        if state.auto_reply_count >= 2 or _merchant_auto_reply_counts[m_id] >= 2:
            state.ended = True
            return {
                "action": "end",
                "rationale": "Repeated canned auto-reply detected; ending conversation.",
            }
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Detected canned auto-reply; waiting 4 hours for a human response.",
        }

    # Clear intent detection
    if _is_clear_intent(lowered):
        body = "Great — here is your next concrete step. I will prepare the details now."
        if body in state.sent_bodies:
            return {
                "action": "wait",
                "wait_seconds": 900,
                "rationale": "Already sent an action response for this intent; waiting to avoid repetition.",
            }
        state.sent_bodies.add(body)
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Clear positive intent detected; providing next concrete step.",
        }

    # Default: acknowledge and continue
    return {
        "action": "wait",
        "wait_seconds": 300,
        "rationale": "Message received; waiting for further context before responding.",
    }


def _is_clear_intent(lowered: str) -> bool:
    """Check if the message expresses clear positive intent."""
    for phrase in _CLEAR_INTENT_PHRASES:
        if phrase in lowered:
            return True
    return False
