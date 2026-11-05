import os
from src.common import save, line_index, log_drop
from src.bible import premise


def seg_dur(seg, eps_by):
    lines = eps_by[seg["episode"]]["lines"]
    return lines[seg["b"]]["end"] - lines[seg["a"]]["start"]


def total_dur(segs, eps_by):
    res = 0.0
    for s in segs:
        res += seg_dur(s, eps_by)
    return res


def make_segments(cand, shot_by):
    segs = []
    n = len(cand["beats"])
    for i, b in enumerate(cand["beats"]):
        s = shot_by[b["shot_id"]]
        role = "escalation"
        if b["beat"] == "scene":
            role = "scene"
        elif i == 0:
            role = "hook"
        elif i == n-1:
            role = "cliffhanger"
        segs.append({"episode": s["episode"], "a": s["start_idx"], "b": s["end_idx"], "keys": [s["key_idx"]],
                     "roles": [role], "shot_ids": [s["shot_id"]], "hook_key": None, "intensity": s["intensity"],
                     "threads": [s["thread_id"]], "char_ids": list(s["char_ids"])})
        if role == "hook" or role == "scene":
            segs[-1]["hook_key"] = s["key_idx"]
    return segs


def merge_into(x, y):
    x["a"] = min(x["a"], y["a"])
    x["b"] = max(x["b"], y["b"])
    x["keys"] = x["keys"] + y["keys"]
    x["roles"] = x["roles"] + y["roles"]
    x["shot_ids"] = x["shot_ids"] + y["shot_ids"]
    x["threads"] = x["threads"] + y["threads"]
    for c in y["char_ids"]:
        if c not in x["char_ids"]:
            x["char_ids"].append(c)
    if x["hook_key"] is None:
        x["hook_key"] = y["hook_key"]
    if y["intensity"] > x["intensity"]:
        x["intensity"] = y["intensity"]


def order_and_merge(segs, template):
    if template == "montage":
        res = []
        for s in segs:
            clash = None
            for r in res:
                if r["episode"] == s["episode"] and s["a"] <= r["b"] + 1 and r["a"] <= s["b"] + 1:
                    clash = r
            if clash is None:
                res.append(s)
            elif "cliffhanger" in s["roles"]:
                res.remove(clash)
                res.append(s)
        return res, None
    segs = sorted(segs, key=lambda s: (s["episode"], s["a"], s["b"]))
    res = []
    for s in segs:
        if res and res[-1]["episode"] == s["episode"] and s["a"] <= res[-1]["b"] + 1:
            merge_into(res[-1], s)
        else:
            res.append(s)
    if "hook" not in res[0]["roles"] and "scene" not in res[0]["roles"]:
        return res, "hook is not first in story order"
    if len(segs) > 1 and "cliffhanger" not in res[-1]["roles"] and "scene" not in res[-1]["roles"]:
        return res, "cliffhanger is not last in story order"
    return res, None


def protected(seg):
    lo = min(seg["keys"])
    hi = max(seg["keys"])
    if seg["hook_key"] is not None:
        if seg["hook_key"] < lo:
            lo = seg["hook_key"]
        if seg["hook_key"] > hi:
            hi = seg["hook_key"]
    return lo, hi


def trim(segs, eps_by, max_sec):
    # escalation segments first, then hook end, then cliffhanger start
    n = len(segs)
    order = []
    for i in range(1, n-1):
        order.append((i, "both"))
    if n > 1:
        order.append((0, "end"))
        order.append((n-1, "start"))
    for i, side in order:
        s = segs[i]
        lines = eps_by[s["episode"]]["lines"]
        lo, hi = protected(s)
        while total_dur(segs, eps_by) > max_sec:
            cut_start = s["a"] < lo
            cut_end = s["b"] > hi
            if side == "end":
                cut_start = False
            if side == "start":
                cut_end = False
            if not cut_start and not cut_end:
                break
            if cut_start and cut_end:
                # cut the edge farther from the key
                if (lines[lo]["start"] - lines[s["a"]]["start"]) >= (lines[s["b"]]["end"] - lines[hi]["end"]):
                    s["a"] += 1
                else:
                    s["b"] -= 1
            elif cut_start:
                s["a"] += 1
            else:
                s["b"] -= 1
        if total_dur(segs, eps_by) <= max_sec:
            return True
    return total_dur(segs, eps_by) <= max_sec


def covered(cov, num, j):
    for a, b in cov.get(num, []):
        if a <= j <= b:
            return True
    return False


def extend(segs, eps_by, cov, min_sec):
    for i in range(len(segs)-1):
        s = segs[i]
        lines = eps_by[s["episode"]]["lines"]
        nxt = segs[i+1]
        limit = len(lines) - 1
        if nxt["episode"] == s["episode"] and nxt["a"] > s["b"]:
            limit = nxt["a"] - 2
        while total_dur(segs, eps_by) < min_sec:
            j = s["b"] + 1
            if j > limit:
                break
            if lines[j]["is_recap"] or not covered(cov, s["episode"], j):
                break
            s["b"] = j
        if total_dur(segs, eps_by) >= min_sec:
            return True
    return total_dur(segs, eps_by) >= min_sec


def twist_lines(bible, summ):
    ev_line = {}
    for s in summ:
        for ev in s["key_events"]:
            ev_line[ev["event_id"]] = (ev["episode"], ev["line_id"])
    res = []
    for w in bible["twists"]:
        e = w["payoff_event_id"]
        if e and e in ev_line and not premise(w):
            res.append({"twist_id": w["twist_id"], "severity": w["severity"], "episode": ev_line[e][0], "line_id": ev_line[e][1]})
    return res


