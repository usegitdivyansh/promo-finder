import os
import difflib
from src import llm
from src.schemas import ShotsOut
from src.parse import norm
from src.bible import compact, resolve
from src.common import save, fmt_lines, line_index, log_drop


def make_check(ep):
    idx = line_index(ep)
    def check(obj):
        errs = []
        if obj.episode != ep["episode"]:
            errs.append(f"episode must be {ep['episode']}")
        for i, s in enumerate(obj.shots):
            for k in [s.start_line_id, s.key_line_id, s.end_line_id]:
                if k not in idx:
                    errs.append(f"shot {i+1}: line id {k} does not exist in this episode")
            if s.start_line_id in idx and s.key_line_id in idx and s.end_line_id in idx:
                if not (idx[s.start_line_id] <= idx[s.key_line_id] <= idx[s.end_line_id]):
                    errs.append(f"shot {i+1}: need start <= key <= end")
        return errs
    return check


def quote_ok(quote, ln, thr):
    q = norm(quote)
    best = difflib.SequenceMatcher(None, q, norm(ln["text"])).ratio()
    if ln["speaker"]:
        temp = difflib.SequenceMatcher(None, q, norm(ln["speaker"] + " " + ln["text"])).ratio()
        if temp > best:
            best = temp
    return best >= thr, round(best, 3)


def size_for(dur, sc):
    if dur <= (sc["small"][1] + sc["medium"][0]) / 2:
        return "small"
    if dur <= (sc["medium"][1] + sc["long"][0]) / 2:
        return "medium"
    return "long"


def clean(obj, ep, summ, bible, cfg, drops):
    sc = cfg["shots"]
    idx = line_index(ep)
    num = ep["episode"]
    lines = ep["lines"]
    tids = set()
    for t in bible["threads"]:
        tids.add(t["thread_id"])
    res = []
    for i, s in enumerate(obj.shots):
        sid = f"E{num}_S{i+1}"
        s = s.model_dump()
        s["shot_id"] = sid
        if s["start_line_id"] not in idx or s["key_line_id"] not in idx or s["end_line_id"] not in idx:
            log_drop(drops, "shots", sid, "invalid_id", f"{s['start_line_id']}..{s['end_line_id']}")
            continue
        a = idx[s["start_line_id"]]
        k = idx[s["key_line_id"]]
        b = idx[s["end_line_id"]]
        if not (a <= k <= b):
            log_drop(drops, "shots", sid, "invalid_id", "start/key/end out of order")
            continue
        ok, ratio = quote_ok(s["quote_or_action"], lines[k], sc["quote_match"])
        if not ok:
            log_drop(drops, "shots", sid, "bad_quote", f"ratio {ratio}: '{s['quote_or_action'][:80]}' vs '{lines[k]['text'][:80]}'")
            continue
        rec = False
        for j in range(a, b+1):
            if lines[j]["is_recap"]:
                rec = True
        if rec:
            log_drop(drops, "shots", sid, "recap", f"{s['start_line_id']}..{s['end_line_id']}")
            continue
        if s["intensity"] < sc["min_intensity"]:
            log_drop(drops, "shots", sid, "low_intensity", str(s["intensity"]))
            continue
        # snap to line boundaries
        start = lines[a]["start"]
        end = lines[b]["end"]
        dur = round(end - start, 2)
        if dur < sc["small"][0]:
            log_drop(drops, "shots", sid, "shot_too_short", f"{dur}s")
            continue
        if dur > sc["long"][1]:
            log_drop(drops, "shots", sid, "shot_too_long", f"{dur}s")
            continue
        size = size_for(dur, sc)
        if size != s["size"]:
            s["relabeled_from"] = s["size"]
            s["size"] = size
        cids = []
        names = []
        for n in s["characters"]:
            c = resolve(n, bible["alias_map"])
            if c and c not in cids:
                cids.append(c)
            names.append(n)
        # also count speakers inside the shot
        for j in range(a, b+1):
            if lines[j]["speaker"]:
                c = resolve(lines[j]["speaker"], bible["alias_map"])
                if c and c not in cids:
                    cids.append(c)
        s["char_ids"] = cids
        s["characters"] = names
        if s["thread_id"] not in tids:
            s["thread_id"] = None
        s["episode"] = num
        s["start_idx"] = a
        s["key_idx"] = k
        s["end_idx"] = b
        s["start"] = start
        s["end"] = end
        s["duration"] = dur
        s["quote_ratio"] = ratio
        res.append(s)
    # per-size caps, keep highest intensity
    out = []
    for size in ["long", "medium", "small"]:
        temp = []
        for s in res:
            if s["size"] == size:
                temp.append(s)
        temp.sort(key=lambda x: -x["intensity"])
        cap = sc["max_" + size]
        for s in temp[cap:]:
            log_drop(drops, "shots", s["shot_id"], "over_size_cap", size)
        out.extend(temp[:cap])
    out.sort(key=lambda x: (x["start_idx"], x["end_idx"]))
    return out


def episode_info(summ):
    res = [f"labels: {summ['primary_label']} {summ['secondary_labels']}", f"summary: {summ['summary']}", "key events:"]
    for ev in summ["key_events"]:
        res.append(f"  {ev['event_id']} at {ev['line_id']}: {ev['description']}")
    res.append(f"ending cliffhanger at {summ['ending_cliffhanger']['line_id']}: {summ['ending_cliffhanger']['description']}")
    return "\n".join(res)


def run(eps, summ_by_ep, bible, cfg, out_dir, drops, failed):
    res = {}
    bt = compact(bible)
    for ep in eps:
        num = ep["episode"]
        if num not in summ_by_ep:
            continue
        summ = summ_by_ep[num]
        try:
            obj = llm.call_llm("shots", {"bible": bt, "episode_info": episode_info(summ), "episode": str(num),
                                         "lines": fmt_lines(ep["lines"]), "min_intensity": str(cfg["shots"]["min_intensity"])},
                               ShotsOut, check=make_check(ep), mock_key=f"ep_{num:02d}", step="04_shots")
        except Exception as ex:
            failed.append({"step": "shots", "episode": num, "error": str(ex)[:300]})
            print(f"  ep {num}: FAILED {str(ex)[:120]}")
            continue
        shots = clean(obj, ep, summ, bible, cfg, drops)
        save(os.path.join(out_dir, "04_shots", f"ep_{num:02d}.json"), {"episode": num, "raw_count": len(obj.shots), "shots": shots})
        res[num] = shots
        temp = []
        for s in shots:
            temp.append(f"{s['size'][0]}{s['intensity']}")
        print(f"  ep {num}: {len(shots)}/{len(obj.shots)} shots kept {' '.join(temp)}")
    return res
