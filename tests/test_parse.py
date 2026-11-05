import os
import sys
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import parse

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
SAMPLE = os.path.join(ROOT, "tests", "fixtures", "sample_show")


def test_to_sec():
    assert parse.to_sec("00:12") == 12
    assert parse.to_sec("01:05") == 65
    assert parse.to_sec("1:02:03") == 3723
    assert parse.to_sec("00:12.5") == 12.5
    assert parse.to_sec("00:12,5") == 12.5


def test_timestamp_styles():
    raw = "[00:01] EMMA: hi there\n(00:04) Lily walks in.\n00:07 - RYAN: what?\n00:00:10 --> 00:00:12 LILY: go\nno time line here"
    rows = parse.rows_from_text(raw)
    assert [r["start"] for r in rows] == [1, 4, 7, 10, None]
    assert rows[0]["speaker"] == "EMMA"
    assert rows[1]["speaker"] is None
    assert rows[3]["text"] == "go"


def test_speaker_cue_lines():
    raw = "[00:03] EMMA\nYou'll regret this.\n[00:06] INT. HALL - DAY"
    rows = parse.rows_from_text(raw)
    assert rows[0]["speaker"] == "EMMA"
    assert rows[0]["start"] == 3
    assert rows[0]["text"] == "You'll regret this."


def test_end_times_and_estimates():
    rows = [{"start": 0.0, "text": "INT. HALL - DAY", "speaker": None},
            {"start": 2.0, "text": "one two three four five six seven eight nine ten", "speaker": "A"},
            {"start": None, "text": "hi", "speaker": "B"},
            {"start": 9.0, "text": "a b c d e f g h i j", "speaker": None}]
    ep = parse.build_episode(5, rows, CFG["parse"])
    ls = ep["lines"]
    assert ls[0]["end"] == 2.0
    # no timestamp: previous start + 10 words / 2.5
    assert ls[2]["start"] == 6.0
    assert ls[2]["estimated_time"] is True
    assert ls[1]["end"] == 6.0
    # last line: 10 words / 2.5 = 4s
    assert ls[3]["end"] == 13.0
    assert ep["duration_sec"] == 13.0
    assert ls[0]["type"] == "scene"
    assert ls[1]["type"] == "dialogue"
    assert ls[3]["id"] == "E5_L3"


def test_min_line_sec():
    rows = [{"start": 0.0, "text": "hi", "speaker": "A"}]
    ep = parse.build_episode(1, rows, CFG["parse"])
    assert ep["lines"][0]["end"] == 2.0


def test_sample_show_and_recap():
    eps, failed = parse.parse_folder(SAMPLE, CFG)
    assert failed == []
    assert [e["episode"] for e in eps] == [1, 2, 3]
    e2 = eps[1]
    assert e2["lines"][0]["is_recap"] is True
    rec = 0
    for e in eps:
        for ln in e["lines"]:
            if ln["is_recap"]:
                rec += 1
    assert rec == 1


def test_recap_needs_previous_tail():
    a = parse.build_episode(1, [{"start": 0.0, "text": "something else entirely here", "speaker": "A"},
                                {"start": 40.0, "text": "You will all regret this", "speaker": "A"}], CFG["parse"])
    b = parse.build_episode(2, [{"start": 0.0, "text": "You will all regret this", "speaker": "A"},
                                {"start": 20.0, "text": "You will all regret this", "speaker": "A"}], CFG["parse"])
    parse.mark_recaps([a, b], CFG["parse"])
    assert b["lines"][0]["is_recap"] is True
    # outside the recap window
    assert b["lines"][1]["is_recap"] is False


def test_multi_episode_file(tmp_path):
    p = tmp_path / "show.txt"
    p.write_text("EPISODE 1\n[00:00] A: one line here\nEPISODE 2\n[00:00] B: another line\n", encoding="utf-8")
    eps, failed = parse.parse_folder(str(tmp_path), CFG)
    assert [e["episode"] for e in eps] == [1, 2]
