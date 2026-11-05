# Promo Finder

Reads timestamped microdrama screenplays and outputs a **ranked list of promo clips** for Instagram, TikTok and YouTube Shorts. Each promo is one or more segments (episode + start/end time), with the hook, the cliffhanger and one line on why it works. It does not edit video. An editor approves the picks and cuts them.

Built for Kuku's AI Generalist assignment (Problem 1: Find the Promos).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # put one API key in .env
```

Models are named once in `config.yaml` → `llm.models`, as [LiteLLM](https://docs.litellm.ai/docs/providers) model strings (`gemini/...`, `openrouter/...`, `openai/...`, `anthropic/...`): `fast` (free tier, Step 2 summaries on non-confidential input) and `strong` (paid: every other step, the fallback, and every step of a confidential run). Every other setting refers to these slot names. **Swapping a model is that one line**, e.g. `strong: "openai/gpt-4.1-mini"` (or `--model <name>` on the command line for every step). A model with no entry in `llm.prices` is priced from LiteLLM's table, or budgeted at `llm.unknown_price` if unknown, so a swap can never skip the `budget_usd` cap.

## Run

```bash
# full show: one folder of episode files (txt, md, pdf, docx, json)
python run.py --input data/show1 --out raw_outputs/show1

# try 3 episodes first
python run.py --input data/show1 --out raw_outputs/show1_try --episodes 1-3

# resume from a step using saved outputs (step1..step9, baseline)
python run.py --input data/show1 --out raw_outputs/show1 --from step5

# swap the model without editing config
python run.py --input data/show1 --model groq/<model>

# offline, no API key: made-up 3-episode show with mock LLM answers
python run.py --input tests/fixtures/sample_show --mock
```

Every LLM call is cached in `cache/` (key = model + filled prompt + schema), so a rerun on the same show is fast and free, and only changed steps call the model again.

Outputs land in `raw_outputs/<run>/`:

| Path | What |
|---|---|
| `09_final/promos.json`, `promos.csv` | ranked promos (CSV has an empty `editor_decision` column) |
| `09_final/report.html`, `report.md` | every promo with its scores and the **actual screenplay lines** of each segment, plus best-per-template, run summary, drops and usage |
| `01_parsed` … `08_selected.json`, `10_baseline*.json` | every intermediate step, unedited |
| `logs/drops_*.json`, `logs/failed_*.json` | every dropped item with a reason, and every failure |
| `usage.jsonl` | tokens, latency, model and step for every call |

## Streamlit app

```bash
streamlit run app.py
```

Upload the episode files (or one zip), set the model and key in the sidebar, then press Run. The "Demo" checkbox runs the made-up sample show offline. Results show a ranked table, an expander per promo with its lines and scores, the best per template, and downloads (promos.json, promos.csv, report.html, a zip of all raw outputs).

**Deploy on Streamlit Community Cloud:** point it at this repo and `app.py`, then add secrets such as:

```toml
MODEL = "gemini/<model>"
GEMINI_API_KEY = "..."
```

No screenplays are bundled. The cache is per session on Cloud (the disk is ephemeral), so a rerun after a restart calls the model again.

## How it works

| Step | Who | Output |
|---|---|---|
| 1 Parse | code | lines with ids, start/end seconds, type, speaker, recap flag |
| 2 Episode labels | LLM ×1 per episode | summary, key events (by line id), 10 fixed labels, peak, cliffhanger |
| 3 Series bible | LLM ×1 (or chunks + merge) | characters + aliases, threads, twists (major/minor), setup→payoff links |
| 4 Shot bank | LLM ×1 per episode | long / medium / small clip-able moments with role, hook type, intensity |
| 5 Promo generation | LLM scripts + code filter + LLM match, then a code safety net | candidate promos from 6 templates |
| 6 Assembly | code | hard rules: line-boundary cuts, hook in 3 s, trim, extend, spoiler guard |
| 7 Scoring | 2 LLM judges + code | full judge (with bible), fresh-viewer judge (no bible), code metrics |
| 8 Selection | code | category coverage, ≤2 per thread, no reused hook |
| 9 Report | code | JSON, CSV, HTML, Markdown |

The LLM only ever refers to **line ids and event ids**. Code computes every timestamp, so a timestamp can't be hallucinated. Every quoted shot is fuzzy-matched against the screenplay and dropped if it doesn't match.

Score: `total = 10 × (0.28 hook + 0.28 cliffhanger + 0.22 clarity + 0.17 coherence + 0.05 duration) − spoiler_penalty`, where hook = 0.7 judge + 0.3 shot intensity, and coherence = 0.5 judge + 0.5 code (shared thread/character between adjacent segments). Clarity comes from the fresh-viewer judge (or from the full judge when `judge.fresh: false`).

## Evaluation

```bash
python -m src.evaluate --run raw_outputs/show1 --make-sheets   # blind hand-check + ranking sheets in <run>/eval/
python -m src.evaluate --run raw_outputs/show1                 # writes eval/results.md
```

- Gold set: put hand-picked promos in `eval/gold.json` (format: `eval/gold.example.json`). A hit = an output promo with time IoU ≥ 0.5.
- Hand check: mark `hook_pass / cliffhanger_pass / standalone_pass` for the top 10 tool promos and the top 10 baseline promos. They are mixed and blinded, and `hand_check.html` shows the lines.
- Judge vs human: rank the tool's top 10 in `human_rank.csv`. The result is a Spearman correlation.
- Also reported: fill rate, drops by reason, the baseline comparison (last 30 s of each episode, same judges), and a failure gallery.

## Tests

```bash
python -m pytest -q
```

These cover timestamp parsing, end times, recap detection, line-id validation, the quote check, the hook-in-3-s rule, trimming, extension, the spoiler guard, the duration score, and a full offline mock run of the sample show.

## Confidentiality

`data/`, `cache/`, `raw_outputs/`, `.env` and eval files derived from real episodes are git-ignored. This repo holds only code, prompts and a made-up 3-episode fixture.

## Known limits

- Text only: visual-only moments (a look, an expression) are invisible to the tool.
- LLM judges can overrate loud scenes. The fresh-viewer judge and the hand check are there to catch this.
- An error in an episode summary spreads to the bible, shots and scripts. The code checks catch invalid ids and quotes, but not a wrong interpretation.
