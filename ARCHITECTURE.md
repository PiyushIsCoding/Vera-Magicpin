# Vera Engagement Bot Architecture

## System Architecture

```mermaid
flowchart LR
    J[Magicpin Judge Harness]

    subgraph BOT[Candidate Bot - FastAPI]
        API[HTTP API\n/v1/*]
        STORE[(ContextStore\nversioned in-memory state)]
        ROUTER[Tick Router]
        RESOLVE[Context Resolver]
        COMP[Engagement Composer]
        DET[Deterministic Composer]
        LLM[Optional LLM Wording]
        VALIDATE[Action Validator]
        SUPPRESS[Suppression + Opt-out State]
        CONV[Conversation Handler]
        REPLYVAL[Reply Validator]
    end

    DATA[Category / Merchant / Customer / Trigger JSON]
    SCORE[Judge LLM Scoring]

    J -->|POST /v1/context| API
    API --> STORE
    DATA -. local seed / expanded data .-> STORE

    J -->|POST /v1/tick| API
    API --> ROUTER
    ROUTER --> RESOLVE
    RESOLVE --> STORE
    RESOLVE --> SUPPRESS
    RESOLVE --> COMP

    COMP --> DET
    DET -. fallback always available .-> COMP
    COMP -->|optional configured call| LLM
    LLM -->|valid grounded JSON| COMP
    LLM -. timeout / invalid / hallucinated output .-> DET

    COMP --> VALIDATE
    VALIDATE -->|valid action| SUPPRESS
    VALIDATE -->|invalid action| DET
    SUPPRESS -->|actions[]| API
    API -->|JSON response| J

    J -->|POST /v1/reply| API
    API --> CONV
    CONV --> REPLYVAL
    REPLYVAL -->|send / wait / end| API
    API -->|JSON response| J

    J -->|messages for scoring| SCORE
```

## Context Flow

```mermaid
flowchart TD
    C[CategoryContext] --> R[Context Resolver]
    M[MerchantContext] --> R
    T[TriggerContext] --> R
    U[Optional CustomerContext] --> R

    R --> D{Trigger scope}
    D -->|merchant| MC[Merchant-facing composition\nsend_as = vera]
    D -->|customer| CC[Customer-facing composition\nsend_as = merchant_on_behalf]

    MC --> V[Validation]
    CC --> V
    V --> A[Action returned from /v1/tick]
```

## Conversation Flow

```mermaid
stateDiagram-v2
    [*] --> Active: /v1/tick sends action
    Active --> Waiting: canned auto-reply detected
    Waiting --> Ended: repeated auto-reply
    Active --> Ended: opt-out or hostile stop
    Active --> ActionReady: clear intent
    Active --> Active: normal reply
    Active --> Active: off-topic redirect
    Ended --> Ended: later replies ignored
```

## UML Class Diagram

```mermaid
classDiagram
    class FastAPIApp {
        +healthz() dict
        +metadata() dict
        +push_context(request) response
        +tick(request) response
        +reply(request) response
        +teardown() response
    }

    class ContextStore {
        -contexts: dict
        -lock: RLock
        +put(scope, context_id, version, payload) tuple
        +get(scope, context_id) dict
        +counts() dict
        +clear() None
    }

    class Composer {
        +compose(category, merchant, trigger, customer) dict
        +template_details(category, merchant, trigger, customer) tuple
        -deterministic_composition(...) dict
        -llm_composition(...) dict
    }

    class ConversationState {
        +merchant_id: str
        +customer_id: str
        +turns: list
        +sent_bodies: set
        +auto_reply_count: int
        +ended: bool
    }

    class ConversationHandler {
        +next_reply(state, message) dict
        +is_opt_out(message) bool
        +is_auto_reply(message) bool
        +has_action_intent(message) bool
    }

    class Validator {
        +validate_action(action, category, sent_bodies) tuple
        +validate_reply(reply) tuple
        +fallback_action(merchant, trigger, customer_id, conversation_id) dict
    }

    class CategoryContext {
        +slug: str
        +voice: dict
        +digest: list
        +offer_catalog: list
        +peer_stats: dict
    }

    class MerchantContext {
        +merchant_id: str
        +category_slug: str
        +identity: dict
        +performance: dict
        +offers: list
        +signals: list
    }

    class CustomerContext {
        +customer_id: str
        +merchant_id: str
        +identity: dict
        +relationship: dict
        +consent: dict
    }

    class TriggerContext {
        +id: str
        +scope: str
        +kind: str
        +payload: dict
        +suppression_key: str
        +expires_at: str
    }

    FastAPIApp --> ContextStore : stores contexts
    FastAPIApp --> Composer : composes tick actions
    FastAPIApp --> ConversationHandler : handles replies
    FastAPIApp --> Validator : validates responses
    FastAPIApp "1" o-- "many" ConversationState : maintains
    Composer --> CategoryContext : reads
    Composer --> MerchantContext : reads
    Composer --> TriggerContext : reads
    Composer --> CustomerContext : optionally reads
    ConversationHandler --> ConversationState : mutates
    Validator --> Composer : validates output
```

## UML Tick Sequence

```mermaid
sequenceDiagram
    participant Judge
    participant API as FastAPIApp
    participant Store as ContextStore
    participant Composer
    participant Validator

    Judge->>API: POST /v1/tick(now, trigger_ids)
    API->>Store: get(trigger)
    Store-->>API: TriggerContext
    API->>Store: get(merchant, category, customer?)
    Store-->>API: Resolved contexts
    API->>API: Check expiry, consent, opt-out, suppression
    API->>Composer: compose(category, merchant, trigger, customer?)
    Composer-->>API: body, cta, send_as, rationale
    API->>Composer: template_details(...)
    Composer-->>API: template name and params
    API->>Validator: validate_action(action)
    alt Valid action
        Validator-->>API: accepted
        API->>API: Record body and suppression key
    else Invalid action
        Validator-->>API: rejected
        API->>Validator: fallback_action(...)
        Validator-->>API: safe action
    end
    API-->>Judge: {actions: [...]}
```

## Endpoint Ownership

| Endpoint | Owner | Main responsibility |
|---|---|---|
| `GET /v1/healthz` | `bot.py` | Liveness and context counts |
| `GET /v1/metadata` | `bot.py` | Bot identity and active composition mode |
| `POST /v1/context` | `bot.py` + `context_store.py` | Versioned context ingestion |
| `POST /v1/tick` | `bot.py` + `composer.py` | Trigger resolution and proactive actions |
| `POST /v1/reply` | `bot.py` + `conversation.py` | Multi-turn conversation response |
| `POST /v1/teardown` | `bot.py` | Local/test state cleanup |

## Composition Decision

The bot uses deterministic composition by default:

```text
contexts -> trigger-specific rules -> validation -> action
```

When enabled, the optional LLM is used only for wording:

```text
contexts -> deterministic baseline -> LLM wording -> grounding validation
                                             |
                         invalid / timeout --+--> deterministic baseline
```

The LLM cannot change:

- Merchant or customer identity
- Trigger ID
- Suppression key
- Sender type
- Consent decision
- Expiry decision
- Conversation termination rules
