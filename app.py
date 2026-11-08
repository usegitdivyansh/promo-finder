import os
import io
import copy
import json
import html
import time
import hashlib
import zipfile
import tempfile
import threading
import yaml
import streamlit as st

from src import llm, parse, check_rules
from src.common import load, load_dir
from src.report import fmt_cut
from src.estimate import estimate
import run as pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(ROOT, "demo", "synthetic_show")
EXTS = ["txt", "md", "pdf", "docx", "json", "fountain", "srt"]
MAX_FILES = 300
MAX_BYTES = 30 * 1024 * 1024

# provider -> (litellm prefix, default model name)
PROVIDERS = {
    "OpenRouter": ("openrouter", "deepseek/deepseek-v4.1-flash"),
    "Google Gemini": ("gemini", "gemini-3.5-flash-lite"),
    "OpenAI": ("openai", ""),
    "Anthropic": ("anthropic", ""),
    "Groq": ("groq", ""),
    "Other (full litellm name)": ("", ""),
}
STEP_NAMES = {"step1": "Parse screenplays", "step2": "Summarise episodes", "step3": "Build story bible", "step4": "Find shots",
              "step5": "Write promo scripts", "step6": "Assemble cuts", "step7": "Judge promos", "step8": "Select",
              "baseline": "Score the last-30 s baseline", "step9": "Write report"}

AUTHOR = "Divyansh Gupta"
TITLE = f"Kuku Cut: Kuku AI Generalist Assignment, by {AUTHOR}"

TAGLINE = "Find the moment worth promoting"

st.set_page_config(page_title=TITLE, page_icon="✂️", layout="wide", initial_sidebar_state="expanded",
                   menu_items={"Get help": None, "Report a bug": None, "About": f"Kuku Cut · Built by {AUTHOR}"})


