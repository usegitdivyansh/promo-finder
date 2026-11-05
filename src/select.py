import os
from src.common import save, log_drop
from src.schemas import TEMPLATES


def verify(p, eps_by, cfg):
    pc = cfg["promo"]
    errs = []
    d = p["total_duration_sec"]
    if d < pc["min_sec"] - 0.01 or d > pc["max_sec"] + 0.01:
        errs.append(f"duration {d}s")
    segs = p["segments"]
    first = segs[0]
    lines = eps_by[first["episode"]]["lines"]
    if lines[first["hook_key"]]["start"] - lines[first["a"]]["start"] > pc["hook_window_sec"]:
        errs.append("hook not in first 3s")
    for i, s in enumerate(segs):
        ls = eps_by[s["episode"]]["lines"]
        if abs(ls[s["a"]]["start"] - s["start"]) > 0.01 or abs(ls[s["b"]]["end"] - s["end"]) > 0.01:
            errs.append(f"segment {i+1} not on line boundary")
        for j in range(s["a"], s["b"]+1):
            if ls[j]["is_recap"]:
                errs.append(f"segment {i+1} uses recap line")
                break
        for k in range(i+1, len(segs)):
            t = segs[k]
            if t["episode"] == s["episode"] and t["a"] <= s["b"] and s["a"] <= t["b"]:
                errs.append(f"segments {i+1} and {k+1} overlap")
        if p["template"] != "montage" and i+1 < len(segs):
            t = segs[i+1]
            if (t["episode"], t["a"]) < (s["episode"], s["a"]):
                errs.append("not chronological")
    return errs


def run(scored, eps, cfg, out_dir, drops):
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    pc = cfg["promo"]
    sc = cfg["select"]
    ok = []
    for p in scored:
        errs = verify(p, eps_by, cfg)
        if errs:
            log_drop(drops, "select", p["promo_id"], "rule_violation", "; ".join(errs))
            continue
        ok.append(p)
    ok.sort(key=lambda x: -x["scores"]["total"])
    picked = []
    used = set()
    hooks = set()
    threads = {}

    def can_take(p):
        if p["promo_id"] in used:
            return False
        if p["hook_shot_id"] in hooks:
            return False
        t = p["main_thread"]
        if t and threads.get(t, 0) >= sc["max_per_thread"]:
            return False
        return True

    def take(p, why):
        picked.append(p)
        used.add(p["promo_id"])
        hooks.add(p["hook_shot_id"])
        t = p["main_thread"]
        if t:
            threads[t] = threads.get(t, 0) + 1
        p["picked_for"] = why

    # category coverage first, templates in order of their best score so a weaker
    # template can't claim a hook shot that a stronger promo needs
    order = []
    for p in ok:
        if p["template"] not in order:
            order.append(p["template"])
    for t in order:
        for p in ok:
            if p["template"] != t:
                continue
            if p["scores"]["total"] < sc["min_score_for_category"]:
                break
            if can_take(p):
                take(p, "best_of_template")
                break
    for p in ok:
        if len(picked) >= pc["num_promos_max"]:
            break
        if can_take(p):
            take(p, "score")
    picked = picked[:pc["num_promos_max"]]
    picked.sort(key=lambda x: -x["scores"]["total"])
    for i, p in enumerate(picked):
        p["rank"] = i+1
        p["final_id"] = f"P{i+1:02d}"
    best = {}
    for t in TEMPLATES:
        for p in ok:
            if p["template"] == t:
                best[t] = {"promo_id": p["promo_id"], "total": p["scores"]["total"], "selected": p["promo_id"] in used}
                for q in picked:
                    if q["promo_id"] == p["promo_id"]:
                        best[t]["final_id"] = q["final_id"]
                break
    note = ""
    if len(picked) < pc["num_promos_min"]:
        note = f"only {len(picked)} promos passed the rules and diversity limits (min is {pc['num_promos_min']}); the quality bar was not lowered"
        print("  " + note)
    res = {"selected": picked, "best_per_template": best, "note": note, "eligible": len(ok)}
    save(os.path.join(out_dir, "08_selected.json"), res)
    print(f"  selected {len(picked)} of {len(ok)} eligible")
    return res
