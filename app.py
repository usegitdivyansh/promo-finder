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
TITLE = f"Promo Finder: Kuku AI Generalist Assignment, by {AUTHOR}"

st.set_page_config(page_title=TITLE, page_icon="✂️", layout="centered",
                   menu_items={"Get help": None, "Report a bug": None, "About": f"Promo Finder · Built by {AUTHOR}"})

# palette from kukufm.com (sources in .streamlit/config.toml). colours only: no kuku logo, wordmark or icons.
# one accent (#E25506) for the rank badge, the timeline segments and the hook caption; everything else is neutral
st.markdown("""
<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"], .stDeployButton, .viewerBadge_container__1QSob {display:none !important}
header[data-testid="stHeader"] {background:transparent}
:root{--acc:#E25506;--red:#EC1C24;--gold:#FFBB10;--green:#3FCB5A;--ink:#0A0A14;--surf:#14141F;--surf2:#1D1D2A;
  --line:rgba(255,255,255,.10);--mute:rgba(236,235,230,.62);--txt:#ECEBE6}
.block-container{padding-top:2.2rem;padding-bottom:3rem}
.hero{border-radius:16px;padding:24px 22px 20px;margin:0 0 14px;background:var(--surf);border:1px solid var(--line)}
.hero .ttl{font-size:30px;font-weight:700;line-height:1.1;margin:0;color:var(--txt);letter-spacing:-.01em}
.hero .ttl::after{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--acc);margin-left:6px;vertical-align:baseline}
.hero .sub{margin:6px 0 12px;font-size:14px;color:var(--mute)}
.hero p{margin:0;font-size:15px;line-height:1.55;color:var(--txt);opacity:.9}
.hero .by{margin-top:14px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.sum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0 18px}
.sum div{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.sum b{display:block;font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--txt)}
.sum small{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin-top:2px}
.sum .ok b{color:var(--green)}.sum .bad b{color:var(--red)}
@media (max-width:640px){.sum{grid-template-columns:repeat(2,minmax(0,1fr))}}
.cr{display:grid;grid-template-columns:minmax(0,1fr) 176px;gap:20px;align-items:start;padding:4px 2px}
@media (max-width:640px){.cr{grid-template-columns:minmax(0,1fr)}.phone{margin:6px auto 0}}
.head{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.rank{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:34px;padding:0 8px;border-radius:10px;
  background:var(--acc);color:#fff;font-weight:700;font-size:15px;font-variant-numeric:tabular-nums}
.tag{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;border:1px solid var(--line);background:var(--surf2);
  color:var(--txt);border-radius:999px;padding:3px 10px}
.conf{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;border-radius:999px;padding:3px 9px;border:1px solid currentColor}
.conf.high{color:var(--green)}.conf.medium{color:var(--gold)}.conf.low{color:var(--red)}
.tot{margin-left:auto;font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.tot small{font-size:11px;font-weight:500;color:var(--mute);margin-left:3px}
.dur{margin:14px 0 4px}
.dbar{position:relative;height:6px;border-radius:3px;background:var(--surf2);overflow:hidden}
.dbar .win{position:absolute;top:0;bottom:0;left:33.33%;right:0;background:rgba(255,255,255,.07)}
.dbar .fill{position:absolute;top:0;bottom:0;left:0;border-radius:3px;background:var(--txt);opacity:.85}
.dur small{display:flex;justify-content:space-between;font-size:12px;color:var(--mute);margin-top:4px;font-variant-numeric:tabular-nums}
.beat{margin:14px 0 0}
.beat small{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-bottom:2px}
.beat.hook div{font-weight:700;font-size:16px;line-height:1.45}
.beat.cliff div{font-style:italic;font-size:15px;line-height:1.5}
.beat.why div{font-size:14px;line-height:1.55;opacity:.88}
.tl{margin:16px 0 0;border-top:1px solid var(--line);padding-top:12px}
.tl-row{display:grid;grid-template-columns:44px minmax(0,1fr) 42px;gap:8px;align-items:center;margin:4px 0}
.tl-row .ep{font-size:12px;font-weight:600;color:var(--mute)}
.tl-row .len{font-size:11px;color:var(--mute);text-align:right;font-variant-numeric:tabular-nums}
.track{position:relative;height:6px;border-radius:3px;background:var(--surf2)}
.track span{position:absolute;top:-1px;height:8px;border-radius:3px;background:var(--acc);min-width:4px}
.tcs{margin:6px 0 0;font-size:12px;color:var(--mute);font-family:ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;line-height:1.6;overflow-wrap:anywhere}
.scores{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 2px}
.chip{font-size:12px;border-radius:999px;padding:2px 10px;font-variant-numeric:tabular-nums;border:1px solid currentColor}
.chip.hi{color:var(--green)}.chip.mid{color:var(--gold)}.chip.lo{color:var(--red)}.chip.na{color:var(--mute)}
.phone{width:176px;border-radius:26px;padding:8px;background:#000;border:1px solid var(--line)}
.screen{position:relative;aspect-ratio:9/16;border-radius:19px;overflow:hidden;background:var(--surf)}
.notch{position:absolute;top:6px;left:50%;transform:translateX(-50%);width:50px;height:6px;border-radius:3px;background:#000;z-index:2}
.pv-top{position:absolute;top:18px;left:10px;right:10px;display:flex;justify-content:space-between;font-size:10px;color:var(--mute);z-index:2}
.pv-caps{position:absolute;top:36px;bottom:100px;left:8px;right:8px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;scrollbar-width:none;
  -webkit-mask-image:linear-gradient(#000 82%,transparent);mask-image:linear-gradient(#000 82%,transparent)}
.cap{font-size:11px;line-height:1.3;color:var(--txt);background:var(--surf2);border-radius:7px;padding:4px 7px}
.cap i{opacity:.75}
.cap.hk{background:var(--acc);color:#fff}
.cap.cf{background:var(--txt);color:var(--ink);font-style:italic}
.cap .t{opacity:.6;font-size:9px;margin-right:4px;font-variant-numeric:tabular-nums;font-style:normal}
.pv-cf{position:absolute;bottom:40px;left:8px;right:8px}
.pv-cf .cap{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.pv-end{position:absolute;bottom:10px;left:10px;right:10px;text-align:center;font-size:11px;font-weight:600;color:var(--txt);
  border:1px solid var(--line);border-radius:999px;padding:4px}
.ln{font-size:13px;line-height:1.45;margin:1px 0}
.ln .ts{opacity:.6;font-variant-numeric:tabular-nums;margin-right:6px}
.ln .who{font-weight:600}
.ln.key{background:rgba(226,85,6,.18);border-radius:4px;padding:0 4px}
.foot{margin:32px 0 8px;padding-top:14px;border-top:1px solid var(--line);font-size:13px;color:var(--mute);text-align:center;line-height:1.6}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px}
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


def timeline_html(p, eps_by):
    # one slim bar per episode, every cut from that episode highlighted on it, timecodes listed below
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
    tcs = ""
    for i, s in enumerate(p["segments"]):
        lines = eps_by[s["episode"]]["lines"]
        a = lines[s["a"]]["start"]
        b = lines[s["b"]]["end"]
        tcs += f'Cut {i+1} · EP {s["episode"]} · {fmt_cut(a)} → {fmt_cut(b)} · {b - a:.1f} s · {esc(", ".join(s["roles"]))}<br>'
    return f'<div class="tl">{rows}<div class="tcs">{tcs}</div></div>'


def chip(label, v, hi, mid):
    cls = "lo"
    if v >= hi:
        cls = "hi"
    elif v >= mid:
        cls = "mid"
    return f'<span class="chip {cls}">{label} {v}</span>'


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
    d = f["total_duration_sec"]
    head = (f'<div class="head"><span class="rank">#{f["rank"]}</span>'
            f'<span class="tag">{esc(f["template"].replace("_", " "))}</span>'
            f'<span class="conf {esc(conf)}">{esc(conf)} confidence</span>'
            f'<span class="tot">{sc["total"]}<small>/ 100</small></span></div>')
    # bar runs 0-60 s; the shaded part is the 20-60 s target window
    fill = max(2.0, min(100.0, d / 60 * 100))
    dur = (f'<div class="dur"><div class="dbar"><div class="win"></div><div class="fill" style="width:{fill:.1f}%"></div></div>'
           f'<small><span>{d:.1f} s</span><span>target 20–60 s</span></small></div>')
    beats = (f'<div class="beat hook"><small>Hook</small><div>{esc(f["hook"])}</div></div>'
             f'<div class="beat cliff"><small>Cliffhanger</small><div>{esc(f["cliffhanger"])}</div></div>'
             f'<div class="beat why"><small>Why it works</small><div>{esc(f["why_it_works"])}</div></div>')
    pen = "na"
    if sc["spoiler_penalty"]:
        pen = "lo"
    scores = ('<div class="scores">' + chip("hook", sc["hook"], 8, 6) + chip("cliffhanger", sc["cliffhanger"], 8, 6)
              + chip("clarity", sc["clarity"], 8, 6) + chip("coherence", sc["coherence"], 8, 6)
              + chip("duration", sc["duration"], 8, 6) + f'<span class="chip {pen}">spoiler −{sc["spoiler_penalty"]}</span></div>')
    return f'<div class="cr"><div>{head}{dur}{beats}{timeline_html(p, eps_by)}{scores}</div>{phone_html(p, eps_by)}</div>'


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
    return (f'<div class="sum"><div><b>{eps}</b><small>Episodes processed</small></div>'
            f'<div><b>{promos}</b><small>Promos found</small></div>'
            f'<div class="{rc}"><b>{rv}</b><small>Rules passed</small></div>'
            f'<div><b>{cost}</b><small>Cost of this run</small></div></div>')


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
    cost_txt = f"${cost:.3f}"
    if demo:
        cost_txt = "$0"
    st.markdown(summary_html(summ["run"].get("episodes", 0), len(finals), rules_passed(out), cost_txt), unsafe_allow_html=True)
    if demo:
        st.caption("Demo results were rebuilt from saved model responses, so this view made no LLM calls and cost nothing.")
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

st.markdown(f'''<div class="hero"><div class="ttl" role="heading" aria-level="1">Promo Finder</div>
<div class="sub">Kuku AI Generalist Assignment, by {AUTHOR}</div>
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
        try:
            check_rules.run(out, cfg, [])
        except Exception as ex:
            st.warning(f"Rule check could not run: {str(ex)[:200]}")
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

st.markdown(f'<div class="foot">Promo Finder · Built by {AUTHOR}<br>An independent assignment project, not a Kuku product. Colours only, no Kuku logos.</div>',
            unsafe_allow_html=True)
