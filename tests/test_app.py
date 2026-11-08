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
    texts = " ".join(m.value for m in at.markdown)
    assert "Enter your API key in the left sidebar (click » at top left if hidden)." in texts
    assert "Upload your own screenplays. No Kuku data is included." in texts
    btn["Demo mode: see it on a made-up show"].click().run()
    assert not at.exception
    cards = [m.value for m in at.markdown if 'class="pr"' in m.value]
    assert len(cards) >= 1
    for c in cards:
        # rank and score, template tag, confidence badge, duration bar, hook and cliffhanger pull quotes,
        # mono segments, per-episode timeline, colour-coded chips, phone preview
        for part in ['class="pr-rank"', 'class="pr-score"', 'class="tag"', 'class="conf ', 'class="dbar"', 'class="pq hook"', 'class="pq cliff"',
                     'class="seg"', 'class="track"', 'class="chip ', 'class="phone"']:
            assert part in c
    assert any("Built by Divyansh Gupta · Promo Finder is an independent assignment project, not a Kuku product." in m.value for m in at.markdown)
    assert len(at.get("download_button")) == 4
    strip = [m.value for m in at.markdown if 'class="sum"' in m.value]
    assert len(strip) == 1
    for label in ["Episodes processed", "Promos found", "Rules passed", "Cost of this run"]:
        assert label in strip[0]
    assert "6/6" in strip[0] and "<b>$0</b>" in strip[0]
