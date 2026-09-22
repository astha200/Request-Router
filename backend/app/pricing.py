"""Cost and savings arithmetic. Pure functions: no I/O, no globals."""
from .models import FRONTIER, ModelSpec


def cost_usd(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    """Cost of one request under a model's published per-1M-token rates."""
    return (input_tokens / 1_000_000) * spec.input_per_1m + (
        output_tokens / 1_000_000
    ) * spec.output_per_1m


def frontier_baseline_usd(input_tokens: int, output_tokens: int) -> float:
    """What this request would have cost had it gone to the frontier model.

    Assumption (documented in the README): identical token counts. A frontier
    model would in reality emit a different number of output tokens, so this is
    an estimate, not a measurement. We state it rather than hide it.
    """
    return cost_usd(FRONTIER, input_tokens, output_tokens)


def estimate_tokens(text: str) -> int:
    """Fallback when upstream gives us no usage block (~4 chars/token)."""
    return max(1, len(text) // 4)
