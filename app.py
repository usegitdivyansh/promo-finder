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

from src import llm, parse
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

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="centered")

st.markdown("""
<style>
.tl{margin:2px 0 8px}
.tl-row{display:flex;align-items:center;gap:8px;font-size:12px;margin:3px 0}
.tl-ep{width:44px;flex:none;opacity:.75}
.tl-bar{position:relative;flex:1;height:10px;border-radius:5px;background:rgba(128,128,128,.22)}
.tl-seg{position:absolute;top:0;height:10px;border-radius:5px;background:#e8590c;min-width:3px}
.tl-t{width:96px;flex:none;text-align:right;font-variant-numeric:tabular-nums;opacity:.75}
.ln{font-size:13px;line-height:1.45;margin:1px 0}
.ln .ts{opacity:.6;font-variant-numeric:tabular-nums;margin-right:6px}
.ln .who{font-weight:600}
.ln.key{background:rgba(232,89,12,.14);border-radius:4px;padding:0 4px}
</style>
""", unsafe_allow_html=True)


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


def badge(conf):
    colors = {"high": "green", "medium": "orange", "low": "red"}
    c = colors.get(str(conf).lower(), "gray")
    return f":{c}-badge[{str(conf).capitalize()} confidence]"


def timeline(p, eps_by):
    rows = []
    for s in p["segments"]:
        e = eps_by[s["episode"]]
        dur = e.get("duration_sec") or e["lines"][-1]["end"] or 1
        lines = e["lines"]
        a = lines[s["a"]]["start"]
        b = lines[s["b"]]["end"]
        left = max(0.0, min(100.0, a / dur * 100))
        width = max(0.8, min(100.0 - left, (b - a) / dur * 100))
        rows.append(f'<div class="tl-row"><span class="tl-ep">Ep {s["episode"]}</span>'
                    f'<span class="tl-bar"><span class="tl-seg" style="left:{left:.2f}%;width:{width:.2f}%"></span></span>'
                    f'<span class="tl-t">{fmt_cut(a)}–{fmt_cut(b)}</span></div>')
    return '<div class="tl">' + "".join(rows) + "</div>"


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


def card(f, p, eps_by):
    sc = f["scores"]
    with st.container(border=True):
        st.markdown(f"**#{f['rank']} · {f['template'].replace('_', ' ')}** · {round(f['total_duration_sec'], 1)} s · "
                    f"score **{sc['total']}** &nbsp; {badge(f['confidence'])}")
        st.markdown(f"**Hook:** {f['hook']}  \n**Cliffhanger:** {f['cliffhanger']}  \n**Why it works:** {f['why_it_works']}")
        st.markdown(timeline(p, eps_by), unsafe_allow_html=True)
        st.caption(f"hook {sc['hook']} · cliffhanger {sc['cliffhanger']} · clarity {sc['clarity']} · coherence {sc['coherence']} · "
                   f"duration {sc['duration']} · spoiler −{sc['spoiler_penalty']}")
        with st.expander("Screenplay lines"):
            if f.get("confusion"):
                st.caption(f"A fresh viewer might ask: {f['confusion']}")
            for i, s in enumerate(p["segments"]):
                lines = eps_by[s["episode"]]["lines"]
                st.markdown(f"**Cut {i+1}** · episode {s['episode']} · {fmt_cut(lines[s['a']]['start'])}–{fmt_cut(lines[s['b']]['end'])} · {', '.join(s['roles'])}")
                st.markdown(lines_html(s, eps_by), unsafe_allow_html=True)


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
    if demo:
        st.success("Demo: a made-up 5-episode show, with saved results from a real run. No key, no cost.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Episodes", summ["run"].get("episodes", 0))
    c2.metric("Promos", len(finals))
    if demo:
        c3.metric("Cost of this view", "$0")
    else:
        c3.metric("Cost", f"${cost:.3f}")
    if sel["note"]:
        st.warning(sel["note"])
    b = summ.get("baseline")
    if b:
        st.caption(f"Same judges on the last 30 s of each episode (baseline): top-15 mean {b['mean_total_baseline_top15']} vs tool {b['mean_total_tool_top15']}.")
    st.subheader("Ranked promos")
    for f in finals:
        card(f, by_id[f["internal_id"]], eps_by)
    st.subheader("Downloads")
    d = os.path.join(out, "09_final")
    c1, c2 = st.columns(2)
    c1.download_button("promos.json", open(os.path.join(d, "promos.json"), "rb").read(), "promos.json", use_container_width=True)
    c2.download_button("promos.csv", open(os.path.join(d, "promos.csv"), "rb").read(), "promos.csv", use_container_width=True)
    c1.download_button("report.html", open(os.path.join(d, "report.html"), "rb").read(), "report.html", use_container_width=True)
    zk = "zip_" + out
    if zk not in st.session_state:
        st.session_state[zk] = zip_dir(out)
    c2.download_button("raw outputs (zip)", st.session_state[zk], "raw_outputs.zip", use_container_width=True)
    if not demo:
        st.caption(f"{calls} LLM calls ({cached} from this session's cache).")


