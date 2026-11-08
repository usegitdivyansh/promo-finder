# Kuku Cut

Reads timestamped microdrama screenplays and returns a **ranked list of promo clips** for Instagram, TikTok and YouTube Shorts. Each promo is one or more cuts (episode + start/end time, to the hundredth of a second), with the hook, the cliffhanger and why it works. It does not edit video: an editor approves the picks and cuts them.

Built by **Divyansh Gupta** for Kuku's AI Generalist assignment (Problem 1: Find the Promos).

**[User manual (PDF)](docs/user_manual.pdf)**: setup, where each API key goes, the web app, the command line, costs and troubleshooting.

## Quick start

```bash
pip install -r requirements.txt          # python 3.11, versions pinned
streamlit run app.py                     # press "Demo mode": saved results, no key, no cost
python run.py --input tests/fixtures/sample_show --mock   # offline end-to-end run, no key
python -m pytest -q                      # 48 tests
```

## Run on a show

```bash
cp .env.example .env                     # one API key per provider you use
python run.py --input data/show1 --out raw_outputs/show1
```

`--input` is a folder with one file per episode (`.txt .md .pdf .docx .json .fountain .srt`). Episode numbers come from an `EPISODE N` header or the file name. Two timestamp styles are read: `(MM:SS)` / `[MM:SS]` per line, and Kuku-style blocks (`**TAG:**`, then `Start: (MM:SS.ss) | End: (MM:SS.ss)`, then the text).

**A new show, step by step:**

1. Put the episodes in `data/<show>/`. Anything under `data/` runs in confidential mode automatically: the paid `strong` model for every step, no free tier, no fallback. `data/` is git-ignored.
2. Try three episodes and read the parse report: `python run.py --input data/<show> --out raw_outputs/<show>_try --episodes 1-3`, then open `raw_outputs/<show>_try/01_parsed/_report.json`.
3. Run the whole show. Every call is cached in `cache/` (key = model + filled prompt + schema), so a rerun is free and `--from step5` (or any step) redoes only what changed.
4. Check the rules: `python -m src.check_rules --run raw_outputs/<show>`.
5. Optional: if the rule check lists speaker tags that are real characters or unnamed extras, put them in a git-ignored aliases file (`--aliases data/<show>_aliases.json`).

Other flags: `--model <litellm name>` (one model for every step), `--no-baseline`, `--num-promos N`, `--to stepN`, `--only stepN`.

## Swap the model: one line

