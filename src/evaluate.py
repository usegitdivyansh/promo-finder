import os
import sys
import csv
import json
import random
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.parse import to_sec, fmt_ts
from src.common import load, load_dir
from src.report import run_summary, seg_lines


def as_sec(v):
    if isinstance(v, (int, float)):
        return float(v)
    return to_sec(str(v))


def spans(segs):
    res = {}
    for s in segs:
        a = s.get("start_sec")
        b = s.get("end_sec")
        if a is None:
            a = as_sec(s["start"])
        if b is None:
            b = as_sec(s["end"])
        res.setdefault(int(s["episode"]), []).append((a, b))
    for k in res:
        res[k] = union(res[k])
    return res


def union(arr):
    arr = sorted(arr)
    res = []
    for a, b in arr:
        if res and a <= res[-1][1]:
            if b > res[-1][1]:
                res[-1] = (res[-1][0], b)
        else:
            res.append((a, b))
    return res


def length(sp):
    res = 0.0
    for k in sp:
        for a, b in sp[k]:
            res += b - a
    return res


def iou(x, y):
    inter = 0.0
    for k in x:
        if k not in y:
            continue
        for a, b in x[k]:
            for c, d in y[k]:
                lo = max(a, c)
                hi = min(b, d)
                if hi > lo:
                    inter += hi - lo
    uni = length(x) + length(y) - inter
    if uni <= 0:
        return 0.0
    return inter / uni


def gold_eval(gold, finals, scored):
    res = []
    for g in gold:
        gs = spans(g["segments"])
        best = 0.0
        best_id = None
        for p in finals:
            v = iou(gs, spans(p["segments"]))
            if v > best:
                best = v
                best_id = p["promo_id"]
        any_best = 0.0
        for p in scored:
            v = iou(gs, spans(p["segments"]))
            if v > any_best:
                any_best = v
        res.append({"gold_id": g.get("gold_id", ""), "best_iou": round(best, 3), "best_promo": best_id,
                    "hit": best >= 0.5, "best_iou_any_candidate": round(any_best, 3)})
    return res


