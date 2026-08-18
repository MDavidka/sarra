from datetime import UTC, datetime

from syte.platform.backup_scheduler import cron_matches


def test_cron_matches_default_midnight_schedule():
    assert cron_matches("0 0 * * *", datetime(2026, 8, 18, 0, 0, tzinfo=UTC))
    assert not cron_matches("0 0 * * *", datetime(2026, 8, 18, 0, 1, tzinfo=UTC))


def test_cron_matches_step_schedule():
    assert cron_matches("*/15 * * * *", datetime(2026, 8, 18, 12, 30, tzinfo=UTC))
    assert not cron_matches("*/15 * * * *", datetime(2026, 8, 18, 12, 31, tzinfo=UTC))
