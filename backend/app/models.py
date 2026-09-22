"""Single source of truth for model identity and pricing.

Pricing verified 2026-09-07 against https://docs.fireworks.ai/serverless/pricing
(Standard serverless tier). Model IDs verified the same day against
GET https://api.fireworks.ai/inference/v1/models.

Re-run `python scripts/verify_models.py` before any demo: the Fireworks
serverless catalog rotates and retired models return 404.
"""
from dataclasses import dataclass
from typing import Dict, Literal

Category = Literal["cheap", "frontier"]

VIRTUAL_MODEL_ID = "fireworks-router/auto"

# Bumped whenever routing weights or the threshold change. Persisted per request
# so metrics can be segmented before and after a policy change. Otherwise a tuning
# change silently contaminates every historical average.
POLICY_VERSION = "v1.1"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    category: Category
    display_name: str
    input_per_1m: float   # USD per 1M input tokens
    output_per_1m: float  # USD per 1M output tokens


CHEAP_MODEL_ID = "accounts/fireworks/models/glm-5p3-flash"
FRONTIER_MODEL_ID = "accounts/fireworks/models/kimi-k3"

MODEL_REGISTRY: Dict[str, ModelSpec] = {
    CHEAP_MODEL_ID: ModelSpec(
        id=CHEAP_MODEL_ID,
        category="cheap",
        display_name="GLM 5.3 Flash",
        input_per_1m=0.15,
        output_per_1m=0.50,
    ),
    FRONTIER_MODEL_ID: ModelSpec(
        id=FRONTIER_MODEL_ID,
        category="frontier",
        display_name="Kimi K3",
        input_per_1m=3.00,
        output_per_1m=15.00,
    ),
}

CHEAP = MODEL_REGISTRY[CHEAP_MODEL_ID]
FRONTIER = MODEL_REGISTRY[FRONTIER_MODEL_ID]


def is_virtual(model: str) -> bool:
    return model == VIRTUAL_MODEL_ID


def resolve_pin(model: str) -> ModelSpec | None:
    """Return the pinned spec for a concrete model id, or None if not concrete.

    Accepts the bare Fireworks slug too (`glm-5p3-flash`), since the upstream
    API expands bare slugs and developers type them by habit.
    """
    if model in MODEL_REGISTRY:
        return MODEL_REGISTRY[model]
    for spec in MODEL_REGISTRY.values():
        if model == spec.id.rsplit("/", 1)[-1]:
            return spec
    return None


def known_model_ids() -> list[str]:
    return [VIRTUAL_MODEL_ID, CHEAP_MODEL_ID, FRONTIER_MODEL_ID]
