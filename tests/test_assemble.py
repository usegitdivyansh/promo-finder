import os
import sys
import json
import copy
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import parse, assemble, shots, score, episodes, llm
from src import select as selector
from src.schemas import EpisodeOut, ShotsOut

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def make_ep(num, n, step=4.0):
    rows = []
    for i in range(n):
        rows.append({"start": i * step, "text": f"line {i} of episode {num} words", "speaker": "A"})
    return parse.build_episode(num, rows, CFG["parse"])


def shot(ep, sid, a, k, b, role, inten=8, thread="T1"):
    ls = ep["lines"]
    return {"shot_id": sid, "episode": ep["episode"], "start_idx": a, "key_idx": k, "end_idx": b, "role": role,
            "intensity": inten, "thread_id": thread, "char_ids": ["C1"], "needs_context": "low", "size": "medium",
            "start": ls[a]["start"], "end": ls[b]["end"], "duration": ls[b]["end"] - ls[a]["start"]}


def setup_world(n=20):
    e1 = make_ep(1, n)
    e2 = make_ep(2, n)
    eps_by = {1: e1, 2: e2}
    return e1, e2, eps_by


def cov_of(bank):
    res = {}
    for s in bank:
        res.setdefault(s["episode"], []).append((s["start_idx"], s["end_idx"], s["key_idx"], s["intensity"], s["role"]))
    return res


def cand(*ids, template="mini_story"):
    beats = []
    names = ["hook"] + ["escalation"] * (len(ids) - 2) + ["cliffhanger"]
    if len(ids) == 1:
        names = ["scene"]
    for nm, sid in zip(names, ids):
        beats.append({"beat": nm, "need": "", "event_id": None, "shot_id": sid})
    return {"cand_id": "T", "source": "script", "template": template, "idea": "", "beats": beats}


def test_hook_moved_into_window():
    e1, e2, eps_by = setup_world()
    # hook key at 8s after segment start, must move
    h = shot(e1, "h", 0, 2, 4, "hook")
    c = shot(e2, "c", 5, 8, 8, "cliffhanger")
    bank = [h, c]
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of(bank), [], CFG)
    assert p is not None
    first = p["segments"][0]
    assert first["start"] == e1["lines"][2]["start"]
    assert "hook_moved" in p["flags"]


def test_hook_kept_when_inside_window():
    e1, e2, eps_by = setup_world()
    h = shot(e1, "h", 0, 0, 5, "hook")
    c = shot(e2, "c", 5, 8, 8, "cliffhanger")
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c]), [], CFG)
    assert p["segments"][0]["start"] == 0.0
    assert "hook_moved" not in p["flags"]


def test_trim_escalation_first_and_keep_ends():
    e1, e2, eps_by = setup_world(30)
    h = shot(e1, "h", 0, 0, 4, "hook")          # 20s
    m = shot(e1, "m", 8, 12, 17, "escalation")  # 40s
    c = shot(e2, "c", 3, 5, 5, "cliffhanger")   # 12s
    p, why, det = assemble.assemble_one(cand("h", "m", "c"), {"h": h, "m": m, "c": c}, eps_by, cov_of([h, m, c]), [], CFG)
    assert p is not None
    assert p["total_duration_sec"] <= 60
    segs = p["segments"]
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == e1["lines"][4]["end"]
    assert segs[2]["end"] == e2["lines"][5]["end"]
    # key line of the escalation kept
    assert segs[1]["a"] <= 12 <= segs[1]["b"]
    assert "trimmed" in p["flags"]


def test_too_long_dropped():
    e1, e2, eps_by = setup_world(40)
    # touching shots merge into one 68s segment whose hook start and cliffhanger end can't be trimmed
    h = shot(e1, "h", 0, 0, 8, "hook")
    c = shot(e1, "c", 9, 16, 16, "cliffhanger")
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c]), [], CFG)
    assert p is None
    assert why == "too_long"


def test_extend_only_into_covered_lines():
    e1, e2, eps_by = setup_world()
    h = shot(e1, "h", 0, 0, 1, "hook")          # 8s
    c = shot(e2, "c", 0, 1, 1, "cliffhanger")   # 8s
    filler = shot(e1, "f", 2, 3, 4, "escalation", inten=6)
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c, filler]), [], CFG)
    assert p is not None
    assert p["total_duration_sec"] >= 20
    assert "extended" in p["flags"]
    # low intensity filler is not enough
    weak = shot(e1, "f", 2, 3, 4, "escalation", inten=4)
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c, weak]), [], CFG)
    assert p is None
    assert why == "too_short"