def load_css():
    # every style lives in ui/style.css (colours, fonts and spacing are variables at its top)
    with open(os.path.join(ROOT, "ui", "style.css"), encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()

# inline feather-style icons; no hosted images
ICONS = {
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "spark": '<path d="M12 3l1.8 5.4L19 10l-5.2 1.6L12 17l-1.8-5.4L5 10l5.2-1.6z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    "cut": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
}
WORKFLOW = [("01", "Upload", "your episode screenplays", "upload"),
            ("02", "Discover", "AI finds the most promo-worthy moments", "spark"),
            ("03", "Extract", "cut points, hooks, cliffhangers and why", "cut")]


@st.cache_resource
def run_lock():
    # the pipeline keeps module-level state, so one real run at a time per server
    return threading.Lock()


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def safe_name(name, used):
    base = os.path.basename(name.replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = "".join(c for c in stem if c.isalnum() or c in " -_.")[:80] or "file"
    res = stem + ext.lower()
    i = 2
    while res in used:
        res = f"{stem}_{i}{ext.lower()}"
        i += 1
    used.add(res)
    return res


def save_uploads(files, dest):
    # flatten everything into one folder; zip entries never choose their own path
    used = set()
    total = 0
    count = 0
    for f in files:
        data = f.getvalue()
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.infolist():
                    if m.is_dir() or "__MACOSX" in m.filename:
                        continue
                    base = os.path.basename(m.filename)
                    if not base or base.startswith(".") or base.rsplit(".", 1)[-1].lower() not in EXTS:
                        continue
                    total += m.file_size
                    count += 1
                    if total > MAX_BYTES or count > MAX_FILES:
                        raise ValueError(f"upload too large (limit {MAX_FILES} files, {MAX_BYTES // 2**20} MB unpacked)")
                    with open(os.path.join(dest, safe_name(base, used)), "wb") as out:
                        out.write(z.read(m))
        else:
            total += len(data)
            count += 1
            if total > MAX_BYTES or count > MAX_FILES:
                raise ValueError(f"upload too large (limit {MAX_FILES} files, {MAX_BYTES // 2**20} MB)")
            with open(os.path.join(dest, safe_name(f.name, used)), "wb") as out:
                out.write(data)


def zip_dir(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(path):
            for n in names:
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, path))
    return buf.getvalue()


def model_name(provider, name):
    pre = PROVIDERS[provider][0]
    name = name.strip()
    if not pre or name.startswith(pre + "/"):
        return name
    return f"{pre}/{name}"


def redact(msg, key):
    if key:
        for frag in [key, key[:12], key[-8:]]:
            if len(frag) >= 8:
                msg = msg.replace(frag, "[key]")
    return msg


def run_cfg(base, model, cap, num, fresh, do_base):
    cfg = copy.deepcopy(base)
    lc = cfg["llm"]
    # one model, one key: no step models, no fallback provider, no confidential override
    lc["provider_model"] = model
    lc["step_models"] = {}
    lc["judge_model"] = ""
    lc["fallback_model"] = None
    lc["confidential"] = False
    lc["run_cap_usd"] = float(cap)
    lc["budget_usd"] = 1e9
    cfg["promo"]["num_promos_max"] = num
    if cfg["promo"]["num_promos_min"] > num:
        cfg["promo"]["num_promos_min"] = num
    cfg["judge"]["fresh"] = fresh
    cfg["_baseline"] = do_base
    cfg["_episodes"] = None
    cfg["_aliases"] = None
    return cfg


def upload_sig(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(str(f.size).encode())
    return h.hexdigest()[:16]


def parsed_upload(files, cfg):
    # parse once per upload (code only, no llm) for the estimate; reused for the run
    sig = upload_sig(files)
    got = st.session_state.get("upload")
    if got and got["sig"] == sig:
        return got
    work = tempfile.mkdtemp(prefix="promo_")
    inp = os.path.join(work, "input")
    os.makedirs(inp)
    err = None
    eps = []
    failed = []
    try:
        save_uploads(files, inp)
        eps, failed = parse.parse_folder(inp, cfg)
    except Exception as ex:
        err = str(ex)[:300]
    got = {"sig": sig, "work": work, "inp": inp, "eps": eps, "failed": failed, "err": err}
    st.session_state["upload"] = got
    return got


def usage_cost(out):
    p = os.path.join(out, "usage.jsonl")
    calls = 0
    cached = 0
    cost = 0.0
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    calls += 1
                    if r.get("cached"):
                        cached += 1
                    cost += r.get("cost_usd") or 0
    return calls, cached, cost


def esc(x):
    return html.escape(str(x))


def svg(name):
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def status_html(kind, text):
    # kind: active (amber), idle (gray) or error (red). text is html built in this file; escape anything external first
    return f'<div class="status {kind}"><span class="dot"></span><span>{text}</span></div>'


def render_status(kind, text):
    st.markdown(status_html(kind, text), unsafe_allow_html=True)


def render_hero():
    st.markdown(f'''<div class="hero"><div class="eyebrow lbl">Kuku AI Generalist Assignment · by {AUTHOR}</div>
<div class="ttl" role="heading" aria-level="1">Kuku Cut<span class="live" title="live"></span></div>
<p class="tag-line">{TAGLINE}</p>
<p class="lede">Upload timestamped episode screenplays and get ranked promo cuts back: exact cut times, the hook, the cliffhanger and why each one works. An editor still approves and cuts.</p></div>''',
                unsafe_allow_html=True)


def render_steps():
    items = ""
    for num, name, desc, ic in WORKFLOW:
        items += (f'<div class="step"><div class="ic">{svg(ic)}</div><div class="num">{num}</div>'
                  f'<div class="name">{name}</div><div class="desc">{desc}</div></div>')
    st.markdown(f'<div class="steps">{items}</div>', unsafe_allow_html=True)


def render_stat_tile(value, label, cls=""):
    return f'<div class="tile {cls}"><b>{value}</b><span class="lbl">{label}</span></div>'


def render_estimate(est, model, cap):
    t = est["total"]
    pi, po = est["price_per_1m"]
    tokens = (t["prompt_tokens"] + t["completion_tokens"]) / 1000
    st.markdown(f'''<div class="lbl">Estimate for {est["episodes"]} episodes</div><div class="est">
<div><b>~{t["calls"]}</b><span class="lbl">LLM calls</span></div><div><b>{tokens:,.0f}k</b><span class="lbl">tokens</span></div>
<div><b>~${t["usd"]:.2f}</b><span class="lbl">cost</span></div></div>
<p class="est-note">At ${pi:g} / ${po:g} per 1M tokens for <code>{esc(model)}</code>. A rough estimate from token use on earlier runs; reasoning models can use more. The spend cap is the hard limit.</p>''',
                unsafe_allow_html=True)
    if t["usd"] > cap:
        render_status("error", f"The estimate is above your <b>${cap:.2f}</b> cap, so the run will probably stop early. Raise the cap or upload fewer episodes.")


def score_class(v, hi, mid):
    if v >= hi:
        return "hi"
    if v >= mid:
        return "mid"
    return "lo"


def chip(label, v, hi, mid):
    return f'<span class="chip {score_class(v, hi, mid)}">{label} <span class="mono">{v}</span></span>'


def segments_html(p, eps_by):
    # cut times as data: episode, in, out, length, role
    rows = ""
    for i, s in enumerate(p["segments"]):
        lines = eps_by[s["episode"]]["lines"]
        a = lines[s["a"]]["start"]
        b = lines[s["b"]]["end"]
        rows += (f'<div class="seg"><span class="k">CUT {i+1}</span><span>EP {s["episode"]}</span>'
                 f'<span>{fmt_cut(a)} → {fmt_cut(b)}</span><span class="m"><span class="d">{b - a:.1f}s</span>'
                 f'<span class="r">{esc(", ".join(s["roles"]))}</span></span></div>')
    return f'<div class="segs"><span class="lbl">Segments</span>{rows}</div>'


def timeline_html(p, eps_by):
    # one slim bar per episode, every cut from that episode highlighted on it
    order = []
    segs = {}
    for s in p["segments"]:
        if s["episode"] not in segs:
            order.append(s["episode"])
            segs[s["episode"]] = []
        segs[s["episode"]].append(s)
    rows = ""
    for ep in order:
        lines = eps_by[ep]["lines"]
        dur = eps_by[ep].get("duration_sec") or lines[-1]["end"] or 1
        spans = ""
        for s in segs[ep]:
            a = lines[s["a"]]["start"]
            b = lines[s["b"]]["end"]
            left = max(0.0, min(100.0, a / dur * 100))
            width = max(0.8, min(100.0 - left, (b - a) / dur * 100))
            spans += f'<span style="left:{left:.2f}%;width:{width:.2f}%" title="{fmt_cut(a)} → {fmt_cut(b)}"></span>'
        rows += (f'<div class="tl-row"><span class="ep">EP {ep}</span><div class="track">{spans}</div>'
                 f'<span class="len">{int(dur // 60)}:{int(dur % 60):02d}</span></div>')
    return f'<div class="tl">{rows}</div>'


def phone_html(p, eps_by):
    # 9:16 preview: the promo's dialogue and action as captions, promo time from 0, hook and cliffhanger marked
    caps = []
    off = 0.0
    for i, s in enumerate(p["segments"]):
        lines = eps_by[s["episode"]]["lines"]
        t0 = lines[s["a"]]["start"]
        for j in range(s["a"], s["b"]+1):
            ln = lines[j]
            if ln["type"] not in ["dialogue", "action"]:
                continue
            t = ln["start"] - t0 + off
            cls = "cap"
            if i == 0 and j == s.get("hook_key"):
                cls = "cap hk"
            txt = f"<i>{esc(ln['text'])}</i>"
            if ln["type"] == "dialogue":
                txt = f"<b>{esc(ln['speaker'] or '')}</b> {esc(ln['text'])}"
            caps.append((cls, f'<span class="t">{int(t // 60)}:{int(t % 60):02d}</span>{txt}'))
        off += lines[s["b"]]["end"] - t0
    cliff = ""
    if caps:
        cliff = f'<div class="pv-cf"><div class="cap cf">{caps[-1][1]}</div></div>'
        caps = caps[:-1]
    body = ""
    for cls, txt in caps:
        body += f'<div class="{cls}">{txt}</div>'
    first = p["segments"][0]["episode"]
    return (f'<div class="phone"><div class="screen"><div class="notch"></div>'
            f'<div class="pv-top"><span>EP {first}</span><span>{off:.0f} s</span></div>'
            f'<div class="pv-caps">{body}</div>{cliff}<div class="pv-end">Next episode ▸</div></div></div>')


def promo_row_html(f, p, eps_by):
    sc = f["scores"]
    conf = str(f["confidence"]).lower()
    d = f["total_duration_sec"]
    head = (f'<div class="pr-head"><span class="pr-rank"><small>#</small>{f["rank"]:02d}</span>'
            f'<span class="pr-score">{sc["total"]}<small>/100</small></span>'
            f'<span class="pr-tags"><span class="tag">{esc(f["template"].replace("_", " "))}</span>'
            f'<span class="conf {esc(conf)}">{esc(conf)} confidence</span></span></div>')
    # bar runs 0-60 s; the lighter part is the 20-60 s target window
    fill = max(2.0, min(100.0, d / 60 * 100))
    dur = (f'<div class="dur"><div class="dbar"><div class="win"></div><div class="fill" style="width:{fill:.1f}%"></div></div>'
           f'<small><span>{d:.1f} s</span><span>target 20–60 s</span></small></div>')
    quotes = (f'<div class="pq hook"><span class="lbl">Hook</span><q>{esc(f["hook"])}</q></div>'
              f'<div class="pq cliff"><span class="lbl">Cliffhanger</span><q>{esc(f["cliffhanger"])}</q></div>'
              f'<p class="why"><span class="lbl">Why it works</span><br>{esc(f["why_it_works"])}</p>')
    pen = "hi"
    if sc["spoiler_penalty"]:
        pen = "lo"
    scores = ('<div class="scores">' + chip("hook", sc["hook"], 8, 6) + chip("cliffhanger", sc["cliffhanger"], 8, 6)
              + chip("clarity", sc["clarity"], 8, 6) + chip("coherence", sc["coherence"], 8, 6)
              + chip("duration", sc["duration"], 8, 6)
              + f'<span class="chip {pen}">spoiler <span class="mono">−{sc["spoiler_penalty"]}</span></span></div>')
    main = head + dur + quotes + segments_html(p, eps_by) + timeline_html(p, eps_by) + scores
    return f'<div class="pr"><div>{main}</div>{phone_html(p, eps_by)}</div>'


def lines_html(s, eps_by):
    lines = eps_by[s["episode"]]["lines"]
    out = []
    for j in range(s["a"], s["b"]+1):
        ln = lines[j]
        who = ln["type"].upper()
        if ln["type"] == "dialogue":
            who = ln["speaker"] or "UNKNOWN"
        cls = "ln"
        if j == s.get("hook_key"):
            cls = "ln key"
        out.append(f'<div class="{cls}"><span class="ts">{fmt_cut(ln["start"])}</span><span class="who">{html.escape(who)}</span>: {html.escape(ln["text"])}</div>')
    return "".join(out)


def render_promo_row(f, p, eps_by):
    # the container key carries the score grade, which colours the left accent bar
    grade = score_class(f["scores"]["total"], 80, 65)
    with st.container(key=f"promo-{grade}-{f['rank']}"):
        st.markdown(promo_row_html(f, p, eps_by), unsafe_allow_html=True)
        with st.expander("Screenplay lines"):
            if f.get("confusion"):
                st.markdown(status_html("idle", f"A fresh viewer might ask: {esc(f['confusion'])}"), unsafe_allow_html=True)
            for i, s in enumerate(p["segments"]):
                lines = eps_by[s["episode"]]["lines"]
                st.markdown(f'<div class="lines-h">CUT {i+1} · EP {s["episode"]} · {fmt_cut(lines[s["a"]]["start"])} → '
                            f'{fmt_cut(lines[s["b"]]["end"])} · {esc(", ".join(s["roles"]))}</div>' + lines_html(s, eps_by),
                            unsafe_allow_html=True)


def rules_passed(out):
    # from rule_check.json (written by src.check_rules); informational checks with ok = None don't count
    p = os.path.join(out, "rule_check.json")
    if not os.path.exists(p):
        return None
    res = load(p)
    n = 0
    ok = 0
    for k, v in res.items():
        if k == "all_ok" or not isinstance(v, dict) or v.get("ok") is None:
            continue
        n += 1
        if v["ok"]:
            ok += 1
    return ok, n


def summary_html(eps, promos, rules, cost):
    rv = "n/a"
    rc = ""
    if rules:
        rv = f"{rules[0]}/{rules[1]}"
        rc = "bad"
        if rules[0] == rules[1]:
            rc = "ok"
    tiles = (render_stat_tile(eps, "Episodes processed") + render_stat_tile(promos, "Promos found")
             + render_stat_tile(rv, "Rules passed", rc) + render_stat_tile(cost, "Cost of this run"))
    return f'<div class="sum">{tiles}</div>'


def render_downloads(out):
    d = os.path.join(out, "09_final")
    cols = st.columns(4)
    cols[0].download_button("promos.json", open(os.path.join(d, "promos.json"), "rb").read(), "promos.json", use_container_width=True)
    cols[1].download_button("promos.csv", open(os.path.join(d, "promos.csv"), "rb").read(), "promos.csv", use_container_width=True)
    cols[2].download_button("report.html", open(os.path.join(d, "report.html"), "rb").read(), "report.html", use_container_width=True)
    zk = "zip_" + out
    if zk not in st.session_state:
        st.session_state[zk] = zip_dir(out)
    cols[3].download_button("raw outputs (zip)", st.session_state[zk], "raw_outputs.zip", use_container_width=True)


def results(out, demo):
    finals = load(os.path.join(out, "09_final", "promos.json"))
    sel = load(os.path.join(out, "08_selected.json"))
    summ = load(os.path.join(out, "09_final", "run_summary.json"))
    eps_by = {}
    for e in load_dir(os.path.join(out, "01_parsed")):
        eps_by[e["episode"]] = e
    by_id = {}
    for p in sel["selected"]:
        by_id[p["promo_id"]] = p
    calls, cached, cost = usage_cost(out)
    cost_txt = f"${cost:.3f}"
    if demo:
        cost_txt = "$0"
    with st.container(key="results"):
        st.markdown('<div class="sec-h">Results</div>', unsafe_allow_html=True)
        if demo:
            render_status("idle", "<b>Demo:</b> a made-up 5-episode show. Rebuilt from saved model responses, so this view made no LLM calls and cost nothing.")
        st.markdown(summary_html(summ["run"].get("episodes", 0), len(finals), rules_passed(out), cost_txt), unsafe_allow_html=True)
        if sel["note"]:
            render_status("error", esc(sel["note"]))
        b = summ.get("baseline")
        if b:
            render_status("idle", f"Same judges on the last 30 s of each episode (baseline): top-15 mean "
                                  f"<b>{b['mean_total_baseline_top15']}</b> vs tool <b>{b['mean_total_tool_top15']}</b>.")
        st.markdown('<div class="sec-h" style="margin-top:28px">Ranked cuts</div>', unsafe_allow_html=True)
        with st.container(key="promolist"):
            for f in finals:
                render_promo_row(f, by_id[f["internal_id"]], eps_by)
        st.markdown('<div class="sec-h" style="margin-top:28px">Downloads</div>', unsafe_allow_html=True)
        render_downloads(out)
        if not demo:
            render_status("idle", f"{calls} LLM calls ({cached} from this session's cache).")


def render_sidebar(base_cfg):
    # widgets, labels and order are unchanged; the key lives only in this session's widget state
    with st.sidebar:
        st.markdown('<div class="side-h">Model</div>', unsafe_allow_html=True)
        provider = st.selectbox("Provider", list(PROVIDERS))
        name = st.text_input("Model name", value=PROVIDERS[provider][1], key="model_" + provider,
                             help="As the provider names it. With 'Other', the full litellm name, e.g. mistral/mistral-small-latest.")
        api_key = st.text_input("API key", type="password", help="Used for this session's run only. Never saved, logged or shared.")
        cap = st.number_input("Spend cap per run (USD)", min_value=0.01, max_value=20.0, value=1.0, step=0.1, format="%.2f",
                              help="The run stops before any call that could go over this.")
        with st.expander("Options"):
            num = st.slider("Max promos", 3, 30, int(base_cfg["promo"]["num_promos_max"]))
            fresh = st.checkbox("Fresh-viewer judge", value=True)
            do_base = st.checkbox("Score the last-30 s baseline", value=True)
        st.markdown('<p class="side-note"><b>Your key stays in this browser session.</b> It is passed straight to the provider and cleared after the run.</p>'
                    f'<p class="side-note">Kuku Cut · Built by {AUTHOR}</p>', unsafe_allow_html=True)
    return provider, name, api_key, cap, num, fresh, do_base


def render_footer():
    st.markdown(f'<div class="foot">Built by {AUTHOR} · Kuku Cut is an independent assignment project, not a Kuku product.</div>',
                unsafe_allow_html=True)


base_cfg = load_cfg()
provider, name, api_key, cap, num, fresh, do_base = render_sidebar(base_cfg)

with st.container(key="intro"):
    render_hero()
    render_status("idle", "Upload your own screenplays. No Kuku data is included.")
    render_status("idle", "Enter your API key in the left sidebar (click » at top left if hidden).")
    if st.button("Demo mode: see it on a made-up show", key="demo", type="tertiary"):
        st.session_state["view"] = {"out": DEMO, "demo": True}
    render_steps()

    with st.container(key="upload"):
        files = st.file_uploader("Screenplays: one file per episode, or a zip", accept_multiple_files=True, type=EXTS + ["zip"])

        model = model_name(provider, name)
        cfg = run_cfg(base_cfg, model, cap, num, fresh, do_base)
        ready = False
        if files:
            up = parsed_upload(files, cfg)
            if up["err"]:
                render_status("error", f"Could not read the upload: {esc(up['err'])}")
            elif not up["eps"]:
                render_status("error", "No episodes found. Each file needs timestamped lines.")
            else:
                for x in up["failed"]:
                    render_status("error", f"Skipped {esc(x.get('file'))}: {esc(x.get('error'))}")
                ready = True
                if model:
                    render_estimate(estimate(up["eps"], cfg, model), model, cap)

        missing = []
        if not files:
            missing.append("upload screenplays")
        if not name.strip():
            missing.append("set a model name")
        if not api_key:
            missing.append("enter an API key")
        if missing:
            render_status("idle", "Idle. To run on your own files: " + ", ".join(missing) + ".")
        elif ready:
            render_status("active", "Ready to run")
        go = st.button("Run", key="run", type="primary", disabled=bool(missing) or not ready, use_container_width=True)

        if go:
            lock = run_lock()
            if not lock.acquire(blocking=False):
                render_status("error", "Another run is in progress on this server. Try again in a few minutes, or use demo mode.")
                st.stop()
            up = st.session_state["upload"]
            out = os.path.join(up["work"], "out_" + time.strftime("%H%M%S"))
            if "cache" not in st.session_state:
                st.session_state["cache"] = tempfile.mkdtemp(prefix="promo_cache_")
            order = [s for s in pipeline.STEPS if s != "baseline" or do_base]
            scan = st.empty()
            scan.markdown('<div class="scan"><span></span></div>', unsafe_allow_html=True)
            status = st.status("Running…", expanded=True)
            bar = status.progress(0.0)
            note = status.empty()
            done = []

            def prog(step, i, n):
                if done:
                    note.write("✓ " + " · ".join(STEP_NAMES[s] for s in done))
                done.append(step)
                k = order.index(step)
                bar.progress(k / len(order), text=f"{STEP_NAMES[step]} ({k+1}/{len(order)}) · spent ${llm.state.get('run_spent', 0.0):.3f}")

            try:
                llm.setup(cfg, run_dir=out, cache_dir=st.session_state["cache"], api_key=api_key, use_env=False)
                pipeline.run(up["inp"], out, cfg, progress=prog)
                try:
                    check_rules.run(out, cfg, [])
                except Exception as ex:
                    render_status("error", f"Rule check could not run: {esc(str(ex)[:200])}")
                bar.progress(1.0, text=f"Done · spent ${llm.state.get('run_spent', 0.0):.3f}")
                status.update(label="Done", state="complete", expanded=False)
                scan.markdown(f'<div class="scan-done">{svg("check")}<span>Run complete</span></div>', unsafe_allow_html=True)
                st.session_state["view"] = {"out": out, "demo": False}
            except llm.BudgetError as ex:
                scan.empty()
                status.update(label="Stopped at the spend cap", state="error")
                render_status("error", f"Stopped: {esc(redact(str(ex), api_key))}")
            except SystemExit as ex:
                scan.empty()
                status.update(label="Stopped", state="error")
                render_status("error", esc(redact(str(ex), api_key)))
            except Exception as ex:
                scan.empty()
                status.update(label="Failed", state="error")
                msg = redact(f"{type(ex).__name__}: {ex}", api_key)
                if "auth" in msg.lower() or "401" in msg:
                    msg = "The provider rejected the API key or model name. " + msg[:300]
                render_status("error", esc(msg[:600]))
            finally:
                llm.forget_key()
                lock.release()

view = st.session_state.get("view")
if view and os.path.exists(os.path.join(view["out"], "09_final", "promos.json")):
    results(view["out"], view["demo"])

render_footer()
