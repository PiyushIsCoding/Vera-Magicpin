from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextStore:
    """In-memory versioned context store for one judge run."""

    def __init__(self) -> None:
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def put(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: dict[str, Any],
    ) -> tuple[bool, int | None]:
        key = (scope, context_id)
        with self._lock:
            current = self._contexts.get(key)
            if current and current["version"] >= version:
                return False, current["version"]
            self._contexts[key] = {
                "version": version,
                "payload": deepcopy(payload),
            }
        return True, None

    def get(self, scope: str, context_id: str | None) -> dict[str, Any] | None:
        if not context_id:
            return None
        with self._lock:
            entry = self._contexts.get((scope, context_id))
            return deepcopy(entry["payload"]) if entry else None

    def counts(self) -> dict[str, int]:
        counts = {scope: 0 for scope in VALID_SCOPES}
        with self._lock:
            for scope, _ in self._contexts:
                counts[scope] = counts.get(scope, 0) + 1
        return counts

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()


store = ContextStore()
