"""Per-step LLM usage accumulation.

`agents.planner.tracking.instrument` opens a `StepUsage` for each node
execution and binds it to a `ContextVar`; `LLMGateway.complete` records
every successful call into whatever accumulator is bound. That way the
step trace knows which model(s) a node used and how many tokens it
spent, without any node or agent passing usage around by hand.

Outside an instrumented node (an API request, a Celery task that is not
a graph) nothing is bound and `record_llm_usage` is a no-op.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class StepUsage:
    """What the LLM calls made during one step added up to.

    Token counts stay `None` until at least one call reports them: a
    provider that returns no usage must read as "unknown", not "0".
    """

    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    models: list[str] = field(default_factory=list)

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        self.provider = provider
        self.model = model
        if model not in self.models:
            self.models.append(model)
        if input_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + input_tokens
        if output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + output_tokens


_current: ContextVar[StepUsage | None] = ContextVar("aikdap_step_usage", default=None)


@contextmanager
def step_usage_scope() -> Iterator[StepUsage]:
    """Bind a fresh accumulator for the duration of one step."""
    usage = StepUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def current_step_usage() -> StepUsage | None:
    """The accumulator bound to the running step, if any."""
    return _current.get()


def record_llm_usage(
    *, provider: str, model: str, input_tokens: int | None, output_tokens: int | None
) -> None:
    """Add one completed call to the running step's accumulator."""
    usage = _current.get()
    if usage is not None:
        usage.record(
            provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
