import pytest
from unittest.mock import AsyncMock

from syte.sqlite_utils import configure_sqlite, _wal_paths


@pytest.fixture(autouse=True)
def reset_wal_paths():
    """Reset the global _wal_paths set before each test."""
    _wal_paths.clear()
    yield
    _wal_paths.clear()


@pytest.mark.asyncio
async def test_configure_sqlite_initial():
    db = AsyncMock()
    db.database = "test.db"

    await configure_sqlite(db)

    assert db.execute.call_count == 3
    db.execute.assert_any_call("PRAGMA journal_mode=WAL")
    db.execute.assert_any_call("PRAGMA synchronous=NORMAL")
    db.execute.assert_any_call("PRAGMA busy_timeout=5000")
    assert "test.db" in _wal_paths


@pytest.mark.asyncio
async def test_configure_sqlite_skip_if_already_configured():
    db = AsyncMock()
    db.database = "test.db"
    _wal_paths.add("test.db")

    await configure_sqlite(db)

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_configure_sqlite_with_db_path_override():
    db = AsyncMock()
    db.database = "wrong.db"

    await configure_sqlite(db, db_path="correct.db")

    assert db.execute.call_count == 3
    assert "correct.db" in _wal_paths
    assert "wrong.db" not in _wal_paths


@pytest.mark.asyncio
async def test_configure_sqlite_empty_path_key():
    class FakeConnection:
        def __init__(self):
            self.execute = AsyncMock()

    fake_db = FakeConnection()

    # fake_db has no 'database' attribute, so getattr(..., "") returns ""
    await configure_sqlite(fake_db) # type: ignore

    assert fake_db.execute.call_count == 3
    assert len(_wal_paths) == 0


@pytest.mark.asyncio
async def test_configure_sqlite_empty_string_database():
    class FakeConnection:
        def __init__(self):
            self.database = ""
            self.execute = AsyncMock()

    fake_db = FakeConnection()

    await configure_sqlite(fake_db) # type: ignore

    assert fake_db.execute.call_count == 3
    assert len(_wal_paths) == 0
