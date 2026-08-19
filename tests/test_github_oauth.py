from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from syte.config import settings


@pytest.mark.asyncio
async def test_github_oauth_state_is_single_use_and_expires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")

    from syte.database import consume_github_oauth_state, create_github_oauth_state, init_db

    await init_db()
    await create_github_oauth_state("state-once", "account-1", "https://syte.test/callback", int(time.time()) + 60)
    state = await consume_github_oauth_state("state-once")
    assert state is not None
    assert state["account_id"] == "account-1"
    assert await consume_github_oauth_state("state-once") is None

    await create_github_oauth_state("state-expired", "account-1", "https://syte.test/callback", int(time.time()) - 1)
    assert await consume_github_oauth_state("state-expired") is None


@pytest.mark.asyncio
async def test_github_connection_hides_ciphertext_and_recovers_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setattr(settings, "github_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "github_oauth_client_secret", "client-secret")
    monkeypatch.setattr(settings, "oauth_encryption_key", key)

    from syte.database import get_github_connection, init_db, save_github_connection
    from syte.github_oauth import token_for_account

    await init_db()
    ciphertext = Fernet(key.encode("utf-8")).encrypt(b"oauth-access-token").decode("utf-8")
    await save_github_connection(
        "account-1", login="octocat", avatar_url="https://avatars.example/octocat", token_ciphertext=ciphertext, scopes="repo read:user"
    )

    public_connection = await get_github_connection("account-1")
    assert public_connection is not None
    assert "token_ciphertext" not in public_connection
    assert await token_for_account("account-1") == "oauth-access-token"
