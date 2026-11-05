import os
import re
import difflib
from src import llm
from src.schemas import ShotsOut
from src.parse import norm
from src.bible import compact, resolve
from src.common import save, fmt_lines, line_index, log_drop


ROLE_RANK = {"cliffhanger": 0, "hook": 1, "payoff": 2, "escalation": 3}


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
    # parentheticals like "(whispering)" are stage directions, not part of the quote
    bare = norm(re.sub(r"\([^)]*\)", " ", ln["text"]))
    cands = [norm(ln["text"]), bare]
    if ln["speaker"]:
        cands.append(norm(ln["speaker"] + " " + ln["text"]))
    best = 0.0
    for c in cands:
        temp = difflib.SequenceMatcher(None, q, c).ratio()
        if temp > best:
            best = temp
        # an exact piece of the line that covers most of it is a real quote, not a hallucination
        if q and q in c and len(q) >= 0.5 * len(c):
            best = 1.0
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
        # highest intensity first; on ties keep the scarcer role (promos need cliffhangers to end on)
        temp.sort(key=lambda x: (-x["intensity"], ROLE_RANK[x["role"]], x["start_idx"]))
        cap = sc["max_" + size]
        for s in temp[cap:]:
            log_drop(drops, "shots", s["shot_id"], "over_size_cap", size)
        out.extend(temp[:cap])
    out.sort(key=lambda x: (x["start_idx"], x["end_idx"]))
    return out


def ending_bounds(ep, summ, tail_sec):
    # the cliffhanger line and the reaction or sound blocks right after it
    lines = ep["lines"]
    idx = line_index(ep)
    k = idx.get(summ["ending_cliffhanger"]["line_id"])
    if k is None:
        for j in range(len(lines)-1, -1, -1):
            if lines[j]["type"] in ["dialogue", "action"]:
                k = j
                break
    if k is None or lines[k]["is_recap"]:
        return None, None
    b = k
    while b+1 < len(lines) and lines[b+1]["type"] != "dialogue" and lines[b+1]["start"] - lines[k]["start"] <= tail_sec:
        b += 1
    return k, b


def ending_shot(ep, summ, shots, bible, cfg):
    # the episode's own closing beat as a cliffhanger shot: the cliffhanger line and its reaction,
    # going back only inside the same scene (not the baseline's fixed last 30 s)
    ec = cfg["ending"]
    lines = ep["lines"]
    k, b = ending_bounds(ep, summ, ec["tail_sec"])
    if k is None:
        return None
    a = k
    while lines[b]["end"] - lines[a]["start"] < ec["min_sec"]:
        if a == 0:
            break
        p = lines[a-1]
        if p["scene_id"] != lines[k]["scene_id"] or p["is_recap"] or p["type"] == "scene":
            break
        if lines[b]["end"] - p["start"] > ec["max_sec"]:
            break
        a -= 1
    dur = round(lines[b]["end"] - lines[a]["start"], 2)
    sc = cfg["shots"]
    if dur < sc["small"][0]:
        return None
    inten = ec["default_intensity"]
    thread = None
    for s in shots:
        if s["start_idx"] <= k <= s["end_idx"]:
            if s["intensity"] > inten or thread is None:
                inten = max(inten, s["intensity"])
                thread = s["thread_id"]
    if thread is None and summ["key_events"]:
        last = summ["key_events"][-1]["event_id"]
        for t in bible["threads"]:
            if last in t["key_event_ids"]:
                thread = t["thread_id"]
    cids = []
    for j in range(a, b+1):
        if lines[j]["speaker"]:
            c = resolve(lines[j]["speaker"], bible["alias_map"])
            if c and c not in cids:
                cids.append(c)
    num = ep["episode"]
    return {"shot_id": f"E{num}_END", "source": "episode_ending", "size": size_for(dur, sc), "role": "cliffhanger", "hook_type": None,
            "intensity": inten, "needs_context": "medium", "characters": [], "char_ids": cids, "thread_id": thread,
            "start_line_id": lines[a]["id"], "key_line_id": lines[k]["id"], "end_line_id": lines[b]["id"],
            "quote_or_action": lines[k]["text"], "quote_ratio": 1.0, "episode": num, "start_idx": a, "key_idx": k, "end_idx": b,
            "start": lines[a]["start"], "end": lines[b]["end"], "duration": dur}


def ending_scene_shot(ep, summ, shots, bible, cfg):
    # a single-scene promo ending on the episode's cliffhanger: start inside the same scene where a strong
    # line (key line of a shot with intensity >= hook_min_intensity) lands within the hook window
    es = cfg["ending_scene"]
    lines = ep["lines"]
    k, b = ending_bounds(ep, summ, cfg["ending"]["tail_sec"])
    if k is None:
        return None
    scene = lines[k]["scene_id"]
    win = cfg["promo"]["hook_window_sec"]
    mid = (es["min_sec"] + es["max_sec"]) / 2
    best = None
    best_key = None
    for h in shots:
        if h.get("source"):
            continue
        i = h["key_idx"]
        if h["intensity"] < es["hook_min_intensity"] or i >= k:
            continue
        if lines[i]["scene_id"] != scene or lines[i]["is_recap"]:
            continue
        # earliest start in the scene that still lands the hook within the window
        a = i
        while a > 0:
            p = lines[a-1]
            if p["scene_id"] != scene or p["type"] == "scene" or p["is_recap"]:
                break
            if lines[i]["start"] - p["start"] > win:
                break
            a -= 1
        dur = lines[b]["end"] - lines[a]["start"]
        if dur < es["min_sec"] or dur > es["max_sec"]:
            continue
        key = (-h["intensity"], abs(dur - mid), a)
        if best_key is None or key < best_key:
            best = (a, h)
            best_key = key
    if best is None:
        return None
    a, h = best
    cids = []
    for j in range(a, b+1):
        if lines[j]["speaker"]:
            c = resolve(lines[j]["speaker"], bible["alias_map"])
            if c and c not in cids:
                cids.append(c)
    dur = round(lines[b]["end"] - lines[a]["start"], 2)
    num = ep["episode"]
    return {"shot_id": f"E{num}_ESC", "source": "ending_scene", "size": "long", "role": "scene", "hook_type": h["hook_type"],
            "intensity": h["intensity"], "needs_context": h["needs_context"], "characters": [], "char_ids": cids,
            "thread_id": h["thread_id"], "hook_shot_id": h["shot_id"], "start_line_id": lines[a]["id"],
            "key_line_id": lines[h["key_idx"]]["id"], "end_line_id": lines[b]["id"], "quote_or_action": lines[h["key_idx"]]["text"],
            "quote_ratio": 1.0, "episode": num, "start_idx": a, "key_idx": h["key_idx"], "end_idx": b,
            "start": lines[a]["start"], "end": lines[b]["end"], "duration": dur}


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
        if cfg.get("ending", {}).get("enabled"):
            end = ending_shot(ep, summ, shots, bible, cfg)
            if end:
                shots.append(end)
        if cfg.get("ending_scene", {}).get("enabled"):
            esc = ending_scene_shot(ep, summ, shots, bible, cfg)
            if esc:
                shots.append(esc)
        save(os.path.join(out_dir, "04_shots", f"ep_{num:02d}.json"), {"episode": num, "raw_count": len(obj.shots), "shots": shots})
        res[num] = shots
        temp = []
        for s in shots:
            temp.append(f"{s['size'][0]}{s['intensity']}")
        print(f"  ep {num}: {len(shots)}/{len(obj.shots)} shots kept {' '.join(temp)}")
    return res
