import os
import io
import copy
import json
import shutil
import zipfile
import tempfile
import yaml
import streamlit as st

from src import llm
from src.common import load, load_dir
from src.report import seg_lines, seg_str
import run as pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(ROOT, "tests", "fixtures", "sample_show")
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
           "openrouter": "OPENROUTER_API_KEY", "mistral": "MISTRAL_API_KEY", "together_ai": "TOGETHERAI_API_KEY"}

st.set_page_config(page_title="Promo Finder", layout="wide")


def secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def key_env(model):
    pre = model.split("/")[0]
    if pre in KEY_ENV:
        return KEY_ENV[pre]
    return "OPENAI_API_KEY"


def save_uploads(files, dest):
    for f in files:
        name = os.path.basename(f.name)
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                for m in z.infolist():
                    if m.is_dir():
                        continue
                    base = os.path.basename(m.filename)
                    if not base or base.startswith(".") or "__MACOSX" in m.filename:
                        continue
                    sub = os.path.join(dest, os.path.dirname(m.filename).replace("..", "_"))
                    os.makedirs(sub, exist_ok=True)
                    with open(os.path.join(sub, base), "wb") as out:
                        out.write(z.read(m))
        else:
            with open(os.path.join(dest, name), "wb") as out:
                out.write(f.getvalue())


def zip_dir(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(path):
            for n in names:
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, path))
    return buf.getvalue()


with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
    base_cfg = yaml.safe_load(f)

st.sidebar.header("Model")
model = st.sidebar.text_input("Model (litellm name)", value=secret("MODEL") or base_cfg["llm"]["provider_model"],
                              help="e.g. gemini/<model> or groq/<model>. Any litellm model string works.")
judge_model = st.sidebar.text_input("Judge model (optional)", value=secret("JUDGE_MODEL") or base_cfg["llm"]["judge_model"])
api_key = st.sidebar.text_input("API key", value="", type="password", help="Left empty, the key from Streamlit secrets is used.")
rpm = st.sidebar.number_input("Requests per minute", min_value=1, max_value=1000, value=int(base_cfg["llm"]["rpm_limit"]))
num = st.sidebar.slider("Number of promos", 3, 30, int(base_cfg["promo"]["num_promos_max"]))
fresh = st.sidebar.checkbox("Fresh-viewer judge", value=True)
do_base = st.sidebar.checkbox("Score the last-30s baseline", value=True)
demo = st.sidebar.checkbox("Demo: made-up sample show, offline mock answers", value=False)

st.title("Promo Finder")
st.caption("Upload timestamped screenplays (one file per episode, or a zip). The tool returns a ranked list of promo clips: segments with timestamps, the hook, the cliffhanger and why each works. An editor still approves and cuts.")

files = st.file_uploader("Screenplays (.txt .md .pdf .docx .json or .zip)", accept_multiple_files=True,
                         type=["txt", "md", "pdf", "docx", "json", "zip", "fountain", "srt"])

go = st.button("Run", type="primary")

if go:
    if not demo and not files:
        st.error("Upload screenplay files first, or tick the demo box.")
        st.stop()
    if not demo and not model:
        st.error("Set a model name in the sidebar.")
        st.stop()
    cfg = copy.deepcopy(base_cfg)
    # the sidebar model is used for every step
    cfg["llm"]["provider_model"] = model
    cfg["llm"]["step_models"] = {}
    cfg["llm"]["judge_model"] = judge_model
    cfg["llm"]["rpm_limit"] = rpm
    cfg["promo"]["num_promos_max"] = num
    if cfg["promo"]["num_promos_min"] > num:
        cfg["promo"]["num_promos_min"] = num
    cfg["judge"]["fresh"] = fresh
    cfg["_baseline"] = do_base
    cfg["_episodes"] = None
    if demo:
        cfg["llm"]["mock"] = True
    else:
        env = key_env(model)
        k = api_key or secret(env) or secret("API_KEY")
        if k:
            os.environ[env] = k
        if judge_model and key_env(judge_model) != env:
            k2 = secret(key_env(judge_model))
            if k2:
                os.environ[key_env(judge_model)] = k2
    work = tempfile.mkdtemp(prefix="promo_")
    inp = os.path.join(work, "input")
    out = os.path.join(work, "out")
    os.makedirs(inp)
    if demo:
        shutil.copytree(SAMPLE, inp, dirs_exist_ok=True)
    else:
        save_uploads(files, inp)
    llm.setup(cfg, run_dir=out)
    bar = st.progress(0.0, text="starting")

    def prog(name, i, n):
        bar.progress(i / n, text=f"running {name}")

    try:
        with st.spinner("Running the pipeline. With free-tier rate limits a 50-episode show takes 15 to 30 minutes."):
            pipeline.run(inp, out, cfg, progress=prog)
    except SystemExit as ex:
        st.error(str(ex))
        st.stop()
    except Exception as ex:
        st.exception(ex)
        st.stop()
    bar.progress(1.0, text="done")
    st.session_state["out"] = out