def test_spoiler_guard():
    e1, e2, eps_by = setup_world()
    h = shot(e1, "h", 0, 0, 4, "hook")
    c = shot(e2, "c", 0, 2, 4, "cliffhanger")
    major = [{"twist_id": "W1", "severity": "major", "episode": 2, "line_id": "E2_L3"}]
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c]), major, CFG)
    assert p is None
    assert why == "spoiler"
    minor = [{"twist_id": "W2", "severity": "minor", "episode": 2, "line_id": "E2_L3"}]
    p, why, det = assemble.assemble_one(cand("h", "c"), {"h": h, "c": c}, eps_by, cov_of([h, c]), minor, CFG)
    assert p["spoiler_penalty"] == 5


def test_merge_and_chronological():
    e1, e2, eps_by = setup_world()
    h = shot(e1, "h", 0, 0, 3, "hook")
    m = shot(e1, "m", 4, 5, 6, "escalation")   # touches h, merges
    c = shot(e2, "c", 0, 2, 3, "cliffhanger")
    p, why, det = assemble.assemble_one(cand("h", "m", "c"), {"h": h, "m": m, "c": c}, eps_by, cov_of([h, m, c]), [], CFG)
    assert len(p["segments"]) == 2
    # cliffhanger earlier than hook in story order
    p, why, det = assemble.assemble_one(cand("c", "h"), {"h": h, "c": c}, eps_by, cov_of([h, c]), [], CFG)
    assert p is None
    assert why == "not_chronological"


def test_duration_score():
    pc = CFG["promo"]
    assert score.duration_score(35, pc) == 10
    assert score.duration_score(20, pc) == 5
    assert score.duration_score(60, pc) == 5
    assert score.duration_score(25, pc) == 7.5


def test_quote_check():
    ln = {"text": "I'm the new owner of Blake Group.", "speaker": "EMMA"}
    ok, r = shots.quote_ok("I'm the new owner of Blake Group.", ln, 0.85)
    assert ok
    ok, r = shots.quote_ok("EMMA: I'm the new owner of Blake Group", ln, 0.85)
    assert ok
    ok, r = shots.quote_ok("Emma slaps Lily", ln, 0.85)
    assert not ok


def test_line_id_validation():
    ep = make_ep(4, 6)
    chk = episodes.make_check(ep)
    good = EpisodeOut(episode=4, summary="s", characters=[], key_events=[{"line_id": "E4_L1", "description": "x"}],
                      ending_cliffhanger={"line_id": "E4_L5", "description": "y"}, primary_label="reveal", peak_line_id="E4_L1")
    assert chk(good) == []
    bad = good.model_copy(update={"peak_line_id": "E4_L99"})
    assert chk(bad)
    sc = shots.make_check(ep)
    s = ShotsOut(episode=4, shots=[{"size": "small", "start_line_id": "E4_L3", "end_line_id": "E4_L1", "key_line_id": "E4_L2",
                                    "role": "hook", "intensity": 8, "needs_context": "low", "quote_or_action": "x"}])
    assert sc(s)


def test_mock_pipeline_end_to_end(tmp_path):
    import run
    cfg = copy.deepcopy(CFG)
    cfg["llm"]["mock"] = True
    cfg["_episodes"] = None
    out = str(tmp_path / "out")
    llm.setup(cfg, run_dir=out)
    run.run(os.path.join(ROOT, "tests", "fixtures", "sample_show"), out, cfg)
    promos = json.load(open(os.path.join(out, "09_final", "promos.json"), encoding="utf-8"))
    top = promos[0]
    assert top["template"] == "underdog_revenge"
    eps = []
    for s in top["segments"]:
        eps.append(s["episode"])
    assert eps == [1, 3, 3]
    assert top["segments"][0]["start"] == "00:04"
    assert 35 <= top["total_duration_sec"] <= 45
    drops = []
    for f in os.listdir(os.path.join(out, "logs")):
        if f.startswith("drops_"):
            drops.extend(json.load(open(os.path.join(out, "logs", f), encoding="utf-8")))
    reasons = set()
    for d in drops:
        reasons.add(d["reason"])
    for r in ["bad_quote", "recap", "spoiler", "invalid_script", "unfilled_beat", "too_short"]:
        assert r in reasons
    # every output promo passes the hard rules
    sel = json.load(open(os.path.join(out, "08_selected.json"), encoding="utf-8"))
    eps_by = {}
    for p in os.listdir(os.path.join(out, "01_parsed")):
        if p.startswith("ep_"):
            e = json.load(open(os.path.join(out, "01_parsed", p), encoding="utf-8"))
            eps_by[e["episode"]] = e
    for p in sel["selected"]:
        assert selector.verify(p, eps_by, cfg) == []
    hooks = []
    for p in sel["selected"]:
        hooks.append(p["hook_shot_id"])
    assert len(hooks) == len(set(hooks))


