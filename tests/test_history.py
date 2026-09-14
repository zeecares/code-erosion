"""Unit tests for trend history and badge support (R4).

Like test_baseline.py, these run without tree-sitter: they exercise
history.py over synthetic report dicts.
"""

from __future__ import annotations

import json

import pytest

from code_erosion import history


def make_report(*, verbosity: float = 0.10, erosion: float = 0.50) -> dict:
    return {
        "verbosity": verbosity,
        "erosion": erosion,
        "total_loc": 1000,
        "files_scanned": 10,
        "high_cc_functions": 3,
    }


# ---------------------------------------------------------------- records


def test_history_record_shape():
    record = history.history_record(
        make_report(verbosity=0.123456, erosion=0.44087),
        sha="abcdef123456",
        date="2026-09-14T09:00:00Z",
    )
    assert record["schema"] == history.HISTORY_SCHEMA
    assert record["sha"] == "abcdef123456"
    assert record["date"] == "2026-09-14T09:00:00Z"
    assert record["verbosity"] == 0.1235
    assert record["erosion"] == 0.4409
    assert record["total_loc"] == 1000
    assert record["files_scanned"] == 10
    assert record["high_cc_functions"] == 3


def test_history_record_defaults_date_and_sha():
    record = history.history_record(make_report())
    assert record["sha"] == "unknown"
    assert record["date"].endswith("Z")


# ---------------------------------------------------------------- append / load


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "hist.jsonl"
    history.append_history(path, history.history_record(make_report(erosion=0.60), sha="aaa", date="2026-09-12T00:00:00Z"))
    history.append_history(path, history.history_record(make_report(erosion=0.50), sha="bbb", date="2026-09-13T00:00:00Z"))
    entries = history.load_history(path)
    assert [e["sha"] for e in entries] == ["aaa", "bbb"]
    assert entries[1]["erosion"] == 0.5


def test_load_history_skips_malformed_lines(tmp_path):
    path = tmp_path / "hist.jsonl"
    path.write_text(
        'not json\n'
        '{"erosion": 0.5}\n'  # missing date
        '{"erosion": 0.4, "date": "2026-09-13T00:00:00Z", "sha": "x"}\n'
        "\n",
        encoding="utf-8",
    )
    entries = history.load_history(path)
    assert len(entries) == 1
    assert entries[0]["erosion"] == 0.4


def test_load_history_missing_file(tmp_path):
    assert history.load_history(tmp_path / "nope.jsonl") == []


def test_load_history_sorts_oldest_first(tmp_path):
    path = tmp_path / "hist.jsonl"
    history.append_history(path, history.history_record(make_report(), sha="new", date="2026-09-14T00:00:00Z"))
    history.append_history(path, history.history_record(make_report(), sha="old", date="2026-09-12T00:00:00Z"))
    assert [e["sha"] for e in history.load_history(path)] == ["old", "new"]


# ---------------------------------------------------------------- trend


@pytest.mark.parametrize(
    "delta,expected",
    [(-0.02, "improving"), (-0.005, "flat"), (0.0, "flat"), (0.005, "flat"), (0.02, "worsening")],
)
def test_trend_direction(delta, expected):
    assert history.trend_direction(delta) == expected


def test_render_trend_empty():
    assert history.render_trend_markdown([]) == ""


def test_render_trend_markdown_window_and_direction():
    entries = [
        {"sha": f"s{i}", "date": f"2026-09-{10 + i}T00:00:00Z", "verbosity": 0.13, "erosion": e}
        for i, e in enumerate([0.68, 0.60, 0.55, 0.53, 0.50, 0.47, 0.44])
    ]
    md = history.render_trend_markdown(entries, limit=5)
    assert "**Trend on the default branch (last 5 scans):** improving" in md
    assert "0.530 → 0.500 → 0.470 → 0.440" in md  # windowed, not the full run
    assert "0.680" not in md
    assert "| 2026-09-16 | `s6` | 0.130 | 0.440 |" in md
    assert "| date | commit | verbosity | erosion |" in md


def test_render_trend_single_entry_is_flat():
    md = history.render_trend_markdown(
        [{"sha": "abc1234", "date": "2026-09-14T00:00:00Z", "verbosity": 0.1, "erosion": 0.5}]
    )
    assert "last 1 scan):" in md
    assert "flat (0.500)" in md


# ---------------------------------------------------------------- badge


@pytest.mark.parametrize(
    "erosion,color",
    [(0.30, "green"), (0.35, "green"), (0.44, "yellow"), (0.55, "yellow"), (0.60, "orange"), (0.68, "orange"), (0.69, "red")],
)
def test_badge_colors_follow_bands(erosion, color):
    badge = history.badge_from_report(make_report(erosion=erosion))
    assert badge["color"] == color
    assert badge["schemaVersion"] == 1
    assert badge["label"] == "code erosion"
    assert badge["message"] == f"{erosion:.3f}"


def test_write_badge(tmp_path):
    path = tmp_path / "badge.json"
    badge = history.write_badge(path, make_report(erosion=0.44))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == badge
    assert on_disk["message"] == "0.440"
