import os
from src import llm
from src.schemas import ScriptsOut, MatchOut
from src.bible import compact, resolve, norm_name, premise
from src.common import save, fmt_lines, line_index, log_drop

ROLE_OK = {"hook": ["hook"], "escalation": ["escalation", "payoff", "hook"], "payoff": ["payoff", "escalation"],
           "cliffhanger": ["cliffhanger", "payoff"]}


def event_table(summ):
    res = {}
    for s in summ:
        lbl = [s["primary_label"]] + s["secondary_labels"]
        for ev in s["key_events"]:
            temp = dict(ev)
            temp["labels"] = lbl
            res[ev["event_id"]] = temp
    return res


def spoiler_events(bible):
    res = []
    for w in bible["twists"]:
        if w["severity"] == "major" and w["payoff_event_id"] and not premise(w):
            res.append(w["payoff_event_id"])
    return res


def template_errs(sc):
    beats = []
    for b in sc.beats:
        beats.append(b.beat)
    t = sc.template
    if t == "single_scene":
        if beats != ["scene"]:
            return f"{sc.script_id}: single_scene must have exactly one beat named scene"
        return None
    if t == "montage":
        if not beats or beats[-1] != "cliffhanger":
            return f"{sc.script_id}: montage must end with a cliffhanger beat"
        cnt = 0
        for b in beats[:-1]:
            if b != "peak":
                return f"{sc.script_id}: montage beats before the cliffhanger must be peak"
            cnt += 1
        if cnt < 3 or cnt > 5:
            return f"{sc.script_id}: montage needs 4 to 5 peaks"
        return None
    if len(beats) < 2 or beats[0] != "hook" or beats[-1] != "cliffhanger":
        return f"{sc.script_id}: {t} must start with hook and end with cliffhanger"
    for b in beats[1:-1]:
        if b not in ["escalation", "payoff"]:
            return f"{sc.script_id}: middle beats must be escalation or payoff"
    if len(beats) > 4:
        return f"{sc.script_id}: too many beats"
    return None


def ev_pos(ev):
    return (ev["episode"], ev["start"])


def order_err(sc, evs):
    if sc.template == "montage":
        return None
    prev = None
    for b in sc.beats:
        if b.event_id not in evs:
            return None
        pos = ev_pos(evs[b.event_id])
        if prev is not None and pos < prev:
            return f"{sc.script_id}: beats must be in story order (episode, then time); {b.event_id} comes before the previous beat's event"
        prev = pos
    return None


def make_check(evs, spoil):
    def check(obj):
        errs = []
        for sc in obj.scripts:
            temp = template_errs(sc)
            if temp:
                errs.append(temp)
            temp = order_err(sc, evs)
            if temp:
                errs.append(temp)
            for b in sc.beats:
                if b.event_id not in evs:
                    errs.append(f"{sc.script_id}: unknown event_id {b.event_id}")
                elif b.event_id in spoil:
                    errs.append(f"{sc.script_id}: {b.event_id} is a major twist payoff, do not use it")
        return errs
    return check


def shot_tags(ev, bank, eps_by):
    ep = eps_by.get(ev["episode"])
    if ep is None:
        return "[no shot]"
    j = line_index(ep).get(ev["line_id"])
    res = []
    for s in bank.get(ev["episode"], []):
        # a shot covers the event, or its key line is within 3 lines of it
        if (s["start_idx"] <= j <= s["end_idx"]) or abs(s["key_idx"] - j) <= 3:
            temp = f"{s['role']} {s['size']} i{s['intensity']}"
            if temp not in res:
                res.append(temp)
    if not res:
        return "[no shot]"
    return "[shots: " + "; ".join(res) + "]"


