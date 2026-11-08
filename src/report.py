import os
import csv
import json
import glob
import html
from src.parse import fmt_ts
from src.common import save, load


def usage_summary(out_dir, cfg):
    path = os.path.join(out_dir, "usage.jsonl")
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    last = None
    for r in rows:
        if r.get("run_id"):
            last = r["run_id"]
    cur = []
    for r in rows:
        if r.get("run_id") == last:
            cur.append(r)
    res = {"run_id": last, "steps": {}, "calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
           "spent_prompt_tokens_all_runs": 0, "spent_completion_tokens_all_runs": 0, "models": []}
    for r in rows:
        if not r.get("cached"):
            res["spent_prompt_tokens_all_runs"] += r["prompt_tokens"]
            res["spent_completion_tokens_all_runs"] += r["completion_tokens"]
    res["by_model"] = {}
    res["cost_usd"] = 0.0
    for r in cur:
        st = res["steps"].setdefault(r["step"], {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency": 0.0, "retries": 0})
        st["calls"] += 1
        res["calls"] += 1
        if r.get("cached"):
            st["cached"] += 1
            res["cached_calls"] += 1
        st["prompt_tokens"] += r["prompt_tokens"]
        st["completion_tokens"] += r["completion_tokens"]
        st["latency"] = round(st["latency"] + r.get("latency", 0), 2)
        if r.get("attempts", 1) > 1:
            st["retries"] += r["attempts"] - 1
        res["prompt_tokens"] += r["prompt_tokens"]
        res["completion_tokens"] += r["completion_tokens"]
        if r["model"] not in res["models"]:
            res["models"].append(r["model"])
        bm = res["by_model"].setdefault(r["model"], {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
        bm["calls"] += 1
        if r.get("cached"):
            bm["cached"] += 1
        bm["prompt_tokens"] += r["prompt_tokens"]
        bm["completion_tokens"] += r["completion_tokens"]
        bm["cost_usd"] = round(bm["cost_usd"] + r.get("cost_usd", 0), 6)
        res["cost_usd"] += r.get("cost_usd", 0)
    res["cost_usd"] = round(res["cost_usd"], 6)
    all_cost = 0.0
    for r in rows:
        all_cost += r.get("cost_usd", 0)
    res["cost_usd_all_runs"] = round(all_cost, 6)
    return res


def load_logs(out_dir, prefix):
    res = []
    for p in sorted(glob.glob(os.path.join(out_dir, "logs", prefix + "_*.json"))):
        res.extend(load(p))
    return res


def run_summary(out_dir, cfg, sel):
    res = {}
    p = os.path.join(out_dir, "01_parsed", "_report.json")
    if os.path.exists(p):
        rep = load(p)
        res["episodes"] = rep["episodes"]
        res["parse_failed"] = len(rep["failed"])
        est = 0
        rec = 0
        for e in rep["per_episode"]:
            est += e["estimated"]
            rec += e["recap"]
        res["estimated_time_lines"] = est
        res["recap_lines"] = rec
    cnt = 0
    raw = 0
    sizes = {"long": 0, "medium": 0, "small": 0}
    for f in glob.glob(os.path.join(out_dir, "04_shots", "ep_*.json")):
        d = load(f)
        cnt += len(d["shots"])
        raw += d["raw_count"]
        for s in d["shots"]:
            sizes[s["size"]] += 1
    res["shots_proposed"] = raw
    res["shots_kept"] = cnt
    res["shots_by_size"] = sizes
    p = os.path.join(out_dir, "05_candidates.json")
    if os.path.exists(p):
        c = load(p)
        res.update(c["fill"])
        net = 0
        for x in c["candidates"]:
            if x["source"] == "safety_net":
                net += 1
        res["safety_net_candidates"] = net
        res["candidates"] = len(c["candidates"])
    p = os.path.join(out_dir, "06_assembled.json")
    if os.path.exists(p):
        res["assembled"] = len(load(p))
    res["eligible_after_rules"] = sel.get("eligible", 0)
    res["selected"] = len(sel["selected"])
    drops = load_logs(out_dir, "drops")
    by = {}
    for d in drops:
        k = f"{d['step']}:{d['reason']}"
        by[k] = by.get(k, 0) + 1
    res["drops_by_reason"] = dict(sorted(by.items()))
    res["failures"] = load_logs(out_dir, "failed")
    return res


def fmt_cut(sec):
    # exact cut points for editors (the script has centiseconds); whole-second rounding could clip a line
    c = int(round(sec * 100))
    m = c // 6000
    s = (c % 6000) / 100
    return f"{m:02d}:{s:05.2f}"


def seg_str(s):
    return f"ep{s['episode']} {fmt_cut(s['start'])}-{fmt_cut(s['end'])}"


def final_promo(p):
    segs = []
    for s in p["segments"]:
        segs.append({"episode": s["episode"], "start": fmt_cut(s["start"]), "end": fmt_cut(s["end"]),
                     "start_sec": s["start"], "end_sec": s["end"], "start_line_id": s["start_line_id"], "end_line_id": s["end_line_id"]})
    sc = p["scores"]
    return {"rank": p["rank"], "promo_id": p["final_id"], "template": p["template"], "source": p["source"], "segments": segs,
            "total_duration_sec": round(p["total_duration_sec"]), "hook": p["hook"], "cliffhanger": p["cliffhanger"],
            "why_it_works": p["why_it_works"],
            "scores": {"hook": sc["hook"], "cliffhanger": sc["cliffhanger"], "clarity": sc["clarity"], "coherence": sc["coherence"],
                       "duration": sc["duration"], "spoiler_penalty": sc["spoiler_penalty"], "total": sc["total"]},
            "confidence": p["confidence"], "flags": p["flags"], "confusion": p.get("confusion", ""), "idea": p["idea"],
            "internal_id": p["promo_id"], "editor_decision": ""}


def seg_lines(p, eps_by):
    res = []
    for s in p["segments"]:
        lines = eps_by[s["episode"]]["lines"]
        arr = []
        for j in range(s["a"], s["b"]+1):
            ln = lines[j]
            who = ln["type"].upper()
            if ln["type"] == "dialogue":
                who = ln["speaker"] or "UNKNOWN"
            arr.append({"ts": fmt_ts(ln["start"]), "who": who, "text": ln["text"], "id": ln["id"], "key": j in [s["hook_key"]]})
        res.append({"label": seg_str(s), "roles": s["roles"], "lines": arr})
    return res


def write_md(path, finals, promos, eps_by, sel, summ, use, base):
    out = ["# Promo report", "", f"Kuku Cut · Built by {AUTHOR}", ""]
    if sel["note"]:
        out.append(f"**Note:** {sel['note']}")
        out.append("")
    for f, p in zip(finals, promos):
        sc = f["scores"]
        out.append(f"## #{f['rank']} {f['promo_id']} · {f['template']} · {f['total_duration_sec']}s · total {sc['total']}")
        out.append("")
        out.append(f"- **Segments:** {' | '.join(seg_str(s) for s in p['segments'])}")
        out.append(f"- **Hook:** {f['hook']}")
        out.append(f"- **Cliffhanger:** {f['cliffhanger']}")
        out.append(f"- **Why it works:** {f['why_it_works']}")
        out.append(f"- **Scores:** hook {sc['hook']} · cliffhanger {sc['cliffhanger']} · clarity {sc['clarity']} · coherence {sc['coherence']} · duration {sc['duration']} · spoiler -{sc['spoiler_penalty']}")
        out.append(f"- **Confidence:** {f['confidence']} · **source:** {f['source']} · **flags:** {', '.join(f['flags']) or 'none'}")
        if f["confusion"]:
            out.append(f"- **Fresh viewer:** {f['confusion']}")
        out.append("")
        for sg in seg_lines(p, eps_by):
            out.append(f"**{sg['label']}** ({', '.join(sg['roles'])})")
            out.append("```")
            for ln in sg["lines"]:
                out.append(f"[{ln['ts']}] {ln['who']}: {ln['text']}")
            out.append("```")
        out.append("")
    out.append("## Best per template")
    out.append("")
    for t, b in sel["best_per_template"].items():
        out.append(f"- {t}: {b.get('final_id', b['promo_id'])} total {b['total']} (selected: {b['selected']})")
    out.append("")
    out.append("## Run summary")
    out.append("```")
    out.append(json.dumps(summ, indent=1))
    out.append("```")
    if base:
        out.append("## Baseline (last 30 s of each episode)")
        out.append("```")
        out.append(json.dumps(base, indent=1))
        out.append("```")
    out.append("## Usage")
    out.append("```")
    out.append(json.dumps(use, indent=1))
    out.append("```")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


CSS = """
:root{--bg:#f7f7f5;--card:#ffffff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e3e2dd;--accent:#b4380f;--key:#fff1d6;--chip:#efeee9}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#f08a5d;--key:#3a2d14;--chip:#2a2a27}}
:root[data-theme="dark"]{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#f08a5d;--key:#3a2d14;--chip:#2a2a27}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
.sub{color:var(--muted);margin:0 0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.rank{font-weight:700;font-size:20px;color:var(--accent)}
.chip{background:var(--chip);border-radius:999px;padding:1px 9px;font-size:12px;color:var(--muted)}.total{margin-left:auto;font-weight:700;font-size:18px}
dl{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;margin:10px 0}dt{color:var(--muted)}dd{margin:0}
.scores{font-size:13px;color:var(--muted)}pre{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;font:13px/1.45 ui-monospace,Consolas,monospace;margin:6px 0 10px;overflow-x:auto}
.seg{font-size:13px;font-weight:600;margin-top:8px}.k{background:var(--key)}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}details summary{cursor:pointer;color:var(--muted)}
@media (max-width:600px){dl{grid-template-columns:1fr}.total{margin-left:0}}
"""


AUTHOR = "Divyansh Gupta"


def esc(x):
    return html.escape(str(x))


def write_html(path, finals, promos, eps_by, sel, summ, use, base):
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>Promo Report</title><style>" + CSS + "</style></head><body><main>",
           f"<h1>Promo report</h1><p class='sub'>Kuku Cut · Built by {AUTHOR}</p>",
           f"<p class='sub'>{len(finals)} ranked promos. Every segment shows its screenplay lines so an editor can verify it quickly. Highlighted line = hook.</p>"]
    if sel["note"]:
        out.append(f"<p><b>Note:</b> {esc(sel['note'])}</p>")
    for f, p in zip(finals, promos):
        sc = f["scores"]
        out.append("<div class='card'><div class='top'>")
        out.append(f"<span class='rank'>#{f['rank']}</span><b>{esc(f['promo_id'])}</b><span class='chip'>{esc(f['template'])}</span>"
                   f"<span class='chip'>{f['total_duration_sec']}s</span><span class='chip'>{esc(f['source'])}</span>"
                   f"<span class='chip'>confidence {esc(f['confidence'])}</span><span class='total'>{sc['total']}</span></div>")
        out.append("<dl>")
        out.append(f"<dt>Segments</dt><dd>{esc(' | '.join(seg_str(s) for s in p['segments']))}</dd>")
        out.append(f"<dt>Hook</dt><dd>{esc(f['hook'])}</dd><dt>Cliffhanger</dt><dd>{esc(f['cliffhanger'])}</dd>")
        out.append(f"<dt>Why</dt><dd>{esc(f['why_it_works'])}</dd>")
        if f["confusion"]:
            out.append(f"<dt>Fresh viewer</dt><dd>{esc(f['confusion'])}</dd>")
        if f["flags"]:
            out.append(f"<dt>Flags</dt><dd>{esc(', '.join(f['flags']))}</dd>")
        out.append("</dl>")
        out.append(f"<div class='scores'>hook {sc['hook']} · cliffhanger {sc['cliffhanger']} · clarity {sc['clarity']} · coherence {sc['coherence']} · duration {sc['duration']} · spoiler −{sc['spoiler_penalty']}</div>")
        for sg in seg_lines(p, eps_by):
            out.append(f"<div class='seg'>{esc(sg['label'])} <span class='chip'>{esc(', '.join(sg['roles']))}</span></div><pre>")
            for ln in sg["lines"]:
                temp = f"[{ln['ts']}] {esc(ln['who'])}: {esc(ln['text'])}"
                if ln["key"]:
                    temp = f"<span class='k'>{temp}</span>"
                out.append(temp)
            out.append("</pre>")
        out.append("</div>")
    out.append("<h2>Best per template</h2><table><tr><th>Template</th><th>Promo</th><th>Total</th><th>Selected</th></tr>")
    for t, b in sel["best_per_template"].items():
        out.append(f"<tr><td>{esc(t)}</td><td>{esc(b.get('final_id', b['promo_id']))}</td><td>{b['total']}</td><td>{b['selected']}</td></tr>")
    out.append("</table><h2>Run summary</h2><table>")
    for k, v in summ.items():
        if k in ["drops_by_reason", "failures", "shots_by_size"]:
            continue
        out.append(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>")
    out.append(f"<tr><td>shots_by_size</td><td>{esc(summ.get('shots_by_size'))}</td></tr></table>")
    out.append("<h2>Drops by reason</h2><table>")
    for k, v in summ.get("drops_by_reason", {}).items():
        out.append(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>")
    out.append("</table>")
    if summ.get("failures"):
        out.append(f"<details><summary>{len(summ['failures'])} step failures</summary><pre>{esc(json.dumps(summ['failures'], indent=1))}</pre></details>")
    if base:
        out.append("<h2>Baseline: last 30 s of each episode</h2><table><tr><th>Metric</th><th>Tool top 15</th><th>Baseline top 15</th></tr>")
        for k, v in base["by_metric"].items():
            out.append(f"<tr><td>{esc(k)}</td><td>{v['tool']}</td><td>{v['baseline']}</td></tr>")
        out.append("</table>")
    out.append("<h2>Usage</h2><table><tr><th>Step</th><th>Calls</th><th>Cached</th><th>Retries</th><th>Prompt tok</th><th>Completion tok</th><th>Latency s</th></tr>")
    for k, v in use["steps"].items():
        out.append(f"<tr><td>{esc(k)}</td><td>{v['calls']}</td><td>{v['cached']}</td><td>{v['retries']}</td><td>{v['prompt_tokens']}</td><td>{v['completion_tokens']}</td><td>{v['latency']}</td></tr>")
    out.append(f"<tr><th>total</th><th>{use['calls']}</th><th>{use['cached_calls']}</th><th></th><th>{use['prompt_tokens']}</th><th>{use['completion_tokens']}</th><th></th></tr></table>")
    out.append("<table><tr><th>Model</th><th>Calls</th><th>Cached</th><th>Prompt tok</th><th>Completion tok</th><th>Cost $</th></tr>")
    for k, v in use["by_model"].items():
        out.append(f"<tr><td>{esc(k)}</td><td>{v['calls']}</td><td>{v['cached']}</td><td>{v['prompt_tokens']}</td><td>{v['completion_tokens']}</td><td>{v['cost_usd']}</td></tr>")
    out.append("</table>")
    out.append(f"<p class='sub'>Cost this run: ${use['cost_usd']} (all runs in this folder: ${use['cost_usd_all_runs']}). Cached calls cost nothing.</p>")
    out.append(f"<p class='sub' style='margin-top:32px;text-align:center'>Kuku Cut · Built by {AUTHOR}</p>")
    out.append("</main></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


def run(sel, eps, cfg, out_dir):
    eps_by = {}
    for e in eps:
        eps_by[e["episode"]] = e
    promos = sel["selected"]
    finals = []
    for p in promos:
        finals.append(final_promo(p))
    d = os.path.join(out_dir, "09_final")
    os.makedirs(d, exist_ok=True)
    save(os.path.join(d, "promos.json"), finals)
    with open(os.path.join(d, "promos.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "promo_id", "template", "source", "segments", "total_duration_sec", "hook", "cliffhanger", "why_it_works",
                    "score_hook", "score_cliffhanger", "score_clarity", "score_coherence", "score_duration", "spoiler_penalty", "score_total",
                    "confidence", "flags", "editor_decision"])
        for f, p in zip(finals, promos):
            sc = f["scores"]
            segs = []
            for s in p["segments"]:
                segs.append(seg_str(s))
            w.writerow([f["rank"], f["promo_id"], f["template"], f["source"], " | ".join(segs), f["total_duration_sec"], f["hook"],
                        f["cliffhanger"], f["why_it_works"], sc["hook"], sc["cliffhanger"], sc["clarity"], sc["coherence"], sc["duration"],
                        sc["spoiler_penalty"], sc["total"], f["confidence"], ";".join(f["flags"]), ""])
    best = {}
    for t, b in sel["best_per_template"].items():
        best[t] = b
    save(os.path.join(d, "best_per_template.json"), best)
    summ = run_summary(out_dir, cfg, sel)
    use = usage_summary(out_dir, cfg)
    base = None
    p = os.path.join(out_dir, "10_baseline.json")
    if os.path.exists(p):
        base = load(p)["comparison"]
    save(os.path.join(d, "run_summary.json"), {"run": summ, "usage": use, "baseline": base})
    write_md(os.path.join(d, "report.md"), finals, promos, eps_by, sel, summ, use, base)
    write_html(os.path.join(d, "report.html"), finals, promos, eps_by, sel, summ, use, base)
    print(f"  wrote {d}")
    return finals, summ, use
