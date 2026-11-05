import os
import re
import json
import difflib

TS_RE = re.compile(r"^\s*[\[(]?\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*(?:(?:-->|--|-|–|—|to)\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?))?\s*[\])]?\s*[-:|.]?\s*")
EP_RE = re.compile(r"^\s*(?:#+\s*)?(?:episode|ep\.?)\s*[-#:]?\s*(\d+)\b", re.I)
SCENE_RE = re.compile(r"^(?:\d+\s*[.)]?\s*)?(INT\.|EXT\.|INT/EXT|EXT/INT|I/E\.?|SCENE\s+\d+)", re.I)
DIR_RE = re.compile(r"^(CUT TO|SMASH CUT|MATCH CUT|HARD CUT|JUMP CUT|FADE (IN|OUT|TO)|DISSOLVE|CLOSE ON|CLOSE-UP|CLOSE UP|EXTREME CLOSE|ANGLE ON|WIDE ON|WIDE SHOT|POV|INSERT|MONTAGE|END MONTAGE|FLASHBACK|END FLASHBACK|BACK TO|SFX|MUSIC|SUPER|TITLE|FREEZE FRAME|SLOW MOTION|TO BE CONTINUED|BEAT|PAUSE)\b", re.I)
SPK_RE = re.compile(r"^([A-Z][A-Za-z0-9.'’\- ]{0,30}?)\s*(\([^)]{0,30}\))?\s*:\s*(.+)$")
CUE_RE = re.compile(r"^([A-Z][A-Z0-9.'’\- ]{1,30}?)\s*(\([^)]{0,30}\))?\s*$")
NOT_SPEAKERS = {"note", "notes", "scene", "location", "time", "setting", "episode", "title", "summary", "recap", "music", "sfx", "vo", "v.o", "cut to", "transition"}


def to_sec(s):
    s = s.replace(",", ".")
    parts = s.split(":")
    res = 0.0
    for p in parts:
        res = res*60 + float(p)
    return res