def write_scripts(summ, bible, cfg, drops, bank=None, eps_by=None):
    evs = event_table(summ)
    spoil = spoiler_events(bible)
    rows = []
    for eid, ev in evs.items():
        tag = ""
        if bank is not None:
            tag = " " + shot_tags(ev, bank, eps_by)
        rows.append(f"{eid} | {','.join(ev['labels'])} | {ev['description']}{tag}")
    temp = []
    for e in spoil:
        if e in evs:
            temp.append(f"{e}: {evs[e]['description']}")
    if not temp:
        temp = ["(none)"]
    pt = cfg["promo"]["per_template_max"] + 1
    obj = llm.call_llm("scripts", {"bible": compact(bible), "events": "\n".join(rows), "spoilers": "\n".join(temp),
                                   "num_scripts": str(cfg["scripts"]["num_scripts"]), "per_template": str(pt), "max_per_thread": str(cfg["select"]["max_per_thread"])},
                       ScriptsOut, check=make_check(evs, set(spoil)), mock_key="scripts", step="05_scripts")
    res = []
    cnt = {}
    seen = set()
    for sc in obj.scripts:
        sid = sc.script_id
        if sid in seen:
            sid = f"{sid}_{len(seen)}"
        seen.add(sid)
        bad = template_errs(sc)
        if not bad:
            bad = order_err(sc, evs)
        for b in sc.beats:
            if b.event_id not in evs:
                bad = f"unknown event {b.event_id}"
            elif b.event_id in spoil:
                bad = f"major twist payoff {b.event_id}"
        if bad:
            log_drop(drops, "scripts", sid, "invalid_script", bad)
            continue
        cnt[sc.template] = cnt.get(sc.template, 0) + 1
        if cnt[sc.template] > pt:
            log_drop(drops, "scripts", sid, "over_template_cap", sc.template)
            continue
        d = sc.model_dump()
        d["script_id"] = sid
        res.append(d)
    return res, evs


def event_chars(ev, eps_by, amap):
    res = set()
    ep = eps_by.get(ev["episode"])
    if ep:
        idx = line_index(ep)
        if ev["line_id"] in idx:
            ln = ep["lines"][idx[ev["line_id"]]]
            if ln["speaker"]:
                c = resolve(ln["speaker"], amap)
                if c:
                    res.add(c)
    desc = " " + norm_name(ev["description"]) + " "
    for alias, cid in amap.items():
        if " " + alias + " " in desc:
            res.add(cid)
    return res


def event_threads(eid, bible):
    res = set()
    for t in bible["threads"]:
        if eid in t["key_event_ids"]:
            res.add(t["thread_id"])
    return res


def candidates(beat, ev, bank, eps_by, bible, lo=None, hi=None):
    amap = bible["alias_map"]
    chars = event_chars(ev, eps_by, amap)
    threads = event_threads(beat["event_id"], bible)
    if beat.get("thread_id"):
        threads.add(beat["thread_id"])
    ep = eps_by.get(ev["episode"])
    ev_idx = 0
    if ep:
        ev_idx = line_index(ep).get(ev["line_id"], 0)
    res = []
    for num in [ev["episode"]-1, ev["episode"], ev["episode"]+1]:
        for s in bank.get(num, []):
            if beat["beat"] == "scene":
                if s["size"] != "long":
                    continue
            elif beat["beat"] == "peak":
                if s["intensity"] < 7:
                    continue
            elif s["role"] not in ROLE_OK[beat["beat"]]:
                continue
            # keep story order with the neighbouring beats' events
            kpos = (s["episode"], eps_by[s["episode"]]["lines"][s["key_idx"]]["start"])
            if lo is not None and kpos < lo:
                continue
            if hi is not None and kpos > hi:
                continue
            if chars or threads:
                share = False
                if s["thread_id"] and s["thread_id"] in threads:
                    share = True
                for c in s["char_ids"]:
                    if c in chars:
                        share = True
                if not share:
                    continue
            if num == ev["episode"]:
                dist = abs(s["key_idx"] - ev_idx)
            else:
                dist = 1000 * abs(num - ev["episode"]) + s["key_idx"]
            res.append((dist, s["shot_id"]))
    res.sort()
    out = []
    for d, sid in res[:5]:
        out.append(sid)
    return out


def required(beat, i, n):
    if beat in ["hook", "cliffhanger", "scene"]:
        return True
    return False


