import os
from src import score
from src.common import save


def build(eps, cfg):
    res = []
    last = cfg["baseline"]["last_sec"]
    for e in eps:
        lines = e["lines"]
        if not lines:
            continue
        b = len(lines) - 1
        end = lines[b]["end"]
        a = b
        best = None
        # snap start to the line boundary closest to last_sec before the end
        for i in range(len(lines)):
            d = end - lines[i]["start"]
            if best is None or abs(d - last) < abs(best - last):
                best = d
                a = i
        seg = {"episode": e["episode"], "start": lines[a]["start"], "end": end, "start_line_id": lines[a]["id"],
               "end_line_id": lines[b]["id"], "a": a, "b": b, "roles": ["scene"], "shot_ids": [], "threads": [],
               "char_ids": [], "hook_key": a, "duration": round(end - lines[a]["start"], 2)}
        res.append({"promo_id": f"B{e['episode']:02d}", "cand_id": f"B{e['episode']:02d}", "source": "baseline",
                    "template": "baseline_last30", "script_id": None, "idea": f"last {last}s of ep {e['episode']}",
                    "beats": [], "segments": [seg], "total_duration_sec": seg["duration"], "hook_shot_id": None,
                    "hook_intensity": None, "hook_needs_context": None, "main_thread": None, "spoiler_penalty": 0, "flags": []})
    return res


def mean(arr):
    if not arr:
        return 0.0
    return round(sum(arr) / len(arr), 2)


def major_payoffs(bible, out_dir):
    from src.common import load_dir
    from src.assemble import twist_lines
    summ = load_dir(os.path.join(out_dir, "02_episodes"))
    res = []
    for t in twist_lines(bible, summ):
        if t["severity"] == "major":
            res.append(t)
    return res


def run(eps, bible, selected, cfg, out_dir, drops, failed):
    promos = build(eps, cfg)
    # the naive rule ignores spoilers; mark clips that show a major twist payoff so the comparison can be fair
    pays = major_payoffs(bible, out_dir)
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    for p in promos:
        s = p["segments"][0]
        lines = eps_by[s["episode"]]["lines"]
        for t in pays:
            if t["episode"] != s["episode"]:
                continue
            for j in range(s["a"], s["b"]+1):
                if lines[j]["id"] == t["line_id"]:
                    p["flags"].append("shows_major_payoff")
    scored = score.run(promos, eps, bible, cfg, out_dir, drops, failed, mock_key="baseline", name="10_baseline_scored.json")
    top_tool = []
    for p in selected[:15]:
        top_tool.append(p["scores"]["total"])
    top_base = []
    for p in scored[:15]:
        top_base.append(p["scores"]["total"])
    keys = ["hook", "cliffhanger", "clarity", "coherence", "total"]
    comp = {"n_tool": len(top_tool), "n_baseline": len(top_base), "mean_total_tool_top15": mean(top_tool),
            "mean_total_baseline_top15": mean(top_base), "by_metric": {}}
    for k in keys:
        a = []
        for p in selected[:15]:
            a.append(p["scores"][k])
        b = []
        for p in scored[:15]:
            b.append(p["scores"][k])
        comp["by_metric"][k] = {"tool": mean(a), "baseline": mean(b)}
    comp["tool_wins"] = comp["mean_total_tool_top15"] > comp["mean_total_baseline_top15"]
    fair = []
    spoil = []
    for p in scored:
        if "shows_major_payoff" in p["flags"]:
            spoil.append(p["promo_id"])
        else:
            fair.append(p["scores"]["total"])
    comp["baseline_showing_major_payoff"] = spoil
    comp["mean_total_baseline_top15_no_spoilers"] = mean(fair[:15])
    comp["tool_wins_vs_no_spoiler_baseline"] = comp["mean_total_tool_top15"] > comp["mean_total_baseline_top15_no_spoilers"]
    save(os.path.join(out_dir, "10_baseline.json"), {"comparison": comp, "baseline_ranked": scored})
    print(f"  baseline top15 mean {comp['mean_total_baseline_top15']} vs tool {comp['mean_total_tool_top15']}")
    return comp
