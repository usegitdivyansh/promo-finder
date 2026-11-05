import os
import json
import glob
from src.parse import fmt_ts


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_dir(path):
    res = []
    for p in sorted(glob.glob(os.path.join(path, "ep_*.json"))):
        res.append(load(p))
    res.sort(key=lambda x: x["episode"])
    return res


def fmt_line(ln):
    if ln["type"] == "dialogue":
        who = ln["speaker"] or "UNKNOWN"
    else:
        who = ln["type"].upper()
    res = f"[{ln['id']}] [{fmt_ts(ln['start'])}] {who}: {ln['text']}"
    if ln["is_recap"]:
        res = "(RECAP, do not use) " + res
    return res


def fmt_lines(lines):
    res = []
    for ln in lines:
        res.append(fmt_line(ln))
    return "\n".join(res)


def line_index(ep):
    res = {}
    for i, ln in enumerate(ep["lines"]):
        res[ln["id"]] = i
    return res


def log_drop(drops, step, item, reason, detail=""):
    drops.append({"step": step, "item": item, "reason": reason, "detail": detail})
