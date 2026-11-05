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
    cards = [m.value for m in at.markdown if 'class="cr"' in m.value]
    assert len(cards) >= 1
    for c in cards:
        # rank badge, template tag, duration bar, bold hook, italic cliffhanger, colour-coded chips, timeline, phone
        for part in ['class="rank"', 'class="tag"', 'class="dbar"', 'class="beat hook"', 'class="beat cliff"', 'class="chip ', 'class="track"', 'class="phone"']:
            assert part in c
    assert any("Built by Divyansh Gupta" in m.value for m in at.markdown)
    assert len(at.get("download_button")) == 4
    strip = [m.value for m in at.markdown if 'class="sum"' in m.value]
    assert len(strip) == 1
    for label in ["Episodes processed", "Promos found", "Rules passed", "Cost of this run"]:
        assert label in strip[0]
    assert "6/6" in strip[0] and "<b>$0</b>" in strip[0]