def assemble_one(cand, shot_by, eps_by, cov, twists, cfg):
    pc = cfg["promo"]
    segs = make_segments(cand, shot_by)
    segs, err = order_and_merge(segs, cand["template"])
    if err:
        return None, "not_chronological", err
    # hook within window
    first = segs[0]
    lines = eps_by[first["episode"]]["lines"]
    flags = []
    hk = first["hook_key"]
    if hk is None:
        hk = first["keys"][0]
    hook_shot = cand["beats"][0]["shot_id"]
    if cand["template"] == "ending_scene":
        # built so its hook lands within the window; the hook shot is named, not searched for
        esc = shot_by[hook_shot]
        hk = esc["key_idx"]
        hook_shot = esc["hook_shot_id"]
    elif cand["template"] == "single_scene" or "scene" in first["roles"]:
        # use the earliest hook-role shot inside the scene if there is one
        best = None
        for s in shot_by.values():
            if s["episode"] == first["episode"] and s["role"] == "hook" and first["a"] <= s["key_idx"] <= first["b"]:
                if best is None or s["key_idx"] < best["key_idx"]:
                    best = s
        if best is not None:
            hk = best["key_idx"]
            hook_shot = best["shot_id"]
        elif lines[hk]["start"] - lines[first["a"]]["start"] > pc["hook_window_sec"]:
            hk = first["a"]
            flags.append("hook_unverified")
    if lines[hk]["start"] - lines[first["a"]]["start"] > pc["hook_window_sec"]:
        first["a"] = hk
        flags.append("hook_moved")
    first["hook_key"] = hk
    for i in range(len(first["keys"])):
        if first["keys"][i] < first["a"]:
            first["keys"][i] = first["a"]
    if total_dur(segs, eps_by) > pc["max_sec"]:
        if not trim(segs, eps_by, pc["max_sec"]):
            return None, "too_long", f"{round(total_dur(segs, eps_by), 1)}s after trim"
        flags.append("trimmed")
    if total_dur(segs, eps_by) < pc["min_sec"]:
        cv = {}
        for num, arr in cov.items():
            cv[num] = []
            for a, b, k, inten, role in arr:
                if inten >= cfg["shots"]["extend_min_intensity"]:
                    cv[num].append((a, b))
        if not extend(segs, eps_by, cv, pc["min_sec"]):
            return None, "too_short", f"{round(total_dur(segs, eps_by), 1)}s after extend"
        flags.append("extended")
    # recap guard
    for s in segs:
        ls = eps_by[s["episode"]]["lines"]
        for j in range(s["a"], s["b"]+1):
            if ls[j]["is_recap"]:
                return None, "recap", f"ep{s['episode']} {ls[j]['id']}"
    penalty = 0
    for t in twists:
        for s in segs:
            if s["episode"] != t["episode"]:
                continue
            idx = line_index(eps_by[s["episode"]])
            j = idx.get(t["line_id"])
            if j is not None and s["a"] <= j <= s["b"]:
                if t["severity"] == "major":
                    return None, "spoiler", f"major twist {t['twist_id']} payoff {t['line_id']}"
                penalty = 5
                if "minor_spoiler" not in flags:
                    flags.append("minor_spoiler")
    out = []
    for s in segs:
        ls = eps_by[s["episode"]]["lines"]
        out.append({"episode": s["episode"], "start": ls[s["a"]]["start"], "end": ls[s["b"]]["end"],
                    "start_line_id": ls[s["a"]]["id"], "end_line_id": ls[s["b"]]["id"],
                    "a": s["a"], "b": s["b"], "roles": s["roles"], "shot_ids": s["shot_ids"],
                    "threads": s["threads"], "char_ids": s["char_ids"], "hook_key": s["hook_key"],
                    "duration": round(seg_dur(s, eps_by), 2)})
    res = {"promo_id": None, "cand_id": cand["cand_id"], "source": cand["source"], "template": cand["template"],
           "script_id": cand.get("script_id"), "idea": cand["idea"], "beats": cand["beats"], "segments": out,
           "total_duration_sec": round(total_dur(segs, eps_by), 2), "hook_shot_id": hook_shot,
           "hook_intensity": shot_by[hook_shot]["intensity"], "hook_needs_context": shot_by[hook_shot]["needs_context"],
           "main_thread": shot_by[hook_shot]["thread_id"], "spoiler_penalty": penalty, "flags": flags}
    return res, None, None


def run(cands, bank, eps, bible, summ, cfg, out_dir, drops):
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    shot_by = {}
    cov = {}
    for num, arr in bank.items():
        cov[num] = []
        for s in arr:
            shot_by[s["shot_id"]] = s
            cov[num].append((s["start_idx"], s["end_idx"], s["key_idx"], s["intensity"], s["role"]))
    twists = twist_lines(bible, summ)
    res = []
    dropped = []
    seen = set()
    for c in cands:
        p, reason, detail = assemble_one(c, shot_by, eps_by, cov, twists, cfg)
        if p is None:
            dropped.append({"cand_id": c["cand_id"], "source": c["source"], "template": c["template"], "reason": reason, "detail": detail})
            log_drop(drops, "assemble", c["cand_id"], reason, detail)
            continue
        key = []
        for s in p["segments"]:
            key.append((s["episode"], s["a"], s["b"]))
        key = tuple(key)
        if key in seen:
            dropped.append({"cand_id": c["cand_id"], "source": c["source"], "template": c["template"], "reason": "duplicate", "detail": ""})
            log_drop(drops, "assemble", c["cand_id"], "duplicate", "")
            continue
        seen.add(key)
        p["promo_id"] = f"X{len(res)+1:03d}"
        res.append(p)
    save(os.path.join(out_dir, "06_assembled.json"), res)
    save(os.path.join(out_dir, "06_dropped.json"), dropped)
    print(f"  assembled {len(res)} promos, dropped {len(dropped)}")
    return res
