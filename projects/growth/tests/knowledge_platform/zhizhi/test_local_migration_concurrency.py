"""Existing desktop routes and jobs must be able to open the same ledger safely."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from ahadiff.growth.local import GrowthLocalRepository


def test_concurrent_first_open_serializes_all_migrations_and_preserves_rows(tmp_path):
    path = tmp_path / "growth.sqlite"
    ready = Barrier(6)

    def open_ledger(index):
        ready.wait()
        with GrowthLocalRepository(path) as ledger:
            with ledger.connection:
                ledger.connection.execute(
                    "INSERT INTO projects VALUES (?,?,?)",
                    (f"project-{index}", "保留历史", "2026-10-19"),
                )
            return ledger.connection.execute("PRAGMA user_version").fetchone()[0]

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(open_ledger, range(6))) == [33] * 6
    with GrowthLocalRepository(path) as ledger:
        assert ledger.connection.execute("SELECT count(*) FROM projects").fetchone()[0] == 6
        columns = [
            row["name"] for row in ledger.connection.execute("PRAGMA table_info(opportunities)")
        ]
        assert columns.count("card_json") == 1