Models are named once in `config.yaml` under `llm.models`, as [LiteLLM](https://docs.litellm.ai/docs/providers) strings:

```yaml
# config.yaml
    fast: "gemini/gemini-3.5-flash-lite"               # line 5: step 2 summaries on non-confidential input
    strong: "openrouter/deepseek/deepseek-v4.1-flash"  # line 6: every other step, the fallback, confidential runs
```

Every other setting refers to `fast` or `strong`, so **changing line 6 swaps the model for the whole pipeline**, e.g. `strong: "openai/gpt-4.1-mini"`. A model missing from `llm.prices` is priced from LiteLLM's table, or budgeted at `llm.unknown_price` if unknown, so a swap can never skip the spend cap (`llm.budget_usd`, ledger in `cache/_spend.jsonl`).

## Streamlit app

```bash
streamlit run app.py
```

- **Demo mode** loads saved results from a real run on the made-up 5-episode show in `demo/synthetic_show/`. It works with no key and costs nothing.
- **Your own run:** pick a provider and model and paste your API key in the sidebar. The key is sent straight to the provider for this run's calls only, is never written to disk, config or logs, and is cleared when the run ends. No key is bundled and none is read from the server's environment.
- After an upload (files or one zip) the app parses it locally and shows an **estimate** (LLM calls, tokens, dollars). The **spend cap** (default $1) stops the run before any call that could go over it.
- Results: ranked promo cards (template, duration, hook, cliffhanger, why it works, score breakdown, confidence badge), a timeline of where each cut sits in its episode, the screenplay lines of every cut, and downloads (`promos.json`, `promos.csv`, `report.html`, all raw outputs as a zip).
- One real run at a time per server; a second user is asked to wait. Each session has its own cache in a temp folder.

**Deploy (Streamlit Community Cloud):** point it at this repo, branch `main`, file `app.py`, Python 3.11. No secrets are needed: viewers bring their own key, or use demo mode.

## Outputs

`raw_outputs/<run>/`:

| Path | What |
|---|---|
| `09_final/promos.json`, `promos.csv` | ranked promos with exact cut times (`MM:SS.ss`); the CSV has an empty `editor_decision` column |
| `09_final/report.html`, `report.md` | every promo with its scores and the screenplay lines of each cut, plus the run summary, drops and usage |
| `01_parsed` … `08_selected.json`, `10_baseline*.json` | every intermediate step, unedited |
| `logs/drops_*.json`, `logs/failed_*.json` | every dropped item with its reason, and every failure |
| `usage.jsonl` | model, step, tokens, latency and cost of every call |
| `rule_check.json` | written by `src.check_rules` |

## How it works

| Step | Who | Output |
|---|---|---|
| 1 Parse | code | lines with ids, start/end seconds, type, speaker, recap flag |
| 2 Episode labels | LLM ×1 per episode | summary, key events (by line id), labels, peak, cliffhanger |
| 3 Series bible | LLM (chunks of 10 episodes + merge) | characters + aliases, story threads, twists (major/minor) with setup→payoff |
| 4 Shot bank | LLM ×1 per episode, plus code | clip-able moments with role, hook type and intensity; code adds a single-scene shot ending on each episode's cliffhanger |
| 5 Promo candidates | LLM scripts + LLM match + code safety net | candidates from 7 templates |
| 6 Assembly | code | cuts on line boundaries, hook within 3 s, trim/extend to 20–60 s, recap and spoiler guard |
| 7 Scoring | 2 LLM judges + code | full judge (knows the story), fresh-viewer judge (doesn't), code coherence and duration |
| 8 Selection | code | drop scores under 60, ≤2 per story thread, no reused hook, no shared footage, template coverage |
| 9 Report | code | JSON, CSV, HTML, Markdown |
| Baseline | code + same judges | the last 30 s of every episode, for comparison |

The LLM only ever names **line ids and event ids**. Code computes every timestamp, so a timestamp can't be made up, and every quoted line is fuzzy-matched against the screenplay and dropped if it doesn't match.

Score: `total = 10 × (0.28 hook + 0.28 cliffhanger + 0.22 clarity + 0.17 coherence + 0.05 duration) − spoiler_penalty`. Clarity comes from the fresh-viewer judge.

## Evaluation

- **Rule check:** `python -m src.check_rules --run raw_outputs/<show>` (cut times, 20–60 s, hook in 3 s, no recap, quotes, ≤2 per thread, no reused hook, no shared footage, no major twist payoff).
- **Blind human check:** `python src/blind_sheet.py --run raw_outputs/<show>` writes `eval/blind_sheet.html` (top 10 tool promos + top 10 baseline clips, shuffled and unlabelled; pass/fail for hook, cliffhanger and standalone; exports JSON) and `eval/blind_key.json`. Then `python src/score_blind.py --results <export>.json` reports pass rates (tool vs baseline) and agreement with the LLM judge (agreement, kappa, Spearman). Both files are git-ignored because they hold screenplay text.
- **Gold set:** hand-picked promos in `eval/gold.json` (format: `eval/gold.example.json`); `python -m src.evaluate --run raw_outputs/<show>` counts hits at time IoU ≥ 0.5.
- **Run comparison:** `python -m src.compare_runs --runs <run> <run> --out compare.md`.

Notes from building and running it are in `notes/`: model choice, every AI mistake caught, a failure gallery, and the synthetic-show comparison.

## Confidentiality

`data/`, `cache/`, `raw_outputs/`, `.env`, the blind eval files and the raw outputs zip are git-ignored. The repo holds code, prompts, tests, two made-up fixture shows, the demo results for the made-up show, and notes without show names or script text.

## Known limits

- Text only: a look or an expression that isn't written down is invisible to the tool.
- LLM judges score batches relatively and can overrate loud scenes; the fresh-viewer judge and the blind human check are there to catch this.
- The gold set is small (the made-up show only).
- On the real show tested, the last 30 s of each episode still scored higher with the same judges than the tool's promos.
