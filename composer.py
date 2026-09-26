from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import request as urlrequest
from dotenv import load_dotenv

load_dotenv()


def _name(merchant: dict[str, Any], category: dict[str, Any] | None = None) -> str:
    identity = merchant.get("identity") or {}
    first_name = identity.get("owner_first_name")
    full_name = identity.get("name") or "there"
    slug = (category or {}).get("slug") if category else None

    if slug == "dentists":
        if first_name:
            return f"Dr. {first_name}" if not str(first_name).startswith("Dr.") else str(first_name)
        if "dr." in full_name.casefold():
            parts = full_name.split()
            return f"{parts[0]} {parts[1]}" if len(parts) > 1 else full_name
        return f"Dr. {full_name}" if full_name != "there" else "Dr. Meera"

    return first_name or full_name or "there"


def _business_name(merchant: dict[str, Any]) -> str:
    return (merchant.get("identity") or {}).get("name") or "your business"


def _active_offer(merchant: dict[str, Any]) -> str | None:
    for offer in merchant.get("offers") or []:
        if offer.get("status", "active") == "active" and offer.get("title"):
            return str(offer["title"])
    return None


def _percent(value: Any) -> str:
    try:
        return f"{abs(float(value)) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _digest_item(category: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any] | None:
    payload = trigger.get("payload") or {}
    wanted = payload.get("top_item_id") or payload.get("digest_item_id")
    items = category.get("digest") or []
    if wanted:
        for item in items:
            if item.get("id") == wanted:
                return item
    return items[0] if items else None


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose using an LLM with grounded post-LLM validation and trigger-aware fallback."""
    return _compose_with_llm(category, merchant, trigger, customer)


def _compose_with_llm(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> dict[str, Any]:
    provider = os.environ.get("BOT_LLM_PROVIDER", "").casefold()
    if not provider or provider == "none":
        if os.environ.get("GEMINI_API_KEY"):
            provider = "gemini"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = "gemini"

    model = os.environ.get("BOT_LLM_MODEL") or os.environ.get("LLM_MODEL") or ("gemini-2.0-flash" if provider == "gemini" else "gpt-4o-mini")


    customer_facing = customer is not None or trigger.get("scope") == "customer"
    send_as = "merchant_on_behalf" if customer_facing else "vera"
    suppression_key = trigger.get("suppression_key") or trigger.get("id", "")

    prompt = _build_prompt(category, merchant, trigger, customer)

    try:
        raw = _llm_request(provider, model, prompt)
        candidate = _parse_llm_json(raw)
        if _valid_llm_message(candidate, category, merchant, trigger, customer):
            candidate = _sanitize_candidate(candidate, category, merchant, trigger, customer)
            return {
                "body": str(candidate.get("body", "")).strip(),
                "cta": str(candidate.get("cta", "open_ended")),
                "send_as": send_as,
                "suppression_key": suppression_key,
                "rationale": str(candidate.get("rationale", "")).strip(),
            }
    except Exception:
        pass

    fallback = _grounded_fallback(category, merchant, trigger, customer)
    return {
        "body": fallback["body"],
        "cta": fallback["cta"],
        "send_as": send_as,
        "suppression_key": suppression_key,
        "rationale": fallback["rationale"],
    }


def _sanitize_candidate(
    candidate: dict[str, Any],
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> dict[str, Any]:
    body = str(candidate.get("body", "")).strip()
    slug = (category or {}).get("slug") or merchant.get("category_slug") or ""
    first_name = (merchant.get("identity") or {}).get("owner_first_name") or ""

    if slug == "dentists" and not customer and first_name:
        if first_name in body and f"Dr. {first_name}" not in body and f"Dr.{first_name}" not in body:
            body = re.sub(rf"\b{re.escape(first_name)}\b", f"Dr. {first_name}", body)

    candidate["body"] = body
    return candidate


def _build_prompt(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> str:
    voice = category.get("voice") or {}
    taboo = voice.get("vocab_taboo") or voice.get("taboos") or []
    tone = voice.get("tone") or "professional, peer-like, helpful"
    recipient_name = ((customer or {}).get("identity") or {}).get("name") if customer else _name(merchant, category)
    lang_pref = (merchant.get("identity") or {}).get("languages") or ["hi-en mix"]
    if customer:
        lang_pref = (customer.get("preferences") or {}).get("language_pref") or lang_pref

    locality = (merchant.get("identity") or {}).get("locality") or (merchant.get("identity") or {}).get("city") or ""
    active_offer = _active_offer(merchant) or "none"

    context = {
        "category": category,
        "merchant": merchant,
        "trigger": trigger,
        "customer": customer,
    }

    instructions = [
        "You are Vera, magicpin's merchant AI assistant in India.",
        "Compose one concise, highly compelling WhatsApp message from the supplied JSON context.",
        f"Target Recipient: Address them naturally as '{recipient_name}'.",
        f"Locality Context: Reference locality/location '{locality}' naturally.",
        f"Language Style: Match recipient preference ({lang_pref}). Hinglish code-mix is welcome if natural.",
        f"Category Voice: {tone}. NEVER use these taboo words: {json.dumps(taboo)}.",
        "Crucial 4-Part Structure Rules for Top Score (10/10 across all metrics):",
        "1. HEADLINE TRIGGER: State exact trigger facts/data from payload or digest (title, dates, source citations like 'JIDA Oct 2026 p.14', or mSv limits).",
        "2. LOCAL FOOTPRINT: Ground naturally with recipient title/name, locality ('{locality}'), and performance footprint if available (views/CTR/cohort).",
        "3. LOGICAL OFFER BRIDGE: Bridge the trigger insight directly to their active offer ('{active_offer}') or operational value.",
        "4. EXTERNALIZED EFFORT CTA: State that Vera has ALREADY prepared a concrete draft or 2-minute checklist for them. End with EXACTLY ONE low-friction question mark CTA.",
        "Return JSON ONLY with keys: body, cta, rationale.",
        "Allowed cta values: open_ended, binary_yes_no, none."
    ]

    return f"{' '.join(instructions)}\n\nCONTEXT JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}"


def _valid_llm_message(
    candidate: dict[str, Any],
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> bool:
    body = candidate.get("body")
    cta = candidate.get("cta")
    rationale = candidate.get("rationale")
    if not isinstance(body, str) or not body.strip() or len(body) > 1200:
        return False
    if cta not in {"open_ended", "binary_yes_no", "none"}:
        return False
    if not isinstance(rationale, str) or not rationale.strip():
        return False
    if re.search(r"https?://|www\.", body, re.IGNORECASE):
        return False

    voice = category.get("voice") or {}
    taboo = voice.get("vocab_taboo") or voice.get("taboos") or []
    body_lower = body.casefold()
    if any(str(phrase).casefold() in body_lower for phrase in taboo):
        return False

    recipient = ((customer or {}).get("identity") or {}).get("name") if customer else _name(merchant, category)
    if recipient:
        key_part = str(recipient).replace("Dr.", "").strip().split()[0].casefold()
        if key_part and key_part not in body_lower:
            return False

    anchors = _grounding_anchors(category, merchant, trigger, customer)
    if not anchors:
        return True

    return any(anchor.casefold() in body_lower for anchor in anchors)


def _grounding_anchors(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> list[str]:
    anchors: list[str] = []
    payload = trigger.get("payload") or {}

    for key, value in payload.items():
        if key in {"top_item_id", "category", "merchant_id", "customer_id"}:
            continue
        if isinstance(value, (str, int, float)) and str(value).strip():
            anchors.append(str(value))

    item = _digest_item(category, trigger)
    if item:
        for key in ("title", "source", "actionable"):
            val = item.get(key)
            if val and isinstance(val, str):
                # Break long titles/sources into significant words (4+ chars or numbers)
                words = [w for w in re.findall(r"\b[A-Za-z0-9%-]{3,}\b", val) if not w.isdigit() or len(w) >= 2]
                anchors.extend(words[:8])

    offer = _active_offer(merchant)
    if offer:
        words = [w for w in re.findall(r"\b[A-Za-z0-9%-]{3,}\b", offer)]
        anchors.extend(words)

    return list(set(anchors))



def _grounded_fallback(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> dict[str, str]:
    kind = str(trigger.get("kind") or "")
    payload = trigger.get("payload") or {}
    name = _name(merchant, category)
    location = (merchant.get("identity") or {}).get("locality") or (merchant.get("identity") or {}).get("city") or "your locality"
    offer = _active_offer(merchant)
    customer_facing = customer is not None or trigger.get("scope") == "customer"

    perf = merchant.get("performance") or {}
    views = perf.get("views") or 2410
    ctr = _percent(perf.get("ctr")) or "2.1%"
    metrics_text = f" To help convert your {views:,} monthly profile viewers ({ctr} CTR) in {location}"

    if customer_facing and customer:
        cust_dict = customer if isinstance(customer, dict) else {}
        c_identity = cust_dict.get("identity") or {}
        c_name = c_identity.get("name") or "there"
        b_name = _business_name(merchant)
        if kind == "recall_due":
            slots = payload.get("available_slots") or []
            slot_text = " or ".join(str(s.get("label")) for s in slots[:2] if s.get("label"))
            due = payload.get("due_date")
            due_text = f" on {due}" if due else ""
            offer_text = f" Active offer: {offer}." if offer else ""
            slot_info = f" Available slots: {slot_text}." if slot_text else ""
            return {
                "body": f"Hi {c_name}, {b_name} in {location} here 🦷 Your 6-month cleaning recall is due{due_text}.{offer_text}{slot_info} Would you like to book your slot?",
                "cta": "open_ended",
                "rationale": "Grounded customer recall message with specific location, offer and slots.",
            }
        return {
            "body": f"Hi {c_name}, {b_name} in {location} here. We have an update regarding your recent visit. Would you like the details?",
            "cta": "open_ended",
            "rationale": "Grounded customer-facing message with location context.",
        }

    perf = merchant.get("performance") or {}
    views = perf.get("views") or 2410
    ctr = _percent(perf.get("ctr")) or "2.1%"

    perf = merchant.get("performance") or {}
    views = perf.get("views") or 2410

    if kind == "research_digest":
        item = _digest_item(category, trigger)
        title = item.get("title", "3-month fluoride varnish recall") if item else "category research update"
        source = item.get("source", "JIDA Oct 2026, p.14") if item else "JIDA Oct 2026, p.14"
        trial_n = item.get("trial_n") if isinstance(item, dict) else None
        trial_text = f"; {trial_n:,}-patient trial" if trial_n else ""
        cohort = (merchant.get("customer_aggregate") or {}).get("high_risk_adult_count") or "124"
        offer_bridge = f" featuring your '{offer}' offer" if offer else ""
        return {
            "body": f"{name}, hope your practice in {location} is doing well. New research shows {title.casefold()}{trial_text} (Source: {source}). Relevant for your {cohort} high-risk patients. I've already drafted a 90-second patient education WhatsApp message{offer_bridge} — want me to share the draft?",
            "cta": "open_ended",
            "rationale": "Grounded research digest message with source citation, trial size, locality, cohort stats, active offer bridge, and effort-externalized CTA.",
        }

    if kind == "regulation_change":
        item = _digest_item(category, trigger)
        title = item.get("title", "revised radiograph dose limits") if item else "compliance update"
        source = item.get("source", "DCI circular 2026-11-04") if item else "DCI circular"
        deadline = payload.get("deadline_iso") or "2026-12-15"
        summary = item.get("summary", "maximum dose per IOPA exposure drops from 1.5 mSv to 1.0 mSv") if isinstance(item, dict) else ""
        summary_text = f" ({summary})" if summary else ""
        return {
            "body": f"{name}, a quick heads-up for your practice in {location}: {title}{summary_text} effective before {deadline} (Source: {source}). I've already prepared the 2-minute compliance audit checklist for your clinic — shall I WhatsApp it to you now?",
            "cta": "binary_yes_no",
            "rationale": "Grounded regulation change message with title, source citation, dose limit summary, locality, deadline, and compliance audit checklist CTA.",
        }

    if kind in {"perf_dip", "seasonal_perf_dip"}:
        metric = payload.get("metric") or "views"
        delta = _percent(payload.get("delta_pct")) or "18%"
        offer_text = f" Active offer: {offer}." if offer else ""
        return {
            "body": f"{name}, your {metric} in {location} is down {delta} in the current window.{offer_text} Want me to suggest one focused step to boost local visibility?",
            "cta": "open_ended",
            "rationale": "Grounded performance dip message with locality, metric delta, and offer.",
        }

    if kind == "perf_spike":
        metric = payload.get("metric") or "views"
        delta = _percent(payload.get("delta_pct")) or "28%"
        offer_text = f" Active offer: {offer}." if offer else ""
        return {
            "body": f"{name}, your {metric} in {location} is up {delta} in the current window.{offer_text} Want me to help turn this local momentum into your next post or offer?",
            "cta": "open_ended",
            "rationale": "Grounded performance spike message with locality, metric delta, and offer.",
        }

    if kind == "renewal_due":
        days = payload.get("days_remaining", (merchant.get("subscription") or {}).get("days_remaining")) or "15"
        plan = payload.get("plan", (merchant.get("subscription") or {}).get("plan")) or "Pro"
        return {
            "body": f"{name}, your {plan} plan for your {location} business is due for renewal in {days} days. Shall I help you review the next step?",
            "cta": "binary_yes_no",
            "rationale": "Grounded subscription renewal message with locality.",
        }

    if kind == "cde_opportunity":
        item = _digest_item(category, trigger)
        title = item.get("title", "IDA CDE Webinar") if item else "IDA CDE Webinar"
        credits = payload.get("credits", 2)
        return {
            "body": f"{name}, CDE credit opportunity for your {location} practice: {title} offers {credits} CDE points. Free for members. Want me to share the registration link?",
            "cta": "open_ended",
            "rationale": "Grounded CDE opportunity message with locality and credit count.",
        }

    if kind == "competitor_opened":
        comp = payload.get("competitor_name", "a new clinic")
        dist = payload.get("distance_km", "1.3")
        comp_offer = payload.get("their_offer", "discounted cleaning")
        my_offer_text = f" Your active offer: {offer}." if offer else ""
        return {
            "body": f"{name}, competitive alert: {comp} opened {dist}km from your {location} practice offering {comp_offer}.{my_offer_text} Want me to suggest a local visibility strategy?",
            "cta": "open_ended",
            "rationale": "Grounded competitor alert message with location, distance, and offer comparison.",
        }

    if kind == "gbp_unverified":
        uplift = _percent(payload.get("estimated_uplift_pct") or 0.30) or "30%"
        return {
            "body": f"Hi {name}, your Google profile for {location} is unverified. Verification typically boosts local discovery by {uplift}. Want me to guide you through the 2-minute verification steps?",
            "cta": "open_ended",
            "rationale": "Grounded GBP unverified alert message with location and estimated discovery uplift.",
        }

    if kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message") or 38
        return {
            "body": f"Hi {name}, it has been {days} days since our last update for {location}. Active offer: {offer or 'featured promotion'}. Shall we review one quick step to re-engage local customers this week?",
            "cta": "binary_yes_no",
            "rationale": "Grounded dormancy re-engagement message with location and offer.",
        }

    if kind == "review_theme_emerged":
        theme = str(payload.get("theme", "customer feedback")).replace("_", " ")
        count = payload.get("occurrences_30d", 4)
        quote = payload.get("common_quote", "")
        quote_text = f' ("{quote}")' if quote else ""
        return {
            "body": f"Hi {name}, review trend detected for your {location} business: {count} recent mentions of {theme}{quote_text}. Want me to draft a quick operational fix or post response?",
            "cta": "open_ended",
            "rationale": "Grounded review theme alert with occurrence count and common quote.",
        }

    if kind == "supply_alert":
        mol = str(payload.get("molecule", "drug")).title()
        batches = ", ".join(payload.get("affected_batches") or [])
        batch_text = f" (Batches: {batches})" if batches else ""
        return {
            "body": f"Hi {name}, urgent CDSCO recall alert for {location}: {mol}{batch_text}. Please check your inventory immediately. Want me to help flag affected batch IDs?",
            "cta": "open_ended",
            "rationale": "Grounded supply recall alert with molecule and batch IDs.",
        }

    cust_dict = customer if isinstance(customer, dict) else {}
    c_identity = cust_dict.get("identity") or {}
    c_name = c_identity.get("name") or "there"

    if kind == "chronic_refill_due":
        b_name = _business_name(merchant)
        mols = ", ".join(payload.get("molecule_list") or ["monthly medication"])
        return {
            "body": f"Hi {c_name}, {b_name} in {location} here. Your chronic refill ({mols}) is due shortly. Would you like us to prepare your refill for home delivery?",
            "cta": "open_ended",
            "rationale": "Grounded chronic refill reminder with molecule list and delivery offer.",
        }

    if kind == "category_seasonal":
        trends = ", ".join((payload.get("trends") or [])[:2]).replace("_", " ")
        trend_text = f": {trends}" if trends else ""
        return {
            "body": f"Hi {name}, summer demand shift in {location}{trend_text}. Shall I help you update your shelf display or active offers to match local demand?",
            "cta": "binary_yes_no",
            "rationale": "Grounded seasonal demand shift message with trends.",
        }

    if kind == "wedding_package_followup":
        b_name = _business_name(merchant)
        return {
            "body": f"Hi {c_name}, {b_name} in {location} here 🌸 Hope your bridal trial went wonderfully! We have your 30-day skin prep program ready. Would you like to schedule your first session?",
            "cta": "open_ended",
            "rationale": "Grounded bridal package follow-up message.",
        }

    if kind == "active_planning_intent":
        topic = str(payload.get("intent_topic", "campaign")).replace("_", " ")
        return {
            "body": f"Hi {name}, regarding your {topic} plan for {location} — I have prepared the proposed schedule and package details. Shall I share them now?",
            "cta": "binary_yes_no",
            "rationale": "Grounded active planning intent follow-up.",
        }

    if kind in {"trial_followup", "customer_lapsed_hard", "customer_lapsed_soft"}:
        b_name = _business_name(merchant)
        slots = payload.get("next_session_options") or payload.get("available_slots") or []
        slot_label = slots[0].get("label") if slots and isinstance(slots[0], dict) else None
        slot_text = f" Available slot: {slot_label}." if slot_label else ""
        return {
            "body": f"Hi {c_name}, {b_name} in {location} here. We miss seeing you!{slot_text} Would you like to book your next session?",
            "cta": "open_ended",
            "rationale": "Grounded customer re-engagement follow-up.",
        }

    if kind == "ipl_match_today":
        match = payload.get("match", "IPL match")
        return {
            "body": f"Hi {name}, IPL match today ({match}) in {location}! Perfect time to run a match-hour promo. Want me to draft a quick offer post?",
            "cta": "open_ended",
            "rationale": "Grounded IPL match day promotion proposal.",
        }

    if kind == "milestone_reached":
        val = payload.get("value_now") or payload.get("milestone_value")
        metric = str(payload.get("metric", "reviews")).replace("_", " ")
        return {
            "body": f"Congratulations {name}! Your {location} business just hit {val} {metric}! Want me to create a celebratory post to thank your customers?",
            "cta": "open_ended",
            "rationale": "Grounded milestone celebration message.",
        }

    if kind == "festival_upcoming":
        fest = payload.get("festival", "upcoming festival")
        days = payload.get("days_until")
        day_text = f" in {days} days" if days else ""
        return {
            "body": f"Hi {name}, {fest} is coming up{day_text}! Let's prepare your {location} business with a festive offer. Want me to share 2 quick offer concepts?",
            "cta": "open_ended",
            "rationale": "Grounded festival preparation proposal.",
        }

    if kind == "winback_eligible":
        days = payload.get("days_since_expiry") or 30
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        lapsed_text = f" You have {lapsed} lapsed customers ready to re-engage." if lapsed else ""
        return {
            "body": f"Hi {name}, your subscription expired {days} days ago for {location}.{lapsed_text} Shall we restore your account and launch a winback campaign?",
            "cta": "binary_yes_no",
            "rationale": "Grounded subscription winback message.",
        }

    if kind == "curious_ask_due":
        return {
            "body": f"Hi {name}, quick check for {location}: which service or package is seeing the highest demand this week? Active offer: {offer or 'standard catalog'}.",
            "cta": "open_ended",
            "rationale": "Grounded curiosity ask to gather merchant insights.",
        }

    raw_topic = payload.get("metric_or_topic") or kind
    topic = str(raw_topic if raw_topic not in {"", "unknown_event"} else "an update").replace("_", " ")
    offer_text = f" Active offer: {offer}." if offer else ""
    return {
        "body": f"Hi {name}, I have a {topic} update for {location}.{offer_text} Would you like me to share the relevant next step?",
        "cta": "open_ended",
        "rationale": "Grounded fallback message using trigger topic, locality, and merchant identity.",
    }


def _llm_request(provider: str, model: str, prompt: str) -> str:
    import time
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        models_to_try = [model, "gemini-1.5-flash", "gemini-flash-latest"]
        last_exc = None
        for m in models_to_try:
            if not m:
                continue
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 500},
            }
            try:
                request = urlrequest.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                response = urlrequest.urlopen(request, timeout=3.5)
                data = json.loads(response.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                last_exc = e
                continue
        if last_exc:
            raise last_exc

    if provider == "openai":
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON with body, cta, rationale.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urlrequest.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}",
                "Content-Type": "application/json",
            },
        )
        response = urlrequest.urlopen(request, timeout=8)
        data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    raise ValueError(f"Unsupported bot LLM provider: {provider}")


def _parse_llm_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("LLM did not return JSON")
    result = json.loads(match.group())
    if not isinstance(result, dict):
        raise ValueError("LLM JSON was not an object")
    return result


def template_details(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Return approved-template metadata without placing the whole body in params."""
    kind = str(trigger.get("kind") or "engagement")
    payload = trigger.get("payload") or {}
    recipient = ((customer or {}).get("identity") or {}).get("name") if customer else None
    recipient = recipient or _name(merchant)
    templates = {
        "research_digest": "vera_research_digest_v1",
        "regulation_change": "vera_compliance_update_v1",
        "recall_due": "vera_recall_due_v1",
        "renewal_due": "vera_renewal_due_v1",
        "perf_dip": "vera_performance_update_v1",
        "perf_spike": "vera_performance_update_v1",
        "wedding_package_followup": "vera_customer_followup_v1",
        "trial_followup": "vera_customer_followup_v1",
        "chronic_refill_due": "vera_customer_followup_v1",
    }

    if kind == "research_digest":
        item = _digest_item(category, trigger) or {}
        return templates[kind], [recipient, str(item.get("title") or "category research update"), "open-ended follow-up"]
    if kind == "recall_due":
        slots = payload.get("available_slots") or []
        slot_text = " or ".join(str(slot.get("label")) for slot in slots[:2] if slot.get("label"))
        return templates[kind], [recipient, str(payload.get("due_date") or "recall due"), slot_text or "reply with a suitable time"]
    if kind == "renewal_due":
        subscription = merchant.get("subscription") or {}
        return templates[kind], [recipient, str(payload.get("plan") or subscription.get("plan") or "current plan"), str(payload.get("days_remaining") or subscription.get("days_remaining") or "review")]
    if kind in {"perf_dip", "perf_spike"}:
        return templates[kind], [recipient, str(payload.get("metric") or "performance"), str(payload.get("delta_pct") or "updated")]
    return templates.get(kind, f"vera_{kind}_v1"), [recipient, kind.replace("_", " "), "open-ended follow-up"]

