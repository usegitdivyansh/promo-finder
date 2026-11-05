# build a blind human rating sheet: top tool promos and top baseline clips, shuffled, unlabelled.
# writes eval/blind_sheet.html (open in a browser, export results) and eval/blind_key.json (keep closed).
# both are built from screenplay text, so eval/blind_* is git-ignored.
import os
import sys
import json
import random
import hashlib
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.common import load, save, load_dir


def item_lines(p, eps_by):
    # times are relative to the promo start (each cut continues where the last ended), so a clip
    # ending at the episode's end doesn't give itself away
    segs = []
    off = 0.0
    for s in p["segments"]:
        lines = eps_by[s["episode"]]["lines"]
        t0 = lines[s["a"]]["start"]
        rows = []
        for ln in lines[s["a"]:s["b"]+1]:
            rows.append({"t": round(ln["start"] - t0 + off, 1), "type": ln["type"], "speaker": ln["speaker"] or "",
                         "delivery": ln.get("delivery") or "", "text": ln["text"]})
        dur = lines[s["b"]]["end"] - t0
        segs.append({"episode": s["episode"], "duration": round(dur, 1), "lines": rows})
        off += dur
    return segs


def judge(p):
    sc = p["scores"]
    return {"hook": sc["hook"], "cliffhanger": sc["cliffhanger"], "clarity": sc["clarity"], "total": sc["total"]}


def build(run_dir, n, seed):
    eps_by = {}
    for e in load_dir(os.path.join(run_dir, "01_parsed")):
        eps_by[e["episode"]] = e
    sel = load(os.path.join(run_dir, "08_selected.json"))["selected"]
    sel = sorted(sel, key=lambda p: p["rank"])[:n]
    base = load(os.path.join(run_dir, "10_baseline.json"))["baseline_ranked"]
    base = sorted(base, key=lambda p: -p["scores"]["total"])[:n]
    pool = []
    for p in sel:
        pool.append(("tool", p["final_id"], p))
    for p in base:
        pool.append(("baseline", p["promo_id"], p))
    random.Random(seed).shuffle(pool)
    items = []
    key = {}
    for i, (src, pid, p) in enumerate(pool):
        bid = f"X{i+1:02d}"
        items.append({"id": bid, "duration": round(p["total_duration_sec"], 1), "segments": item_lines(p, eps_by)})
        key[bid] = {"source": src, "promo_id": pid, "internal_id": p["promo_id"], "template": p["template"], "judge": judge(p)}
    sid = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]
    return items, {"sheet_id": sid, "run_dir": run_dir, "seed": seed, "n_per_source": n, "items": key}


def page(items, sid):
    data = json.dumps({"sheet_id": sid, "items": items}, ensure_ascii=False).replace("</", "<\\/")
    tpl = open(os.path.join(ROOT, "src", "blind_sheet.html"), encoding="utf-8").read()
    return tpl.replace("/*DATA*/null", data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=os.path.join("raw_outputs", "show1"))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="eval")
    a = ap.parse_args()
    items, key = build(a.run, a.n, a.seed)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "blind_sheet.html"), "w", encoding="utf-8") as f:
        f.write(page(items, key["sheet_id"]))
    save(os.path.join(a.out, "blind_key.json"), key)
    print(f"wrote {len(items)} items, sheet {key['sheet_id']} -> {a.out}/blind_sheet.html and blind_key.json")


if __name__ == "__main__":
    main()
