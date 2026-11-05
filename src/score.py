import os
import random
from src import llm
from src.schemas import FullOut, FreshOut
from src.bible import compact
from src.parse import fmt_ts
from src.common import save, log_drop


def promo_text(p, eps_by, cfg, show_ep=True):
    res = [f"PROMO {p['promo_id']} ({round(p['total_duration_sec'])}s)"]
    t = 0.0
    for i, s in enumerate(p["segments"]):
        lines = eps_by[s["episode"]]["lines"]
        if show_ep:
            res.append(f"  SEGMENT {i+1} (episode {s['episode']}):")
        else:
            res.append(f"  SEGMENT {i+1}:")
        for j in range(s["a"], s["b"]+1):
            ln = lines[j]
            at = t + ln["start"] - lines[s["a"]]["start"]
            mark = "  "
            if at < cfg["promo"]["hook_window_sec"]:
                mark = ">>"
            who = ln["type"].upper()
            if ln["type"] == "dialogue":
                who = ln["speaker"] or "UNKNOWN"
            res.append(f"  {mark} [{fmt_ts(at)}] {who}: {ln['text']}")
        t += s["duration"]
    return "\n".join(res)


def judge(kind, promos, eps_by, bible, cfg, mock_key, failed):
    res = {}
    arr = list(promos)
    random.Random(cfg["judge"]["seed"]).shuffle(arr)
    n = cfg["judge"]["batch_size"]
    model = llm.model_for("judge")
    if not model:
        model = None
    schema = FullOut
    if kind == "judge_fresh":
        schema = FreshOut
    batches = []
    for i in range(0, len(arr), n):
        batches.append(arr[i:i+n])
    for bi, batch in enumerate(batches):
        ids = []
        txt = []
        for p in batch:
            ids.append(p["promo_id"])
            txt.append(promo_text(p, eps_by, cfg, show_ep=(kind == "judge_full")))
        def check(obj, ids=ids):
            for sc in obj.scores:
                sc.promo_id = sc.promo_id.replace("PROMO", "").strip()
            got = set()
            for s in obj.scores:
                got.add(s.promo_id)
            miss = []
            for i in ids:
                if i not in got:
                    miss.append(i)
            if miss:
                return [f"missing scores for promo_id {', '.join(miss)}"]
            return []
        vs = {"promos": "\n\n".join(txt)}
        if kind == "judge_full":
            vs["bible"] = compact(bible)
        try:
            obj = llm.call_llm(kind, vs, schema, model=model, check=check, mock_key=mock_key, step="07_" + kind)
        except Exception as ex:
            failed.append({"step": kind, "batch": ids, "error": str(ex)[:300]})
            print(f"  {kind} batch {bi+1} FAILED: {str(ex)[:120]}")
            continue
        for s in obj.scores:
            if s.promo_id in ids:
                res[s.promo_id] = s.model_dump()
        print(f"  {kind} batch {bi+1}/{len(batches)} ok")
    return res


def code_coherence(p):
    segs = p["segments"]
    if len(segs) < 2:
        return 10.0
    cnt = 0
    for i in range(len(segs)-1):
        x = segs[i]
        y = segs[i+1]
        share = False
        for t in x["threads"]:
            if t and t in y["threads"]:
                share = True
        for c in x["char_ids"]:
            if c in y["char_ids"]:
                share = True
        if share:
            cnt += 1
    return 10.0 * cnt / (len(segs)-1)


def duration_score(d, pc):
    lo = pc["min_sec"]
    hi = pc["max_sec"]
    tlo = pc["target_min_sec"]
    thi = pc["target_max_sec"]
    if tlo <= d <= thi:
        return 10.0
    if d < tlo:
        res = 5 + 5 * (d - lo) / (tlo - lo)
    else:
        res = 5 + 5 * (hi - d) / (hi - thi)
    if res < 0:
        res = 0.0
    return round(res, 2)


def compute(p, full, fresh, cfg):
    hi = p.get("hook_intensity")
    if hi is None:
        hi = full["hook"]
    hook_final = 0.7 * full["hook"] + 0.3 * hi
    coh_code = code_coherence(p)
    coh = 0.5 * full["coherence"] + 0.5 * coh_code
    dur = duration_score(p["total_duration_sec"], cfg["promo"])
    clarity = full["clarity"]
    clarity_src = "judge_full"
    if fresh is not None:
        clarity = fresh["clarity"]
        clarity_src = "judge_fresh"
    pen = p.get("spoiler_penalty", 0)
    total = 10 * (0.28*hook_final + 0.28*full["cliffhanger"] + 0.22*clarity + 0.17*coh + 0.05*dur) - pen
    sc = {"hook": round(hook_final, 2), "hook_judge": full["hook"], "cliffhanger": full["cliffhanger"], "clarity": clarity,
          "clarity_source": clarity_src, "clarity_full": full["clarity"], "coherence": round(coh, 2), "coherence_judge": full["coherence"],
          "coherence_code": round(coh_code, 2), "duration": dur, "spoiler_penalty": pen, "total": round(total, 2)}
    if fresh is not None:
        sc["would_watch_next"] = fresh["would_watch_next"]
    flags = list(p["flags"])
    for f in full.get("safety_flags", []):
        flags.append("safety:" + f)
    conf = "medium"
    if clarity < 5 or p.get("hook_needs_context") == "high":
        conf = "low"
    elif clarity >= 7 and not full.get("safety_flags") and p["source"] != "baseline":
        conf = "high"
    p["scores"] = sc
    p["hook"] = full["hook_text"]
    p["cliffhanger"] = full["cliffhanger_text"]
    p["why_it_works"] = full["why_it_works"]
    p["safety_flags"] = full.get("safety_flags", [])
    p["confusion"] = ""
    if fresh is not None:
        p["confusion"] = fresh["confusion"]
    p["flags"] = flags
    p["confidence"] = conf
    return p


def run(promos, eps, bible, cfg, out_dir, drops, failed, mock_key="all", name="07_scored.json"):
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    full = judge("judge_full", promos, eps_by, bible, cfg, mock_key, failed)
    fresh = {}
    if cfg["judge"].get("fresh", True):
        fresh = judge("judge_fresh", promos, eps_by, bible, cfg, mock_key, failed)
    res = []
    for p in promos:
        f = full.get(p["promo_id"])
        if f is None:
            log_drop(drops, "score", p["promo_id"], "judge_failed", "no judge_full score")
            continue
        fr = None
        if cfg["judge"].get("fresh", True):
            fr = fresh.get(p["promo_id"])
            if fr is None:
                log_drop(drops, "score", p["promo_id"], "fresh_missing", "no judge_fresh score, used judge_full clarity")
        res.append(compute(p, f, fr, cfg))
    res.sort(key=lambda x: -x["scores"]["total"])
    save(os.path.join(out_dir, name), res)
    print(f"  scored {len(res)}/{len(promos)}")
    return res