def rank_list(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    res = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j+1 < len(order) and vals[order[j+1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j+1):
            res[order[k]] = r
        i = j+1
    return res


def spearman(x, y):
    n = len(x)
    if n < 3:
        return None
    rx = rank_list(x)
    ry = rank_list(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = 0.0
    dx = 0.0
    dy = 0.0
    for i in range(n):
        num += (rx[i]-mx) * (ry[i]-my)
        dx += (rx[i]-mx) ** 2
        dy += (ry[i]-my) ** 2
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx*dy) ** 0.5, 3)


def make_sheets(run_dir, n=10):
    eps = load_dir(os.path.join(run_dir, "01_parsed"))
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    sel = load(os.path.join(run_dir, "08_selected.json"))["selected"][:n]
    base = []
    p = os.path.join(run_dir, "10_baseline.json")
    if os.path.exists(p):
        base = load(p)["baseline_ranked"][:n]
    items = []
    for p in sel:
        items.append(("tool", p))
    for p in base:
        items.append(("baseline", p))
    random.Random(11).shuffle(items)
    d = os.path.join(run_dir, "eval")
    os.makedirs(d, exist_ok=True)
    key = {}
    rows = []
    html = ["<!doctype html><meta charset='utf-8'><title>Blind hand check</title><style>body{font:15px system-ui;max-width:860px;margin:auto;padding:16px}pre{white-space:pre-wrap;background:#f4f4f2;padding:8px}</style>",
            "<h1>Blind hand check</h1><p>Read each clip as a new viewer. In hand_check.csv mark hook_pass / cliffhanger_pass / standalone_pass with 1 or 0.</p>"]
    for i, (src, p) in enumerate(items):
        hid = f"H{i+1:02d}"
        key[hid] = {"source": src, "promo_id": p["promo_id"], "final_id": p.get("final_id"), "total": p["scores"]["total"]}
        segs = []
        for s in p["segments"]:
            segs.append(f"ep{s['episode']} {fmt_ts(s['start'])}-{fmt_ts(s['end'])}")
        rows.append([hid, " | ".join(segs), round(p["total_duration_sec"]), "", "", "", ""])
        html.append(f"<h2>{hid} ({round(p['total_duration_sec'])}s)</h2>")
        for sg in seg_lines(p, eps_by):
            html.append(f"<b>{sg['label']}</b><pre>")
            for ln in sg["lines"]:
                html.append(f"[{ln['ts']}] {ln['who']}: {ln['text']}")
            html.append("</pre>")
    with open(os.path.join(d, "hand_check.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["item", "segments", "duration_sec", "hook_pass", "cliffhanger_pass", "standalone_pass", "notes"])
        w.writerows(rows)
    with open(os.path.join(d, "hand_key.json"), "w", encoding="utf-8") as f:
        json.dump(key, f, indent=1)
    with open(os.path.join(d, "hand_check.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    rows = []
    for p in sel:
        rows.append([p["final_id"], p["template"], " | ".join(f"ep{s['episode']} {fmt_ts(s['start'])}-{fmt_ts(s['end'])}" for s in p["segments"]), ""])
    random.Random(5).shuffle(rows)
    with open(os.path.join(d, "human_rank.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["promo_id", "template", "segments", "human_rank"])
        w.writerows(rows)
    print(f"wrote {d}/hand_check.csv, hand_check.html, human_rank.csv (fill them, then run evaluate again)")


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def hand_eval(run_dir):
    d = os.path.join(run_dir, "eval")
    rows = read_csv(os.path.join(d, "hand_check.csv"))
    kp = os.path.join(d, "hand_key.json")
    if not rows or not os.path.exists(kp):
        return None, []
    key = load(kp)
    res = {}
    fails = []
    for r in rows:
        if not r["hook_pass"].strip():
            continue
        src = key[r["item"]]["source"]
        st = res.setdefault(src, {"n": 0, "hook": 0, "cliffhanger": 0, "standalone": 0, "all3": 0})
        st["n"] += 1
        vals = []
        for k in ["hook", "cliffhanger", "standalone"]:
            v = r[k + "_pass"].strip() in ["1", "y", "yes", "pass", "true"]
            vals.append(v)
            if v:
                st[k] += 1
        if all(vals):
            st["all3"] += 1
        elif src == "tool":
            fails.append({"item": r["item"], "promo": key[r["item"]].get("final_id"), "segments": r["segments"],
                          "failed": [k for k, v in zip(["hook", "cliffhanger", "standalone"], vals) if not v], "notes": r["notes"]})
    for src, st in res.items():
        for k in ["hook", "cliffhanger", "standalone", "all3"]:
            st[k + "_rate"] = round(st[k] / st["n"], 2)
    return res, fails


def rank_eval(run_dir):
    rows = read_csv(os.path.join(run_dir, "eval", "human_rank.csv"))
    sel = load(os.path.join(run_dir, "08_selected.json"))["selected"]
    judge = {}
    for p in sel:
        judge[p["final_id"]] = p["rank"]
    x = []
    y = []
    for r in rows:
        if r["human_rank"].strip() and r["promo_id"] in judge:
            x.append(judge[r["promo_id"]])
            y.append(float(r["human_rank"]))
    return spearman(x, y), len(x)


def run(run_dir, gold_path, out_path):
    sel = load(os.path.join(run_dir, "08_selected.json"))
    scored = load(os.path.join(run_dir, "07_scored.json"))
    finals = load(os.path.join(run_dir, "09_final", "promos.json"))
    cfg = {"llm": {}}
    summ = run_summary(run_dir, cfg, sel)
    out = ["# Evaluation results", "", f"Run: `{os.path.basename(os.path.normpath(run_dir))}`", ""]
    out.append("## 1. Gold set (time IoU >= 0.5)")
    if os.path.exists(gold_path):
        gold = load(gold_path)
        ge = gold_eval(gold, finals, scored)
        hits = 0
        for g in ge:
            if g["hit"]:
                hits += 1
        out.append(f"**{hits}/{len(ge)} gold promos hit** by the final list.")
        out.append("")
        out.append("| gold | best IoU (final list) | promo | best IoU (any scored candidate) |")
        out.append("|---|---|---|---|")
        for g in ge:
            out.append(f"| {g['gold_id']} | {g['best_iou']} | {g['best_promo']} | {g['best_iou_any_candidate']} |")
    else:
        out.append(f"Pending: add hand-picked promos to `{os.path.relpath(gold_path, ROOT)}` (see eval/gold.example.json).")
    out.append("")
    out.append("## 2. Hand check of the top 10 (blind, tool vs baseline)")
    he, fails = hand_eval(run_dir)
    if he:
        out.append("| source | n | hook | cliffhanger | standalone | all 3 |")
        out.append("|---|---|---|---|---|---|")
        for src, st in he.items():
            out.append(f"| {src} | {st['n']} | {st['hook_rate']} | {st['cliffhanger_rate']} | {st['standalone_rate']} | {st['all3_rate']} |")
    else:
        out.append("Pending: run `python -m src.evaluate --run <dir> --make-sheets`, fill `eval/hand_check.csv` in the run folder.")
    out.append("")
    out.append("## 3. Judge vs human ranking")
    rho, n = rank_eval(run_dir)
    if rho is not None:
        out.append(f"Spearman correlation between judge rank and human rank: **{rho}** (n={n}).")
    else:
        out.append("Pending: fill `human_rank` in `eval/human_rank.csv` in the run folder (1 = best).")
    out.append("")
    out.append("## 4. Fill rate")
    out.append(f"{summ.get('scripts_filled', 0)}/{summ.get('scripts_written', 0)} scripts fully filled (fill rate {summ.get('fill_rate', 0)}).")
    out.append("")
    out.append("## 5. Drops by reason")
    out.append("| step:reason | count |")
    out.append("|---|---|")
    for k, v in summ["drops_by_reason"].items():
        out.append(f"| {k} | {v} |")
    out.append("")
    p = os.path.join(run_dir, "10_baseline.json")
    if os.path.exists(p):
        c = load(p)["comparison"]
        out.append("## Baseline (last 30 s of each episode), same judges")
        out.append(f"Mean total, top 15: tool **{c['mean_total_tool_top15']}** vs baseline **{c['mean_total_baseline_top15']}**.")
        out.append("")
        out.append("| metric | tool | baseline |")
        out.append("|---|---|---|")
        for k, v in c["by_metric"].items():
            out.append(f"| {k} | {v['tool']} | {v['baseline']} |")
        out.append("")
    out.append("## 6. Failure gallery")
    if fails:
        for f in fails:
            out.append(f"- **{f['promo']}** ({f['segments']}): failed {', '.join(f['failed'])}. {f['notes']}")
    else:
        out.append("From the hand check (pending). Auto-flagged suspects from the run:")
    sus = []
    for p in sel["selected"]:
        why = []
        if p["scores"]["clarity"] < 6:
            why.append(f"low fresh-viewer clarity {p['scores']['clarity']}")
        if p.get("confusion") and p["confusion"].lower() not in ["nothing", "none", ""]:
            why.append(f"fresh viewer: {p['confusion']}")
        if p.get("hook_needs_context") == "high":
            why.append("hook needs context")
        if "hook_unverified" in p["flags"]:
            why.append("no hook shot inside the scene")
        if why:
            sus.append(f"- {p['final_id']} ({p['template']}): {'; '.join(why)}")
    out.extend(sus[:6])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run output folder, e.g. raw_outputs/show1")
    ap.add_argument("--gold", default=os.path.join(ROOT, "eval", "gold.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results.md"))
    ap.add_argument("--make-sheets", action="store_true", help="write blind hand-check and ranking sheets into <run>/eval/")
    a = ap.parse_args()
    if a.make_sheets:
        make_sheets(a.run)
        return
    run(a.run, a.gold, a.out)


if __name__ == "__main__":
    main()
