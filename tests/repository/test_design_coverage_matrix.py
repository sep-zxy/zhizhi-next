"""Audit traceability, not a claim that UI screenshots or real E2E passed."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOVES = sorted(json.loads((ROOT / "docs/migration/layout.json").read_text(encoding="utf-8"))["moves"], key=lambda item: len(item["from"]), reverse=True)

def current_source(original):
    for item in MOVES:
        if original == item["from"] or original.startswith(item["from"] + "/"):
            return item["to"] + original[len(item["from"]):]
    return original


def test_coverage_matrix_has_all_approved_ids_and_real_sources():
    matrix = json.loads((ROOT / 'archive/acceptance/implementation/coverage-review-20261023/coverage-matrix.json').read_text())
    rows = matrix['rows']
    expected = {f'{prefix}{number:02}' for prefix, maximum in [('P', 18), ('F', 16), ('C', 21)] for number in range(1, maximum + 1)}
    assert {row['id'] for row in rows} == expected
    assert len(rows) == len(expected)
    for row in rows:
        assert row['implementation'] in {'implemented', 'partial', 'missing'}
        assert row['real_e2e_verified'] is False
        assert (ROOT / current_source(row['source'])).is_file(), row['source']
        assert row['gap']


def test_later_registry_does_not_claim_complete_and_p1_remains_separate():
    matrix = json.loads((ROOT / 'archive/acceptance/implementation/coverage-review-20261023/coverage-matrix.json').read_text())
    rows = {row['id']: row for row in matrix['rows']}
    for id in ('P13', 'F13', 'C17'):
        assert rows[id]['phase'] == 'Later'
        assert rows[id]['implementation'] == 'missing'
    for id in ('P10', 'P12', 'F10', 'F12'):
        assert rows[id]['phase'] == 'P1'