def match_scripts(scripts, evs, bank, eps_by, bible, cfg, drops):
    by_id = {}
    for num, arr in bank.items():
        for s in arr:
            by_id[s["shot_id"]] = s
    filled = []
    for sc in scripts:
        sid = sc["script_id"]
        cands = []
        n = len(sc["beats"])
        for i, b in enumerate(sc["beats"]):
            lo = None
            hi = None
            if sc["template"] != "montage":
                if i > 0:
                    lo = ev_pos(evs[sc["beats"][i-1]["event_id"]])
                if i < n-1:
                    hi = ev_pos(evs[sc["beats"][i+1]["event_id"]])
            cands.append(candidates(b, evs[b["event_id"]], bank, eps_by, bible, lo, hi))
        sc["candidates"] = cands
        fail = None
        for i, b in enumerate(sc["beats"]):
            if not cands[i] and required(b["beat"], i, len(sc["beats"])):
                fail = f"no candidates for {b['beat']} ({b['event_id']})"
        if fail:
            sc["status"] = "failed"
            sc["fail_reason"] = fail
            log_drop(drops, "match", sid, "unfilled_beat", fail)
            continue
        txt = []
        for i, b in enumerate(sc["beats"]):
            ev = evs[b["event_id"]]
            txt.append(f"BEAT {i}: {b['beat']} | need: {b['need']} | event ep{ev['episode']}: {ev['description']}")
            if not cands[i]:
                txt.append("  (no candidates, answer none)")
            for c in cands[i]:
                s = by_id[c]
                ep = eps_by[s["episode"]]
                txt.append(f"  CANDIDATE {c} (ep {s['episode']}, {s['duration']}s, role {s['role']}, intensity {s['intensity']}):")
                for ln in fmt_lines(ep["lines"][s["start_idx"]:s["end_idx"]+1]).split("\n"):
                    txt.append("    " + ln)
        def check(obj, cands=cands):
            errs = []
            for p in obj.picks:
                if p.beat_index < 0 or p.beat_index >= len(cands):
                    errs.append(f"beat_index {p.beat_index} out of range")
                elif p.shot_id != "none" and p.shot_id not in cands[p.beat_index]:
                    errs.append(f"beat {p.beat_index}: {p.shot_id} is not one of its candidates")
            return errs
        try:
            obj = llm.call_llm("match", {"script_id": sid, "template": sc["template"], "idea": sc["idea"], "beats": "\n".join(txt)},
                               MatchOut, check=check, mock_key=sid, step="05_match")
        except Exception as ex:
            sc["status"] = "failed"
            sc["fail_reason"] = f"match call failed: {str(ex)[:200]}"
            log_drop(drops, "match", sid, "llm_failed", str(ex)[:200])
            continue
        picks = {}
        for p in obj.picks:
            if 0 <= p.beat_index < len(cands) and p.shot_id in cands[p.beat_index]:
                picks[p.beat_index] = p.shot_id
        beats = []
        fail = None
        for i, b in enumerate(sc["beats"]):
            shot = picks.get(i)
            if shot is None:
                if required(b["beat"], i, len(sc["beats"])):
                    fail = f"{b['beat']} beat is none"
                continue
            temp = dict(b)
            temp["shot_id"] = shot
            beats.append(temp)
        if sc["template"] == "montage":
            peaks = 0
            for b in beats:
                if b["beat"] == "peak":
                    peaks += 1
            if peaks < 3:
                fail = f"only {peaks} peaks filled"
        if fail:
            sc["status"] = "failed"
            sc["fail_reason"] = fail
            log_drop(drops, "match", sid, "unfilled_beat", fail)
            continue
        sc["status"] = "filled"
        sc["filled_beats"] = beats
        filled.append({"cand_id": sid, "source": "script", "template": sc["template"], "script_id": sid,
                       "idea": sc["idea"], "beats": beats})
    return filled


def safety_net(bank, used, cfg):
    pool = []
    for num, arr in bank.items():
        for s in arr:
            if s["shot_id"] in used:
                continue
            if s["needs_context"] == "high":
                continue
            pool.append(s)
    pool.sort(key=lambda x: (-x["intensity"], x["episode"], x["start_idx"]))
    pool = pool[:cfg["safety_net"]["num_shots"]]
    res = []
    for s in pool:
        if s["size"] == "long":
            res.append({"cand_id": f"SN_{s['shot_id']}", "source": "safety_net", "template": "single_scene", "script_id": None,
                        "idea": f"strongest scene in ep {s['episode']}: {s['quote_or_action'][:80]}",
                        "beats": [{"beat": "scene", "need": "", "event_id": None, "shot_id": s["shot_id"]}]})
            continue
        if s["role"] != "hook":
            continue
        cl = None
        for t in bank.get(s["episode"], []):
            if t["role"] == "cliffhanger" and t["start_idx"] > s["key_idx"] and t["shot_id"] != s["shot_id"]:
                cl = t
                break
        if cl is None:
            continue
        res.append({"cand_id": f"SN_{s['shot_id']}", "source": "safety_net", "template": "reveal_tease", "script_id": None,
                    "idea": f"hook from ep {s['episode']} to its cliffhanger",
                    "beats": [{"beat": "hook", "need": "", "event_id": None, "shot_id": s["shot_id"]},
                              {"beat": "cliffhanger", "need": "", "event_id": None, "shot_id": cl["shot_id"]}]})
    return res


