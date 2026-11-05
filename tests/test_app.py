import os
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def test_demo_mode_loads_without_key(monkeypatch):
    for k in list(os.environ):
        if k.endswith("_API_KEY"):
            monkeypatch.delenv(k)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    btn = {b.label: b for b in at.button}
    # no key, no files: the real run stays off
    assert btn["Run"].disabled
    btn["Demo mode: see it on a made-up show"].click().run()
    assert not at.exception
    assert len([m for m in at.markdown if m.value.startswith("**#")]) >= 1
    assert len(at.get("download_button")) == 4
    assert [m.value for m in at.metric if m.label == "Cost of this view"] == ["$0"]