def test_quote_check_partial_and_parenthetical():
    ln = {"text": "Means nothing? I'm carrying your child, Arjun.", "speaker": "TANYA"}
    ok, r = shots.quote_ok("I'm carrying your child, Arjun.", ln, 0.85)
    assert ok
    ok, r = shots.quote_ok("child", ln, 0.85)
    assert not ok
    ok, r = shots.quote_ok("This can't be true.", {"text": "(whispering) This can't be true.", "speaker": "MIRA"}, 0.85)
    assert ok


def test_script_order_check():
    from src import scripts
    from src.schemas import Script
    evs = {"E1_ev1": {"episode": 1, "start": 10.0}, "E5_ev1": {"episode": 5, "start": 17.0}, "E5_ev2": {"episode": 5, "start": 33.0}}
    good = Script(script_id="C1", template="mini_story", idea="x", beats=[
        {"beat": "hook", "need": "", "event_id": "E1_ev1"}, {"beat": "escalation", "need": "", "event_id": "E5_ev1"},
        {"beat": "cliffhanger", "need": "", "event_id": "E5_ev2"}])
    assert scripts.order_err(good, evs) is None
    bad = good.model_copy(deep=True)
    bad.beats[1].event_id = "E5_ev2"
    bad.beats[2].event_id = "E5_ev1"
    assert scripts.order_err(bad, evs)


def test_episode_one_payoff_is_premise():
    from src.assemble import twist_lines
    from src.scripts import spoiler_events
    bib = {"twists": [{"twist_id": "W1", "severity": "major", "setup_event_id": "E1_ev1", "payoff_event_id": "E1_ev4"},
                      {"twist_id": "W2", "severity": "major", "setup_event_id": "E2_ev1", "payoff_event_id": "E3_ev2"}]}
    summ = [{"episode": 1, "key_events": [{"event_id": "E1_ev4", "episode": 1, "line_id": "E1_L9"}]},
            {"episode": 3, "key_events": [{"event_id": "E3_ev2", "episode": 3, "line_id": "E3_L5"}]}]
    assert [t["twist_id"] for t in twist_lines(bib, summ)] == ["W2"]
    assert spoiler_events(bib) == ["E3_ev2"]


def test_ending_shot_stays_in_scene_and_short():
    e = make_ep(3, 20, step=2.0)
    for ln in e["lines"][:12]:
        ln["scene_id"] = 1
    summ = {"ending_cliffhanger": {"line_id": e["lines"][18]["id"]}, "key_events": []}
    bib = {"threads": [], "alias_map": {}}
    s = shots.ending_shot(e, summ, [], bib, CFG)
    assert s["role"] == "cliffhanger"
    assert s["key_idx"] == 18
    # line 19 is dialogue, so the tail stops at the cliffhanger line
    assert s["end_idx"] == 18
    assert s["start_idx"] >= 12
    assert CFG["ending"]["min_sec"] <= s["duration"] <= CFG["ending"]["max_sec"]


def test_hook_ending_pairs_earlier_hook_sharing_thread():
    from src.scripts import hook_endings
    e1, e2, eps_by = setup_world()
    h = shot(e1, "E1_S1", 2, 2, 5, "hook", inten=9)
    end = shot(e2, "E2_END", 14, 18, 18, "cliffhanger", inten=8)
    end["source"] = "episode_ending"
    other = shot(e1, "E1_S2", 6, 6, 8, "hook", inten=9, thread="T9")
    other["char_ids"] = ["C7"]
    res = hook_endings({1: [h, other], 2: [end]}, CFG)
    assert len(res) == 1
    assert [b["shot_id"] for b in res[0]["beats"]] == ["E1_S1", "E2_END"]
    assert res[0]["template"] == "hook_ending"


def test_ending_scene_starts_at_hook_inside_scene():
    e = make_ep(4, 30, step=2.0)
    for i, ln in enumerate(e["lines"]):
        ln["scene_id"] = 1
        if i >= 10:
            ln["scene_id"] = 2
    summ = {"ending_cliffhanger": {"line_id": e["lines"][27]["id"]}, "key_events": []}
    bib = {"threads": [], "alias_map": {}}
    weak = shot(e, "E4_S1", 12, 13, 14, "hook", inten=6)
    strong = shot(e, "E4_S2", 15, 16, 17, "hook", inten=9)
    other_scene = shot(e, "E4_S3", 3, 4, 5, "hook", inten=10)
    for s in [weak, strong, other_scene]:
        s["hook_type"] = "shocking_line"
    s = shots.ending_scene_shot(e, summ, [weak, strong, other_scene], bib, CFG)
    assert s["hook_shot_id"] == "E4_S2"
    ls = e["lines"]
    assert ls[s["key_idx"]]["start"] - ls[s["start_idx"]]["start"] <= CFG["promo"]["hook_window_sec"]
    assert ls[s["start_idx"]]["scene_id"] == 2
    assert CFG["ending_scene"]["min_sec"] <= s["duration"] <= CFG["ending_scene"]["max_sec"]
