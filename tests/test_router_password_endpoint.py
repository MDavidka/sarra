from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_router_password_endpoint_returns_persisted_manager_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authenticated handler exposes the manager's saved WebGUI credential."""
    from syte import main
    from syte import nine_router_manager

    async def fake_router_password() -> tuple[str, bool]:
        return "persisted-test-password", False

    monkeypatch.setattr(nine_router_manager, "_router_password", fake_router_password)

    payload = await main.api_router_password({"email": "operator@example.com"})

    assert payload == {"password": "persisted-test-password", "is_new": False}


@pytest.mark.asyncio
async def test_router_password_endpoint_preserves_first_creation_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clients can render newly created and existing credentials distinctly."""
    from syte import main
    from syte import nine_router_manager

    async def fake_router_password() -> tuple[str, bool]:
        return "new-test-password", True

    monkeypatch.setattr(nine_router_manager, "_router_password", fake_router_password)

    payload = await main.api_router_password({"email": "operator@example.com"})

    assert payload == {"password": "new-test-password", "is_new": True}