out = st.session_state.get("out")
if out and os.path.exists(os.path.join(out, "09_final", "promos.json")):
    finals = load(os.path.join(out, "09_final", "promos.json"))
    sel = load(os.path.join(out, "08_selected.json"))
    summ = load(os.path.join(out, "09_final", "run_summary.json"))
    eps_by = {}
    for e in load_dir(os.path.join(out, "01_parsed")):
        eps_by[e["episode"]] = e
    if sel["note"]:
        st.warning(sel["note"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Episodes", summ["run"].get("episodes", 0))
    c2.metric("Promos", len(finals))
    c3.metric("Script fill rate", summ["run"].get("fill_rate", 0))
    c4.metric("LLM calls", summ["usage"]["calls"])
    rows = []
    for f in finals:
        rows.append({"rank": f["rank"], "id": f["promo_id"], "template": f["template"], "secs": f["total_duration_sec"],
                     "total": f["scores"]["total"], "confidence": f["confidence"], "hook": f["hook"], "cliffhanger": f["cliffhanger"],
                     "segments": " | ".join(f"ep{s['episode']} {s['start']}-{s['end']}" for s in f["segments"])})
    st.subheader("Ranked promos")
    st.dataframe(rows, hide_index=True, use_container_width=True)
    for f, p in zip(finals, sel["selected"]):
        with st.expander(f"#{f['rank']} {f['promo_id']} · {f['template']} · {f['total_duration_sec']}s · {f['scores']['total']}"):
            st.markdown(f"**Hook:** {f['hook']}  \n**Cliffhanger:** {f['cliffhanger']}  \n**Why it works:** {f['why_it_works']}")
            if f["confusion"]:
                st.markdown(f"**Fresh viewer:** {f['confusion']}")
            sc = f["scores"]
            st.caption(f"hook {sc['hook']} · cliffhanger {sc['cliffhanger']} · clarity {sc['clarity']} · coherence {sc['coherence']} · duration {sc['duration']} · spoiler −{sc['spoiler_penalty']} · flags: {', '.join(f['flags']) or 'none'}")
            for sg in seg_lines(p, eps_by):
                st.markdown(f"**{sg['label']}** ({', '.join(sg['roles'])})")
                txt = []
                for ln in sg["lines"]:
                    txt.append(f"[{ln['ts']}] {ln['who']}: {ln['text']}")
                st.code("\n".join(txt), language=None)
    st.subheader("Best per template")
    temp = []
    for t, b in sel["best_per_template"].items():
        temp.append({"template": t, "promo": b.get("final_id", b["promo_id"]), "total": b["total"], "selected": b["selected"]})
    st.dataframe(temp, hide_index=True)
    if summ.get("baseline"):
        b = summ["baseline"]
        st.caption(f"Baseline (last 30 s of each episode), same judges: tool top-15 mean {b['mean_total_tool_top15']} vs baseline {b['mean_total_baseline_top15']}")
    d = os.path.join(out, "09_final")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("promos.json", open(os.path.join(d, "promos.json"), "rb").read(), "promos.json")
    c2.download_button("promos.csv", open(os.path.join(d, "promos.csv"), "rb").read(), "promos.csv")
    c3.download_button("report.html", open(os.path.join(d, "report.html"), "rb").read(), "report.html")
    c4.download_button("all raw outputs (zip)", zip_dir(out), "raw_outputs.zip")
