from __future__ import annotations

import time
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from context_store import VALID_SCOPES, store
from composer import compose, template_details
from conversation import ConversationState, next_reply, clear_conversation_counts
from validation import fallback_action, validate_action, validate_reply

load_dotenv()

STARTED_AT = time.time()
conversations: dict[str, ConversationState] = {}
sent_suppression_keys: set[str] = set()
opted_out_merchants: set[str] = set()

app = FastAPI(title="Vera Engagement Bot", version="0.1.0")


class ContextRequest(BaseModel):
    scope: str
    context_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    payload: dict[str, Any]
    delivered_at: str


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int = Field(ge=1)


@app.get("/v1/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - STARTED_AT),
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    bot_model = os.environ.get("BOT_LLM_MODEL") if os.environ.get("BOT_LLM_PROVIDER") else "deterministic-composer-v0"
    return {
        "team_name": "Local Vera Bot",
        "team_members": [],
        "model": bot_model or "deterministic-composer-v0",
        "approach": "optional grounded LLM wording with deterministic fallback",
        "contact_email": "",
        "version": "0.1.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/v1/context")
def push_context(body: ContextRequest) -> Any:
    if body.scope not in VALID_SCOPES:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": body.scope},
        )

    accepted, current_version = store.put(
        body.scope, body.context_id, body.version, body.payload
    )
    if not accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )

    stored_at = datetime.now(timezone.utc).isoformat()
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": stored_at,
    }


from concurrent.futures import ThreadPoolExecutor

@app.post("/v1/tick")
def tick(body: TickRequest) -> dict[str, Any]:
    valid_tasks = []
    for trigger_id in body.available_triggers:
        trigger = store.get("trigger", trigger_id)
        if not trigger:
            continue
        if _is_expired(trigger.get("expires_at"), body.now):
            continue

        suppression_key = trigger.get("suppression_key") or trigger_id
        if suppression_key in sent_suppression_keys:
            continue

        merchant_id = trigger.get("merchant_id")
        if merchant_id in opted_out_merchants:
            continue
        merchant = store.get("merchant", merchant_id)
        if not merchant:
            continue

        category = store.get("category", merchant.get("category_slug"))
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = store.get("customer", customer_id) if customer_id else None
        if trigger.get("scope") == "customer" and not customer:
            continue
        if customer and not _customer_can_be_contacted(customer, trigger.get("kind", "")):
            continue

        conversation_id = f"conv_{merchant_id}_{trigger_id}"
        valid_tasks.append((trigger_id, suppression_key, merchant_id, customer_id, conversation_id, category, merchant, trigger, customer))

    def _process_one(task):
        trigger_id, suppression_key, merchant_id, customer_id, conversation_id, category, merchant, trigger, customer = task
        conversations.setdefault(
            conversation_id,
            ConversationState(merchant_id=merchant_id, customer_id=customer_id),
        )
        message = compose(category, merchant, trigger, customer)
        template_name, template_params = template_details(category, merchant, trigger, customer)
        action = {
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": message["send_as"],
            "trigger_id": trigger_id,
            "template_name": template_name,
            "template_params": template_params,
            **message,
        }
        state = conversations[conversation_id]
        valid, _ = validate_action(action, category, state.sent_bodies)
        if not valid:
            action = fallback_action(merchant, trigger, customer_id, conversation_id)
            fallback_valid, _ = validate_action(action, category, state.sent_bodies)
            if not fallback_valid:
                return None, None
        return action, suppression_key

    actions: list[dict[str, Any]] = []
    if valid_tasks:
        with ThreadPoolExecutor(max_workers=min(10, len(valid_tasks))) as executor:
            results = executor.map(_process_one, valid_tasks)
            for action, suppression_key in results:
                if action and suppression_key:
                    state = conversations[action["conversation_id"]]
                    state.sent_bodies.add(action["body"])
                    sent_suppression_keys.add(suppression_key)
                    actions.append(action)
                    if len(actions) >= 20:
                        break

    return {"actions": actions}


@app.post("/v1/reply")
def reply(body: ReplyRequest) -> dict[str, Any]:
    state = conversations.setdefault(
        body.conversation_id,
        ConversationState(merchant_id=body.merchant_id, customer_id=body.customer_id),
    )
    result = next_reply(state, body.message)
    if result.get("action") == "end" and _is_opt_out_message(body.message) and body.merchant_id:
        opted_out_merchants.add(body.merchant_id)
    valid, _ = validate_reply(result)
    if not valid:
        return {
            "action": "wait",
            "wait_seconds": 300,
            "rationale": "The reply was replaced with a safe wait because it failed response validation.",
        }
    return result


@app.post("/v1/teardown")
def teardown() -> dict[str, bool]:
    store.clear()
    conversations.clear()
    sent_suppression_keys.clear()
    opted_out_merchants.clear()
    clear_conversation_counts()
    return {"cleared": True}


def _customer_can_be_contacted(customer: dict[str, Any], trigger_kind: str) -> bool:
    preferences = customer.get("preferences") or {}
    if preferences.get("reminder_opt_in") is False:
        return False
    consent = customer.get("consent") or {}
    scopes = set(consent.get("scope") or [])
    if not scopes:
        return False
    allowed_scopes = {
        "recall_due": {"recall_reminders"},
        "appointment_tomorrow": {"appointment_reminders"},
        "trial_followup": {"program_updates", "appointment_reminders"},
        "wedding_package_followup": {"appointment_reminders", "bridal_package_followup"},
        "chronic_refill_due": {"refill_reminders"},
        "customer_lapsed_soft": {"promotional_offers", "recall_reminders"},
        "customer_lapsed_hard": {"winback_offers", "promotional_offers"},
    }
    required = allowed_scopes.get(trigger_kind)
    return not required or bool(scopes & required)


def _is_opt_out_message(message: str) -> bool:
    lowered = message.casefold()
    return any(phrase in lowered for phrase in ("stop", "unsubscribe", "do not message", "don't message", "remove me"))


def _is_expired(expires_at: str | None, now: str) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= expiry
    except ValueError:
        return False
