import os
from src import llm
from src.schemas import EpisodeOut, LABELS, PRIORITY
from src.common import save, fmt_lines, line_index, log_drop


def make_check(ep):
    idx = line_index(ep)
    def check(obj):
        errs = []
        if obj.episode != ep["episode"]:
            errs.append(f"episode must be {ep['episode']}")
        if not obj.key_events:
            errs.append("key_events is empty")
        for ev in obj.key_events:
            if ev.line_id not in idx:
                errs.append(f"key event line_id {ev.line_id} does not exist")
            elif ep["lines"][idx[ev.line_id]]["is_recap"]:
                errs.append(f"key event line_id {ev.line_id} is a recap line")
        if obj.peak_line_id not in idx:
            errs.append(f"peak_line_id {obj.peak_line_id} does not exist")
        elif ep["lines"][idx[obj.peak_line_id]]["is_recap"]:
            errs.append(f"peak_line_id {obj.peak_line_id} is a recap line")
        if obj.ending_cliffhanger.line_id not in idx:
            errs.append(f"ending_cliffhanger line_id {obj.ending_cliffhanger.line_id} does not exist")
        if len(obj.secondary_labels) > 2:
            errs.append("at most 2 secondary_labels")
        return errs
    return check


def clean(obj, ep, drops):
    idx = line_index(ep)
    num = ep["episode"]
    res = obj.model_dump()
    evs = []
    for ev in res["key_events"]:
        if ev["line_id"] not in idx or ep["lines"][idx[ev["line_id"]]]["is_recap"]:
            log_drop(drops, "episodes", f"ep{num}", "invalid_id", f"key event {ev['line_id']}")
            continue
        evs.append(ev)
    evs.sort(key=lambda e: idx[e["line_id"]])
    for i, ev in enumerate(evs):
        ev["event_id"] = f"E{num}_ev{i+1}"
        ev["episode"] = num
        ev["start"] = ep["lines"][idx[ev["line_id"]]]["start"]
        # the speaker tag comes from the screenplay, so the bible can tie "the nephew" to a tag like "NEW INTERN"
        ev["speaker"] = ep["lines"][idx[ev["line_id"]]]["speaker"]
    res["key_events"] = evs
    cnt = {}
    for ln in ep["lines"]:
        if ln["type"] == "dialogue" and ln["speaker"] and not ln["is_recap"]:
            cnt[ln["speaker"]] = cnt.get(ln["speaker"], 0) + 1
    res["speaker_tags"] = cnt
    res["episode"] = num
    pk = res["peak_line_id"]
    if pk not in idx or ep["lines"][idx[pk]]["is_recap"]:
        log_drop(drops, "episodes", f"ep{num}", "invalid_id", f"peak {pk}")
        if evs:
            res["peak_line_id"] = evs[0]["line_id"]
        else:
            res["peak_line_id"] = None
    cl = res["ending_cliffhanger"]["line_id"]
    if cl not in idx:
        log_drop(drops, "episodes", f"ep{num}", "invalid_id", f"cliffhanger {cl}")
        temp = None
        for ln in reversed(ep["lines"]):
            if ln["type"] == "dialogue" or ln["type"] == "action":
                temp = ln["id"]
                break
        res["ending_cliffhanger"]["line_id"] = temp
    sec = []
    for lb in res["secondary_labels"]:
        if lb != res["primary_label"] and lb not in sec and lb in LABELS:
            sec.append(lb)
    res["secondary_labels"] = sec[:2]
    res["promo_priority"] = PRIORITY[res["primary_label"]]
    pr = {}
    for lb in [res["primary_label"]] + res["secondary_labels"]:
        pr[lb] = PRIORITY[lb]
    res["label_priority"] = pr
    return res


def run(eps, cfg, out_dir, drops, failed):
    res = []
    for ep in eps:
        num = ep["episode"]
        path = os.path.join(out_dir, "02_episodes", f"ep_{num:02d}.json")
        lines = fmt_lines(ep["lines"])
        notes = "(none)"
        meta = ep.get("meta", {})
        if meta:
            notes = "Logline: " + meta.get("episode_logline", "") + "\nContinuity: " + meta.get("continuity_notes", "")
        first = ep["lines"][0]["id"]
        for ln in ep["lines"]:
            if ln["type"] == "dialogue" or ln["type"] == "action":
                first = ln["id"]
                break
        try:
            obj = llm.call_llm("episode", {"episode": str(num), "lines": lines, "first_id": first, "notes": notes},
                               EpisodeOut, check=make_check(ep), mock_key=f"ep_{num:02d}", step="02_episodes")
        except Exception as ex:
            failed.append({"step": "episodes", "episode": num, "error": str(ex)[:300]})
            print(f"  ep {num}: FAILED {str(ex)[:120]}")
            continue
        out = clean(obj, ep, drops)
        save(path, out)
        res.append(out)
        print(f"  ep {num}: {out['primary_label']} {out['secondary_labels']} events={len(out['key_events'])}")
    return res
