import os
import re
import json
from src import llm
from src.schemas import Bible
from src.common import save, log_drop


def premise(w):
    # a promo sends viewers to episode 1, so what episode 1 shows is the premise, not a spoiler
    e = w.get("payoff_event_id") or ""
    return e.startswith("E1_")


def digest(summ):
    res = []
    for s in summ:
        lbl = [s["primary_label"]] + s["secondary_labels"]
        res.append(f"EP {s['episode']} [{', '.join(lbl)}]: {s['summary']}")
        for ev in s["key_events"]:
            if ev.get("speaker"):
                res.append(f"  {ev['event_id']} (line spoken by {ev['speaker']}): {ev['description']}")
            else:
                res.append(f"  {ev['event_id']}: {ev['description']}")
        res.append(f"  cliffhanger: {s['ending_cliffhanger']['description']}")
        tags = s.get("speaker_tags", {})
        if tags:
            res.append("  speaker tags: " + ", ".join(f"{k} ({v})" for k, v in sorted(tags.items(), key=lambda x: -x[1])))
    return "\n".join(res)


def all_event_ids(summ):
    res = set()
    for s in summ:
        for ev in s["key_events"]:
            res.add(ev["event_id"])
    return res


def make_check(ids):
    def check(obj):
        errs = []
        for t in obj.threads:
            for e in t.key_event_ids:
                if e not in ids:
                    errs.append(f"thread {t.thread_id} uses unknown event id {e}")
        for w in obj.twists:
            for e in [w.setup_event_id, w.payoff_event_id]:
                if e and e not in ids:
                    errs.append(f"twist {w.twist_id} uses unknown event id {e}")
        for l in obj.setup_payoff_links:
            for e in [l.setup_event_id, l.payoff_event_id]:
                if e not in ids:
                    errs.append(f"link uses unknown event id {e}")
        cids = []
        for c in obj.characters:
            cids.append(c.char_id)
        if len(set(cids)) != len(cids):
            errs.append("char_id values must be unique")
        tids = []
        for t in obj.threads:
            tids.append(t.thread_id)
        if len(set(tids)) != len(tids):
            errs.append("thread_id values must be unique")
        return errs
    return check


def clean(obj, ids, drops):
    res = obj.model_dump()
    tids = set()
    for t in res["threads"]:
        tids.add(t["thread_id"])
        keep = []
        for e in t["key_event_ids"]:
            if e in ids:
                keep.append(e)
            else:
                log_drop(drops, "bible", t["thread_id"], "invalid_id", e)
        t["key_event_ids"] = keep
    for w in res["twists"]:
        for k in ["setup_event_id", "payoff_event_id"]:
            if w[k] and w[k] not in ids:
                log_drop(drops, "bible", w["twist_id"], "invalid_id", w[k])
                w[k] = None
        if w["payoff_event_id"] and w["payoff_event_id"] == w["setup_event_id"]:
            log_drop(drops, "bible", w["twist_id"], "payoff_equals_setup", w["payoff_event_id"])
            w["payoff_event_id"] = None
    links = []
    for l in res["setup_payoff_links"]:
        if l["setup_event_id"] in ids and l["payoff_event_id"] in ids:
            if l["thread_id"] not in tids:
                l["thread_id"] = None
            links.append(l)
        else:
            log_drop(drops, "bible", "link", "invalid_id", f"{l['setup_event_id']}->{l['payoff_event_id']}")
    res["setup_payoff_links"] = links
    res["alias_map"] = alias_map(res["characters"])
    return res


def alias_map(chars):
    res = {}
    firsts = {}
    for c in chars:
        names = [c["name"]] + c["aliases"]
        for n in names:
            k = norm_name(n)
            if k:
                res[k] = c["char_id"]
        parts = c["name"].split()
        if parts:
            k = norm_name(parts[0])
            firsts.setdefault(k, set()).add(c["char_id"])
    # first names only when unambiguous
    for k, v in firsts.items():
        if len(v) == 1 and k not in res:
            res[k] = list(v)[0]
    return res


def norm_name(n):
    n = n.lower()
    n = re.sub(r"\(.*?\)", "", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return " ".join(n.split())


def resolve(name, amap):
    k = norm_name(name)
    if k in amap:
        return amap[k]
    parts = k.split()
    if parts and parts[0] in amap:
        return amap[parts[0]]
    if parts and parts[-1] in amap:
        return amap[parts[-1]]
    return None


def compact(b):
    res = [f"GENRE/TONE: {b['genre_tone']}", "CHARACTERS:"]
    for c in b["characters"]:
        temp = f"  {c['char_id']} {c['name']}"
        if c["aliases"]:
            temp += f" (aka {', '.join(c['aliases'][:4])})"
        temp += f": {c['role']}"
        if c["relationships"]:
            temp += f"; {'; '.join(c['relationships'][:3])}"
        res.append(temp)
    res.append("THREADS:")
    for t in b["threads"]:
        temp = f"  {t['thread_id']} {t['name']} (ep {t['start_episode']}"
        if t["resolution_episode"]:
            temp += f"-{t['resolution_episode']}"
        temp += f", {t['status']})"
        res.append(temp)
    res.append("TWISTS:")
    for w in b["twists"]:
        res.append(f"  {w['twist_id']} [{w['severity']}] {w['description']}")
    return "\n".join(res)


def run(summ, cfg, out_dir, drops):
    ids = all_event_ids(summ)
    bc = cfg["bible"]
    dg = digest(summ)
    if len(dg) <= bc["max_input_chars"]:
        obj = llm.call_llm("bible", {"digest": dg, "scope": ""}, Bible, check=make_check(ids), mock_key="bible", step="03_bible")
    else:
        parts = []
        n = bc["chunk_size"]
        for i in range(0, len(summ), n):
            chunk = summ[i:i+n]
            scope = f" for episodes {chunk[0]['episode']} to {chunk[-1]['episode']} only"
            cids = all_event_ids(chunk)
            p = llm.call_llm("bible", {"digest": digest(chunk), "scope": scope}, Bible, check=make_check(cids),
                             mock_key=f"part_{i//n+1}", step="03_bible")
            parts.append(p.model_dump())
            save(os.path.join(out_dir, "03_bible_parts", f"part_{i//n+1}.json"), parts[-1])
        temp = []
        for i, p in enumerate(parts):
            temp.append(f"PART {i+1}:\n" + json.dumps(p, ensure_ascii=False))
        obj = llm.call_llm("bible_merge", {"parts": "\n\n".join(temp)}, Bible, check=make_check(ids), mock_key="bible", step="03_bible")
    res = clean(obj, ids, drops)
    save(os.path.join(out_dir, "03_bible.json"), res)
    print(f"  bible: {len(res['characters'])} characters, {len(res['threads'])} threads, {len(res['twists'])} twists")
    return res
