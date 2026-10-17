"""A lightweight Streamlit startup check for the deployable dashboard."""

from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_northstar_dashboard_starts_without_provider_secrets():
    app = Path(__file__).resolve().parents[1] / "app.py"
    result = AppTest.from_file(str(app), default_timeout=30).run()

    assert not result.exception
    assert any("NORTHSTAR" in block.value for block in result.markdown)
