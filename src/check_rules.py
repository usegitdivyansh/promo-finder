import os
import sys
import json
import argparse
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.common import load, load_dir, line_index
from src.parse import to_sec
from src.bible import resolve
from src.shots import quote_ok
from src import select as selector


def run(run_dir, cfg, forbid):
    eps = load_dir(os.path.join(run_dir, "01_parsed"))
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    bib = load(os.path.join(run_dir, "03_bible.json"))
    sel = load(os.path.join(run_dir, "08_selected.json"))["selected"]
    summ = load_dir(os.path.join(run_dir, "02_episodes"))
    bank = load_dir(os.path.join(run_dir, "04_shots"))
    res = {}

    # 1 recaps never used
    bad = []
    for s in summ:
        ep = eps_by[s["episode"]]
        idx = line_index(ep)
        for ev in s["key_events"]:
            if ep["lines"][idx[ev["line_id"]]]["is_recap"]:
                bad.append(f"event {ev['event_id']} is a recap line")
    for d in bank:
        for sh in d["shots"]:
            ls = eps_by[d["episode"]]["lines"]
            for j in range(sh["start_idx"], sh["end_idx"]+1):
                if ls[j]["is_recap"]:
                    bad.append(f"shot {sh['shot_id']} uses recap line")
                    break
    rec = 0
    for e in eps:
        for ln in e["lines"]:
            if ln["is_recap"]:
                rec += 1
    res["recaps_skipped"] = {"ok": not bad, "recap_lines": rec, "problems": bad}

    # 2 aliases merged
    speakers = {}
    lines_by = {}
    for e in eps:
        for ln in e["lines"]:
            if ln["speaker"]:
                speakers[ln["speaker"]] = resolve(ln["speaker"], bib["alias_map"])
                lines_by[ln["speaker"]] = lines_by.get(ln["speaker"], 0) + 1
    groups = {}
    for sp, cid in speakers.items():
        groups.setdefault(cid, []).append(sp)
    # unnamed walk-ons (GUEST, VOICE) may stay unresolved; recurring speakers may not
    miss = []
    for sp in groups.get(None, []):
        if lines_by[sp] >= 3:
            miss.append(sp)
    res["aliases"] = {"ok": not miss, "unresolved_recurring": miss, "groups": {str(k): sorted(v) for k, v in groups.items()},
                      "characters": [f"{c['char_id']} {c['name']} {c['aliases']}" for c in bib["characters"]]}

    # 3 no hallucinated quotes kept
    bad = []
    for d in bank:
        ls = eps_by[d["episode"]]["lines"]
        for sh in d["shots"]:
            ok, r = quote_ok(sh["quote_or_action"], ls[sh["key_idx"]], cfg["shots"]["quote_match"])
            if not ok:
                bad.append(f"{sh['shot_id']} ratio {r}")
    drops = []
    for f in os.listdir(os.path.join(run_dir, "logs")):
        if f.startswith("drops_"):
            drops.extend(load(os.path.join(run_dir, "logs", f)))
    bq = []
    for d in drops:
        if d["reason"] == "bad_quote":
            bq.append(d["item"] + ": " + d["detail"])
    res["quotes"] = {"ok": not bad, "kept_bad": bad, "dropped_bad_quotes": bq}

    # 4-7 per promo rules (duration, hook 3s, line boundaries, recap, overlap, order)
    bad = []
    durs = []
    for p in sel:
        errs = selector.verify(p, eps_by, cfg)
        if errs:
            bad.append(f"{p['final_id']}: {errs}")
        durs.append(p["total_duration_sec"])
    res["promo_rules"] = {"ok": not bad, "problems": bad, "durations": durs}

    # major twist payoff never shown (bible-based and ground-truth)
    ev_line = {}
    for s in summ:
        for ev in s["key_events"]:
            ev_line[ev["event_id"]] = (ev["episode"], ev["line_id"])
    targets = []
    for w in bib["twists"]:
        if w["severity"] == "major" and w["payoff_event_id"] in ev_line:
            targets.append(("bible " + w["twist_id"], ev_line[w["payoff_event_id"]]))
    for f in forbid:
        num, ts = f.split(":", 1)
        num = int(num)
        for ln in eps_by[num]["lines"]:
            if abs(ln["start"] - to_sec(ts)) < 0.01:
                targets.append(("ground truth " + f, (num, ln["id"])))
    bad = []
    for p in sel:
        for name, (num, lid) in targets:
            for s in p["segments"]:
                if s["episode"] != num:
                    continue
                j = line_index(eps_by[num])[lid]
                if s["a"] <= j <= s["b"]:
                    bad.append(f"{p['final_id']} shows {name} ({lid})")
    res["major_twist"] = {"ok": not bad, "checked": [f"{n}: {t}" for n, t in targets], "problems": bad,
                          "twists": [f"{w['twist_id']} [{w['severity']}] {w['description']} setup={w['setup_event_id']} payoff={w['payoff_event_id']}" for w in bib["twists"]]}

    # max per thread, unique hook shot
    cnt = {}
    hooks = {}
    for p in sel:
        t = p["main_thread"]
        if t:
            cnt[t] = cnt.get(t, 0) + 1
        hooks[p["hook_shot_id"]] = hooks.get(p["hook_shot_id"], 0) + 1
    over = []
    for t, c in cnt.items():
        if c > cfg["select"]["max_per_thread"]:
            over.append(t)
    rep = []
    for h, c in hooks.items():
        if c > 1:
            rep.append(h)
    res["diversity"] = {"ok": not over and not rep, "per_thread": cnt, "over": over, "reused_hooks": rep}
    allok = True
    for k, v in res.items():
        if v["ok"] is False:
            allok = False
    res["all_ok"] = allok
    with open(os.path.join(run_dir, "rule_check.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    for k, v in res.items():
        if k == "all_ok":
            continue
        print(f"{k:16} {'OK' if v['ok'] else 'FAIL'}")
    print("all rules ok" if allok else "SOME RULES FAILED, see rule_check.json")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--forbid", action="append", default=[], help="ground-truth spoiler line as EP:MM:SS, e.g. 4:01:47")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    run(a.run, cfg, a.forbid)


if __name__ == "__main__":
    main()