def hook_endings(bank, cfg):
    # pair a strong earlier hook with an episode's own ending; the hook must share a thread or a character
    hc = cfg["hook_ending"]
    pc = cfg["promo"]
    ends = []
    hooks = []
    for num, arr in bank.items():
        for s in arr:
            if s.get("source") == "episode_ending":
                ends.append(s)
            elif s["role"] == "hook" and s["intensity"] >= hc["min_hook_intensity"] and s["needs_context"] != "high":
                hooks.append(s)
    ends.sort(key=lambda x: (-x["intensity"], x["episode"]))
    used = set()
    res = []
    for e in ends:
        best = None
        best_key = None
        for h in hooks:
            if h["shot_id"] in used:
                continue
            if h["episode"] > e["episode"] or h["episode"] < e["episode"] - hc["window_episodes"]:
                continue
            if h["episode"] == e["episode"] and h["end_idx"] >= e["start_idx"]:
                continue
            shared = h["thread_id"] is not None and h["thread_id"] == e["thread_id"]
            for c in h["char_ids"]:
                if c in e["char_ids"]:
                    shared = True
            if not shared:
                continue
            tot = h["duration"] + e["duration"]
            if tot > pc["max_sec"]:
                continue
            # a pair under min_sec is dropped later unless extension rescues it, so prefer pairs that reach it;
            # then the strongest hook, then the total closest to the target length
            short = 0
            if tot < pc["min_sec"]:
                short = 1
            key = (short, -h["intensity"], abs(tot - (pc["target_min_sec"] + pc["target_max_sec"]) / 2), -h["episode"])
            if best_key is None or key < best_key:
                best = h
                best_key = key
        if best is None:
            continue
        used.add(best["shot_id"])
        res.append({"cand_id": f"HE_{e['shot_id']}", "source": "hook_ending", "template": "hook_ending", "script_id": None,
                    "idea": f"hook from ep {best['episode']} into the ending of ep {e['episode']}",
                    "beats": [{"beat": "hook", "need": "", "event_id": None, "shot_id": best["shot_id"]},
                              {"beat": "cliffhanger", "need": "", "event_id": None, "shot_id": e["shot_id"]}]})
        if len(res) >= hc["max_candidates"]:
            break
    return res


def run(summ, bible, bank, eps, cfg, out_dir, drops, failed):
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    try:
        scripts, evs = write_scripts(summ, bible, cfg, drops, bank, eps_by)
    except Exception as ex:
        failed.append({"step": "scripts", "error": str(ex)[:300]})
        print(f"  scripts FAILED: {str(ex)[:200]}")
        scripts = []
        evs = event_table(summ)
    print(f"  {len(scripts)} valid scripts")
    filled = match_scripts(scripts, evs, bank, eps_by, bible, cfg, drops)
    used = set()
    for c in filled:
        for b in c["beats"]:
            used.add(b["shot_id"])
    net = safety_net(bank, used, cfg)
    fill = {"scripts_written": len(scripts), "scripts_filled": len(filled), "fill_rate": 0.0}
    if scripts:
        fill["fill_rate"] = round(len(filled) / len(scripts), 3)
    he = []
    if cfg.get("hook_ending", {}).get("enabled"):
        he = hook_endings(bank, cfg)
    print(f"  filled {len(filled)}/{len(scripts)} scripts, {len(net)} safety-net candidates, {len(he)} hook_ending candidates")
    res = {"scripts": scripts, "candidates": filled + net + he, "fill": fill}
    save(os.path.join(out_dir, "05_candidates.json"), res)
    return res