def fmt_ts(sec):
    sec = int(round(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def est_dur(text, cfg):
    temp = len(text.split()) / cfg["words_per_sec"]
    if temp < cfg["min_line_sec"]:
        temp = cfg["min_line_sec"]
    return temp


def norm(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def read_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pdfplumber
        res = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                temp = page.extract_text()
                if temp:
                    res.append(temp)
        return "\n".join(res)
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        res = []
        for p in d.paragraphs:
            res.append(p.text)
        for t in d.tables:
            for row in t.rows:
                cells = []
                for c in row.cells:
                    cells.append(c.text.strip())
                res.append(" ".join(cells))
        return "\n".join(res)
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def classify(text, speaker):
    if speaker:
        return "dialogue"
    if SCENE_RE.match(text):
        return "scene"
    if DIR_RE.match(text):
        return "direction"
    inner = text.strip()
    if inner.startswith("(") and inner.endswith(")") and len(inner.split()) <= 3:
        return "direction"
    return "action"


def split_speaker(text, caps=False):
    m = SPK_RE.match(text)
    if not m:
        return None, text
    name = m.group(1).strip()
    if caps and not name.isupper():
        return None, text
    if len(name.split()) > 4:
        return None, text
    if name.lower().strip(". ") in NOT_SPEAKERS:
        return None, text
    if SCENE_RE.match(name) or DIR_RE.match(name):
        return None, text
    return name, m.group(3).strip()


# raw rows: {"start": sec or None, "text": str}
def rows_from_text(raw):
    rows = []
    cue = None
    caps = caps_mode(raw)
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            cue = None
            continue
        if re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?\.?", ln, re.I):
            continue
        start = None
        m = TS_RE.match(ln)
        if m and m.end() > 0 and re.search(r"\d:\d", m.group(0)):
            start = to_sec(m.group(1))
            ln = ln[m.end():].strip()
            if not ln:
                rows.append({"start": start, "text": "", "speaker": None, "pending": True})
                continue
        # timestamp on its own line, text on the next one
        if start is None and rows and rows[-1].get("pending"):
            rows[-1]["text"] = ln
            rows[-1]["pending"] = False
            sp, txt = split_speaker(ln, caps)
            rows[-1]["speaker"] = sp
            rows[-1]["text"] = txt
            continue
        # speaker cue line like "EMMA" with dialogue on the next line
        cm = CUE_RE.match(ln)
        if cm and not SCENE_RE.match(ln) and not DIR_RE.match(ln) and ln.isupper() and len(ln.split()) <= 4 and ln.lower() not in NOT_SPEAKERS:
            cue = {"name": cm.group(1).strip(), "start": start}
            continue
        sp, txt = split_speaker(ln, caps)
        if sp is None and cue is not None:
            sp = cue["name"]
            if start is None:
                start = cue["start"]
            cue = None
        rows.append({"start": start, "text": txt, "speaker": sp, "pending": False})
    res = []
    for r in rows:
        if r["text"]:
            res.append(r)
    return res


def rows_from_json(data):
    if isinstance(data, dict):
        for k in ["lines", "script", "screenplay", "dialogue", "rows", "items"]:
            if k in data and isinstance(data[k], list):
                data = data[k]
                break
    rows = []
    if not isinstance(data, list):
        return rows
    for item in data:
        if isinstance(item, str):
            rows.extend(rows_from_text(item))
            continue
        if not isinstance(item, dict):
            continue
        start = None
        for k in ["start", "timestamp", "time", "ts", "start_time"]:
            if k in item and item[k] is not None:
                v = item[k]
                if isinstance(v, (int, float)):
                    start = float(v)
                else:
                    m = TS_RE.match(str(v))
                    if m:
                        start = to_sec(m.group(1))
                break
        sp = None
        for k in ["speaker", "character", "name"]:
            if item.get(k):
                sp = str(item[k]).strip()
                break
        txt = ""
        for k in ["text", "line", "dialogue", "content", "action", "description"]:
            if item.get(k):
                txt = str(item[k]).strip()
                break
        if txt:
            rows.append({"start": start, "text": txt, "speaker": sp, "pending": False})
    return rows


def split_episodes(raw, fname):
    chunks = []
    cur = None
    for ln in raw.splitlines():
        m = EP_RE.match(ln)
        if m and len(ln.strip()) < 80:
            cur = {"episode": int(m.group(1)), "lines": []}
            chunks.append(cur)
            continue
        if cur is None:
            cur = {"episode": None, "lines": []}
            chunks.append(cur)
        cur["lines"].append(ln)
    numbered = False
    for c in chunks:
        if c["episode"] is not None:
            numbered = True
    res = []
    for c in chunks:
        text = "\n".join(c["lines"])
        if not text.strip():
            continue
        # title page or preamble before the first episode header
        if numbered and c["episode"] is None and not has_ts(text):
            continue
        res.append({"episode": c["episode"], "text": text})
    if len(res) == 1 and res[0]["episode"] is None:
        res[0]["episode"] = ep_from_name(fname)
    return res


def has_ts(text):
    for ln in text.splitlines():
        m = TS_RE.match(ln)
        if m and re.search(r"\d:\d", m.group(0)):
            return True
    return False


def caps_mode(raw):
    # if most "name:" prefixes are upper case, title-case ones like "Inside:" are not speakers
    up = 0
    cnt = 0
    for ln in raw.splitlines():
        ln = ln.strip()
        m = TS_RE.match(ln)
        if m and re.search(r"\d:\d", m.group(0)):
            ln = ln[m.end():].strip()
        sm = SPK_RE.match(ln)
        if sm and len(sm.group(1).split()) <= 4:
            cnt += 1
            if sm.group(1).isupper():
                up += 1
    return cnt >= 5 and up >= 0.8 * cnt


def ep_from_name(fname):
    base = os.path.splitext(os.path.basename(fname))[0]
    m = re.search(r"(?:ep|episode)[\s_\-]*(\d+)", base, re.I)
    if m:
        return int(m.group(1))
    nums = re.findall(r"\d+", base)
    if nums:
        return int(nums[-1])
    return None


def build_episode(ep, rows, cfg):
    lines = []
    scene = 0
    oddities = []
    for i, r in enumerate(rows):
        typ = classify(r["text"], r["speaker"])
        if typ == "scene":
            scene += 1
        if scene == 0:
            scene = 1
        est = False
        start = r["start"]
        if start is None:
            est = True
            if lines:
                prev = lines[-1]
                start = prev["start"] + est_dur(prev["text"], cfg)
            else:
                start = 0.0
        lines.append({"id": f"E{ep}_L{i}", "start": round(start, 2), "end": None, "type": typ,
                      "speaker": r["speaker"], "text": r["text"], "scene_id": scene,
                      "is_recap": False, "estimated_time": est})
    for i, ln in enumerate(lines):
        if i+1 < len(lines):
            nxt = lines[i+1]["start"]
            if nxt < ln["start"]:
                oddities.append(f"{ln['id']} timestamp goes backwards")
                nxt = ln["start"] + est_dur(ln["text"], cfg)
            ln["end"] = round(nxt, 2)
        else:
            ln["end"] = round(ln["start"] + est_dur(ln["text"], cfg), 2)
    dur = 0.0
    if lines:
        dur = lines[-1]["end"]
    return {"episode": ep, "duration_sec": dur, "lines": lines, "oddities": oddities}


def mark_recaps(eps, cfg):
    by_num = {}
    for e in eps:
        by_num[e["episode"]] = e
    for e in eps:
        prev = by_num.get(e["episode"]-1)
        if prev is None or not prev["lines"]:
            continue
        cut = prev["duration_sec"] - cfg["recap_lookback_sec"]
        tail = []
        for ln in prev["lines"]:
            if ln["end"] >= cut:
                temp = norm(ln["text"])
                if len(temp) >= 10:
                    tail.append(temp)
        for ln in e["lines"]:
            if ln["start"] > cfg["recap_window_sec"]:
                break
            temp = norm(ln["text"])
            if len(temp) < 10:
                continue
            for t in tail:
                if difflib.SequenceMatcher(None, temp, t).ratio() >= cfg["recap_similarity"]:
                    ln["is_recap"] = True
                    break


def parse_folder(inp, cfg, out_dir=None):
    pcfg = cfg["parse"]
    files = []
    for root, dirs, names in os.walk(inp):
        dirs.sort()
        for n in sorted(names):
            if n.startswith(".") or n.startswith("~$"):
                continue
            if os.path.splitext(n)[1].lower() in [".txt", ".md", ".pdf", ".docx", ".json", ".fountain", ".srt"]:
                files.append(os.path.join(root, n))
    eps = []
    failed = []
    seen = set()
    order = 0
    for path in files:
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".json":
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
                num = None
                if isinstance(data, dict):
                    num = data.get("episode") or data.get("episode_number")
                if num is None:
                    num = ep_from_name(path)
                chunks = [{"episode": num, "rows": rows_from_json(data)}]
            else:
                raw = read_text(path)
                chunks = []
                for c in split_episodes(raw, path):
                    chunks.append({"episode": c["episode"], "rows": rows_from_text(c["text"])})
            for c in chunks:
                order += 1
                num = c["episode"]
                if num is None:
                    num = order
                num = int(num)
                if num in seen:
                    failed.append({"file": path, "episode": num, "error": "duplicate episode number"})
                    continue
                if not c["rows"]:
                    failed.append({"file": path, "episode": num, "error": "no lines parsed"})
                    continue
                seen.add(num)
                ep = build_episode(num, c["rows"], pcfg)
                ep["source_file"] = os.path.relpath(path, inp)
                eps.append(ep)
        except Exception as ex:
            failed.append({"file": path, "episode": None, "error": f"{type(ex).__name__}: {ex}"})
    eps.sort(key=lambda e: e["episode"])
    mark_recaps(eps, pcfg)
    if out_dir:
        d = os.path.join(out_dir, "01_parsed")
        os.makedirs(d, exist_ok=True)
        for e in eps:
            with open(os.path.join(d, f"ep_{e['episode']:02d}.json"), "w", encoding="utf-8") as f:
                json.dump(e, f, indent=1, ensure_ascii=False)
        with open(os.path.join(d, "_report.json"), "w", encoding="utf-8") as f:
            json.dump(make_report(eps, failed), f, indent=1, ensure_ascii=False)
    return eps, failed


def make_report(eps, failed):
    res = {"episodes": len(eps), "failed": failed, "per_episode": []}
    for e in eps:
        cnt = {"scene": 0, "action": 0, "dialogue": 0, "direction": 0}
        est = 0
        rec = 0
        for ln in e["lines"]:
            cnt[ln["type"]] += 1
            if ln["estimated_time"]:
                est += 1
            if ln["is_recap"]:
                rec += 1
        res["per_episode"].append({"episode": e["episode"], "lines": len(e["lines"]), "duration_sec": e["duration_sec"],
                                   "types": cnt, "estimated": est, "recap": rec, "oddities": e["oddities"]})
    return res


def print_report(eps, failed):
    rep = make_report(eps, failed)
    print(f"parsed {rep['episodes']} episodes, {len(failed)} failed")
    for r in rep["per_episode"]:
        temp = f"  ep {r['episode']:>3}: {r['lines']:>4} lines  {r['duration_sec']:>6.1f}s  est={r['estimated']}  recap={r['recap']}"
        if r["oddities"]:
            temp += f"  oddities={len(r['oddities'])}"
        print(temp)
    for f in failed:
        print(f"  FAILED {f['file']}: {f['error']}")
