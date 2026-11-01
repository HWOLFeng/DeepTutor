"""Every turn capability must accept the reader's timezone.

The chat surface stamps ``config.timezone`` on *every* message it sends, while
each capability's config model forbids unknown fields. One model missing the
field therefore rejects every send — which is exactly what happened: the turn
was refused with "timezone: Extra inputs are not permitted" and the UI reported
it as a connection failure.

These tests hold the invariant for capabilities that do not exist yet, which is
why they iterate the catalog instead of naming models.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deeptutor.agents.math_animator.request_config import (
    validate_math_animator_request_config,
)
from deeptutor.agents.research.request_config import validate_research_request_config
from deeptutor.runtime.registry.capability_registry import CapabilityRegistry
from deeptutor.runtime.request_contracts import (
    ChatRequestConfig,
    validate_capability_config,
    validate_chat_request_config,
    validate_deep_question_request_config,
    validate_visualize_request_config,
)

ZONE = "Asia/Shanghai"


def _turn_config_models() -> list[tuple[str, type]]:
    registry = CapabilityRegistry()
    registry.load_builtins()
    return [(entry.name, entry.config_model) for entry in registry.catalog.entries("turn")]


def _errors(model, payload) -> list[dict]:
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return list(exc.errors())
    return []


def test_the_catalog_is_not_empty():
    # A silent empty parameterisation would make every assertion below vacuous.
    assert len(_turn_config_models()) >= 5


@pytest.mark.parametrize("name,model", _turn_config_models())
def test_every_capability_config_declares_the_reader_timezone(name, model):
    assert "timezone" in model.model_fields, name


@pytest.mark.parametrize("name,model", _turn_config_models())
def test_a_reader_timezone_is_never_an_unknown_field(name, model):
    # Required capability options are irrelevant here: this asks only whether the
    # zone is *recognised*, i.e. whether validation calls it extra input.
    extras = [
        error
        for error in _errors(model, {"timezone": ZONE})
        if error["type"] == "extra_forbidden" and error["loc"] == ("timezone",)
    ]
    assert extras == [], name


def test_unknown_options_are_still_rejected():
    # The fix must not turn `extra="forbid"` into `extra="ignore"`.
    assert _errors(ChatRequestConfig, {"timezone": ZONE}) == []
    types = {error["type"] for error in _errors(ChatRequestConfig, {"timezone": ZONE, "bogus": 1})}
    assert "extra_forbidden" in types


def test_capability_validators_accept_reader_timezone():
    # Modules whose config models are not reachable through the catalog still
    # receive the same per-message field.
    assert validate_chat_request_config({"timezone": ZONE}).timezone == ZONE
    assert validate_deep_question_request_config({"timezone": ZONE}).timezone == ZONE
    assert validate_visualize_request_config({"timezone": ZONE}).timezone == ZONE
    assert (
        validate_research_request_config(
            {"timezone": ZONE, "mode": "notes", "depth": "quick"}
        ).timezone
        == ZONE
    )
    assert validate_math_animator_request_config({"timezone": ZONE}).timezone == ZONE


def test_an_absent_timezone_is_dropped_not_stored_as_empty():
    # Older clients (and the CLI) send no zone; the runtime falls back to the
    # host zone, so this must stay a valid request — and it must not add
    # ``timezone: ""`` to the persisted request snapshot.
    assert validate_capability_config("chat", {}) == {}
    assert validate_capability_config("chat", None) == {}
    assert validate_capability_config("chat", {"timezone": ZONE}) == {"timezone": ZONE}