base_cfg = load_cfg()

with st.sidebar:
    st.header("Model")
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
    st.caption("Your key stays in this browser session. It is passed straight to the provider and cleared after the run.")

st.title("Promo Finder")
st.write("Finds ranked promo clips in timestamped episode screenplays: each with cut times, the hook, the cliffhanger and why it works. An editor still approves and cuts.")
st.info("Upload your own screenplays. No Kuku data is included.")

if st.button("Demo mode: see it on a made-up show", use_container_width=True):
    st.session_state["view"] = {"out": DEMO, "demo": True}

files = st.file_uploader("Screenplays: one file per episode, or a zip", accept_multiple_files=True, type=EXTS + ["zip"])

model = model_name(provider, name)
cfg = run_cfg(base_cfg, model, cap, num, fresh, do_base)
ready = False
if files:
    up = parsed_upload(files, cfg)
    if up["err"]:
        st.error(f"Could not read the upload: {up['err']}")
    elif not up["eps"]:
        st.error("No episodes found. Each file needs timestamped lines.")
    else:
        for x in up["failed"]:
            st.warning(f"Skipped {x.get('file')}: {x.get('error')}")
        ready = True
        if model:
            est = estimate(up["eps"], cfg, model)
            t = est["total"]
            pi, po = est["price_per_1m"]
            st.markdown(f"**Estimate for {est['episodes']} episodes:** about {t['calls']} LLM calls, "
                        f"{(t['prompt_tokens'] + t['completion_tokens']) / 1000:,.0f}k tokens, **~${t['usd']:.2f}** "
                        f"at ${pi:g} / ${po:g} per 1M tokens for `{model}`.")
            if t["usd"] > cap:
                st.warning(f"The estimate is above your ${cap:.2f} cap, so the run will probably stop early. Raise the cap or upload fewer episodes.")
            st.caption("A rough estimate from token use on earlier runs; reasoning models can use more. The cap is the hard limit.")

missing = []
if not files:
    missing.append("upload screenplays")
if not name.strip():
    missing.append("set a model name")
if not api_key:
    missing.append("enter an API key")
if missing:
    st.caption("To run on your own files: " + ", ".join(missing) + ".")
go = st.button("Run", type="primary", disabled=bool(missing) or not ready, use_container_width=True)

if go:
    lock = run_lock()
    if not lock.acquire(blocking=False):
        st.error("Another run is in progress on this server. Try again in a few minutes, or use demo mode.")
        st.stop()
    up = st.session_state["upload"]
    out = os.path.join(up["work"], "out_" + time.strftime("%H%M%S"))
    if "cache" not in st.session_state:
        st.session_state["cache"] = tempfile.mkdtemp(prefix="promo_cache_")
    order = [s for s in pipeline.STEPS if s != "baseline" or do_base]
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
        bar.progress(1.0, text=f"Done · spent ${llm.state.get('run_spent', 0.0):.3f}")
        status.update(label="Done", state="complete", expanded=False)
        st.session_state["view"] = {"out": out, "demo": False}
    except llm.BudgetError as ex:
        status.update(label="Stopped at the spend cap", state="error")
        st.error(f"Stopped: {redact(str(ex), api_key)}")
    except SystemExit as ex:
        status.update(label="Stopped", state="error")
        st.error(redact(str(ex), api_key))
    except Exception as ex:
        status.update(label="Failed", state="error")
        msg = redact(f"{type(ex).__name__}: {ex}", api_key)
        if "auth" in msg.lower() or "401" in msg:
            msg = "The provider rejected the API key or model name. " + msg[:300]
        st.error(msg[:600])
    finally:
        llm.forget_key()
        lock.release()

view = st.session_state.get("view")
if view and os.path.exists(os.path.join(view["out"], "09_final", "promos.json")):
    results(view["out"], view["demo"])
