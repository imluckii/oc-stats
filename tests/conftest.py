"""Shared pytest fixtures built on the synthetic fake service in ``tests.helpers``."""

from __future__ import annotations

import pytest

from oc_usage.service import ServiceClient
from tests.helpers import (
    T0,
    FakeService,
    RowSpec,
    assistant_message,
    model_switched_message,
    user_message,
)

__all__ = ["sample_specs", "fake_service", "client"]


@pytest.fixture()
def sample_specs() -> list[RowSpec]:
    # provider,         model,         variant,  inp,   cr,   cw, out, reas, cost,   created
    return [
        ("zai-coding-plan", "glm-4.7", "default", 500, 8000, 0, 40, 10, 0.0123, T0),
        ("zai-coding-plan", "glm-4.7", "default", 300, 6000, 0, 20, 0, 0.0, T0 + 1000),
        ("openai", "gpt-4o", "high", 1000, 0, 0, 200, 50, 0.5, T0 + 2000),
        ("openai", "gpt-4o-mini", "low", 100, 0, 0, 10, 0, 0.0, T0 + 3000),
    ]


@pytest.fixture()
def fake_service(sample_specs) -> FakeService:
    """One session with assistant messages plus non-assistant noise to filter."""
    messages = [
        user_message("msg_user"),
        assistant_message("msg_a0", sample_specs[0]),
        # A model switch mid-session is a control message, not a counted turn.
        model_switched_message("msg_switch", "glm-4.7", "zai-coding-plan", "default"),
        assistant_message("msg_a1", sample_specs[1]),
        assistant_message("msg_a2", sample_specs[2]),
        assistant_message("msg_a3", sample_specs[3]),
    ]
    return FakeService(["ses_one"], {"ses_one": messages})


@pytest.fixture()
def client(fake_service) -> ServiceClient:
    return ServiceClient(executable="opencode2", runner=fake_service)
