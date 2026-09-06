import json
import sys
from pathlib import Path

import pytest

# Import the package from src/ regardless of whether the editable install is
# healthy (hatchling writes its .pth without a trailing newline, which Python
# silently drops).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


def load_scoreboard(season: int, week: int) -> dict:
    return json.loads((FIXTURES / f"scoreboard-{season}-w{week:02d}.json").read_text())


@pytest.fixture
def week1_2026() -> dict:
    """The week whose slate screenshot drives the golden parser test."""
    return load_scoreboard(2026, 1)


@pytest.fixture
def week5_2025() -> dict:
    """A fully-completed week, used for end-to-end grading."""
    return load_scoreboard(2025, 5)


@pytest.fixture
def db_conn(tmp_path):
    from football_pool import db

    conn = db.connect(tmp_path / "test.db")
    yield conn
    conn.close()


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests that call the Claude API (costs money)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the real Claude API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (makes a real API call)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
