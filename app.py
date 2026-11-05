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

AUTHOR = "Divyansh Gupta"

st.set_page_config(page_title="Promo Finder", page_icon="✂️", layout="centered",
                   menu_items={"Get help": None, "Report a bug": None, "About": f"Promo Finder · Built by {AUTHOR}"})

# palette from kukufm.com (sources in .streamlit/config.toml); the slate and phone are dark in both themes,
# everything else inherits the theme's text colour
st.markdown("""
<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"], .stDeployButton, .viewerBadge_container__1QSob {display:none !important}
header[data-testid="stHeader"] {background:transparent}
:root{--k-brand:#E25506;--k-red:#EC1C24;--k-gold:#FFBB10;--k-green:#01860A;--k-ink:#0A0A14}
.hero{border-radius:14px;padding:18px 18px 14px;margin:0 0 6px;color:#fff;
  background:linear-gradient(135deg,#0A0A14 0%,#1A1420 55%,#3A1A0E 100%)}
.hero .ttl{font-size:28px;font-weight:700;line-height:1.15;margin:0 0 6px;color:#fff}
.hero .ttl .acc{color:var(--k-brand)}
.hero p{margin:0;opacity:.85;font-size:15px}
.hero .by{margin-top:10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;opacity:.7}
.cr{display:grid;grid-template-columns:minmax(0,1fr) 188px;gap:14px;align-items:start}
@media (max-width:640px){.cr{grid-template-columns:minmax(0,1fr)}.phone{margin:4px auto 0}}
.slate{border-radius:10px;overflow:hidden;background:var(--k-ink);color:#fff;border:1px solid rgba(128,128,128,.35)}
.clap{height:12px;background:repeating-linear-gradient(-45deg,#fff 0 12px,var(--k-ink) 12px 24px)}
.slate-row{display:flex;flex-wrap:wrap;gap:0;border-top:1px solid rgba(255,255,255,.14)}
.slate-row div{flex:1 1 auto;padding:6px 10px;border-right:1px solid rgba(255,255,255,.14);min-width:72px}
.slate-row div:last-child{border-right:none}
.slate-row small{display:block;font-size:10px;letter-spacing:.08em;text-transform:uppercase;opacity:.6}
.slate-row b{font-size:15px;font-variant-numeric:tabular-nums}
.slate-row .cut b{color:var(--k-brand);font-size:18px}
.conf{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;border-radius:999px;padding:2px 9px}
.conf.high{background:var(--k-green);color:#fff}.conf.medium{background:var(--k-gold);color:var(--k-ink)}.conf.low{background:var(--k-red);color:#fff}
.beats{margin:10px 2px 6px;font-size:15px;line-height:1.5}
.beats b{color:var(--k-brand)}
.strip{border-radius:8px;padding:7px 12px;margin:6px 0;background:#15151f;color:#e9e9ee;position:relative;outline:1px solid rgba(128,128,128,.35);
  border-top:6px dotted rgba(255,255,255,.35);border-bottom:6px dotted rgba(255,255,255,.35)}
.strip-top{display:flex;justify-content:space-between;gap:8px;font-size:12px;flex-wrap:wrap}
.strip-top .tc{font-family:ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.role{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;border-radius:4px;padding:1px 6px;background:rgba(255,255,255,.12)}
.role.hook,.role.scene{background:var(--k-brand);color:#fff}.role.cliffhanger{background:var(--k-red);color:#fff}
.bar{position:relative;height:6px;border-radius:3px;background:rgba(255,255,255,.16);margin-top:6px}
.bar span{position:absolute;top:0;height:6px;border-radius:3px;background:var(--k-brand);min-width:3px}
.scores{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
.scores span{font-size:12px;border:1px solid rgba(128,128,128,.35);border-radius:999px;padding:1px 9px;font-variant-numeric:tabular-nums}
.phone{width:188px;border-radius:26px;padding:9px;background:#1b1b22;box-shadow:0 6px 18px rgba(0,0,0,.25)}
.screen{position:relative;aspect-ratio:9/16;border-radius:18px;overflow:hidden;background:linear-gradient(180deg,#191925,#0A0A14)}
.notch{position:absolute;top:6px;left:50%;transform:translateX(-50%);width:54px;height:6px;border-radius:3px;background:#000;z-index:2}
.pv-top{position:absolute;top:18px;left:10px;right:10px;display:flex;justify-content:space-between;font-size:10px;color:#fff;opacity:.8;z-index:2}
.pv-caps{position:absolute;top:36px;bottom:104px;-webkit-mask-image:linear-gradient(#000 82%,transparent);mask-image:linear-gradient(#000 82%,transparent);left:8px;right:8px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;scrollbar-width:none}
.cap{font-size:11px;line-height:1.3;color:#fff;background:rgba(0,0,0,.45);border-radius:6px;padding:4px 6px}
.cap i{opacity:.75}
.cap.hk{background:var(--k-brand)}.cap.cf{background:var(--k-red)}
.cap .t{opacity:.7;font-size:9px;margin-right:4px;font-variant-numeric:tabular-nums}
.pv-cf{position:absolute;bottom:42px;left:8px;right:8px}
.pv-cf .cap{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.pv-end{position:absolute;bottom:10px;left:10px;right:10px;text-align:center;font-size:11px;font-weight:700;color:var(--k-ink);
  background:var(--k-gold);border-radius:999px;padding:5px}
.ln{font-size:13px;line-height:1.45;margin:1px 0}
.ln .ts{opacity:.6;font-variant-numeric:tabular-nums;margin-right:6px}
.ln .who{font-weight:600}
.ln.key{background:rgba(226,85,6,.16);border-radius:4px;padding:0 4px}
.foot{margin:28px 0 8px;padding-top:12px;border-top:1px solid rgba(128,128,128,.3);font-size:13px;opacity:.75;text-align:center}
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


def esc(x):
    return html.escape(str(x))


def strip_html(s, eps_by):
    # one film-strip row per cut: episode, timecodes in/out, role, where the cut sits in the episode
    e = eps_by[s["episode"]]
    lines = e["lines"]
    dur = e.get("duration_sec") or lines[-1]["end"] or 1
    a = lines[s["a"]]["start"]
    b = lines[s["b"]]["end"]
    left = max(0.0, min(100.0, a / dur * 100))
    width = max(0.8, min(100.0 - left, (b - a) / dur * 100))
    roles = ""
    for r in s["roles"]:
        roles += f'<span class="role {esc(r)}">{esc(r)}</span> '
    return (f'<div class="strip"><div class="strip-top"><span>EP {s["episode"]} &nbsp;{roles}</span>'
            f'<span class="tc">TC IN {fmt_cut(a)} → OUT {fmt_cut(b)} · {b - a:.1f} s</span></div>'
            f'<div class="bar" title="position in the episode"><span style="left:{left:.2f}%;width:{width:.2f}%"></span></div></div>')


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


def card_html(f, p, eps_by):
    sc = f["scores"]
    conf = str(f["confidence"]).lower()
    slate = (f'<div class="slate"><div class="clap"></div><div class="slate-row">'
             f'<div class="cut"><small>Cut</small><b>{f["rank"]:02d}</b></div>'
             f'<div><small>Template</small><b>{esc(f["template"].replace("_", " "))}</b></div>'
             f'<div><small>Runtime</small><b>{f["total_duration_sec"]:.1f} s</b></div>'
             f'<div><small>Score</small><b>{sc["total"]}</b></div>'
             f'<div><small>Confidence</small><span class="conf {esc(conf)}">{esc(conf)}</span></div></div></div>')
    beats = (f'<div class="beats"><b>Hook</b> {esc(f["hook"])}<br><b>Cliffhanger</b> {esc(f["cliffhanger"])}<br>'
             f'<b>Why it works</b> {esc(f["why_it_works"])}</div>')
    strips = ""
    for s in p["segments"]:
        strips += strip_html(s, eps_by)
    scores = (f'<div class="scores"><span>hook {sc["hook"]}</span><span>cliffhanger {sc["cliffhanger"]}</span>'
              f'<span>clarity {sc["clarity"]}</span><span>coherence {sc["coherence"]}</span>'
              f'<span>duration {sc["duration"]}</span><span>spoiler −{sc["spoiler_penalty"]}</span></div>')
    return f'<div class="cr"><div>{slate}{beats}{strips}{scores}</div>{phone_html(p, eps_by)}</div>'


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
    with st.container(border=True):
        st.markdown(card_html(f, p, eps_by), unsafe_allow_html=True)
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
    st.caption(f"Promo Finder · Built by {AUTHOR}")

st.markdown(f'''<div class="hero"><div class="ttl" role="heading" aria-level="1">Promo <span class="acc">Finder</span></div>
<p>Ranked promo cuts from timestamped episode screenplays: exact cut times, the hook, the cliffhanger and why it works. An editor still approves and cuts.</p>
<div class="by">Built by {AUTHOR}</div></div>''', unsafe_allow_html=True)
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

st.markdown(f'<div class="foot">Promo Finder · Built by {AUTHOR}</div>', unsafe_allow_html=True)
