"""Per-workspace token + cost accounting for the LLM gateway (doc 06 §7, §9).

MVP uses a simple in-process accountant: it accumulates input/output tokens and
an estimated USD cost per ``workspace_id``. This is enough to enforce the
per-workspace daily token budgets described in doc 18 §6.6 within a single
worker process and to surface "LLM token consumption by task and workspace"
metrics (doc 06 §9).

TODO N3 / TODO E2: persistent, cross-process accounting (usage metering) will
attach behind this same :class:`TokenAccountant` protocol — swap the in-memory
implementation for one backed by Postgres/Redis without touching the gateway.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Protocol

# USD per 1K tokens, keyed by model id. Coarse defaults for cost estimation;
# the figures only need to be order-of-magnitude right for budget alerts. Prefix
# match falls back to a conservative default for unknown models.
_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    # (input_usd_per_1k, output_usd_per_1k)
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-haiku": (0.00025, 0.00125),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-sonnet": (0.003, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
}
_DEFAULT_PRICING = (0.003, 0.015)  # Sonnet-class; conservative for unknowns.


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of a call from token counts.

    Local/Ollama models price at zero. Unknown hosted models fall back to a
    conservative Sonnet-class rate so budget alerts err on the safe side.
    """
    if model.startswith("ollama") or "/" in model:
        return 0.0
    input_rate, output_rate = _DEFAULT_PRICING
    for prefix, rates in _PRICING_PER_1K.items():
        if model.startswith(prefix):
            input_rate, output_rate = rates
            break
    return (input_tokens / 1000) * input_rate + (output_tokens / 1000) * output_rate


@dataclass
class TaskUsage:
    """Per-task usage breakdown. Token/call counts are ints; cost is USD."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


@dataclass
class UsageCounters:
    """Accumulated usage for a single workspace."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    # Per-task breakdown, e.g. {"classify": TaskUsage(...)}.
    by_task: dict[str, TaskUsage] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenAccountant(Protocol):
    """Records and reports LLM usage per workspace."""

    def record(
        self,
        *,
        workspace_id: str,
        task: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None: ...

    def usage(self, workspace_id: str) -> UsageCounters: ...

    def reset(self, workspace_id: str | None = None) -> None: ...


class InMemoryTokenAccountant:
    """Thread-safe, process-local :class:`TokenAccountant` for the MVP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, UsageCounters] = defaultdict(UsageCounters)

    def record(
        self,
        *,
        workspace_id: str,
        task: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        with self._lock:
            counters = self._usage[workspace_id]
            counters.input_tokens += input_tokens
            counters.output_tokens += output_tokens
            counters.cost_usd += cost_usd
            counters.calls += 1
            task_counters = counters.by_task.setdefault(task, TaskUsage())
            task_counters.input_tokens += input_tokens
            task_counters.output_tokens += output_tokens
            task_counters.cost_usd += cost_usd
            task_counters.calls += 1

    def usage(self, workspace_id: str) -> UsageCounters:
        with self._lock:
            # Return a copy so callers can't mutate internal state.
            counters = self._usage.get(workspace_id, UsageCounters())
            return UsageCounters(
                input_tokens=counters.input_tokens,
                output_tokens=counters.output_tokens,
                cost_usd=counters.cost_usd,
                calls=counters.calls,
                by_task={task: replace(stats) for task, stats in counters.by_task.items()},
            )

    def reset(self, workspace_id: str | None = None) -> None:
        with self._lock:
            if workspace_id is None:
                self._usage.clear()
            else:
                self._usage.pop(workspace_id, None)
