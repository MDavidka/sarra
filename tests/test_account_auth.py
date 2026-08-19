from __future__ import annotations

from pathlib import Path

import pytest

from syte.config import settings


@pytest.mark.asyncio
async def test_email_password_account_authentication_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Email-password accounts are durable, verify scrypt hashes, and create account sessions."""
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")

    from syte import auth, database

    await database.init_db()
    assert await database.count_operator_accounts() == 0

    account = await database.create_operator_account({
        "id": "account-1",
        "email": "operator@example.com",
        "password_hash": auth.hash_password("a-strong-password"),
        "display_name": "Operator",
        "avatar_icon": "rocket",
        "role": "owner",
    })
    assert account["email"] == "operator@example.com"
    assert await database.count_operator_accounts() == 1

    authenticated = await auth.authenticate_operator_account("OPERATOR@example.com", "a-strong-password")
    assert authenticated is not None
    assert authenticated["id"] == "account-1"
    assert await auth.authenticate_operator_account("operator@example.com", "incorrect-password") is None

    session = auth.create_account_operator_session(authenticated)
    assert session["account"]["email"] == "operator@example.com"
    assert session["account"]["avatar_icon"] == "rocket"
    assert session["csrf_token"]
