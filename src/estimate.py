# rough cost estimate before a run: llm calls and tokens per step from the parsed screenplay size.
# coefficients are fitted on three measured runs (5 and 50 episodes, usage.jsonl), rounded up;
# completion tokens include reasoning tokens. it is an estimate: the per-run spend cap is the real limit.
import math
from src import llm

# per call: prompt tokens = a + b * screenplay chars of the episode (episode, shots); completion tokens flat
# billed cost ran about 1.4x tokens x listed price on openrouter (provider routing); applied to the estimate
BILLING_MARGIN = 1.4
EP = {"episode": (1730, 0.29, 2000, 0.5), "shots": (1260, 0.75, 6500, 0.0)}


def estimate(eps, cfg, model):
    n = len(eps)
    chars = []
    for e in eps:
        c = 0
        for ln in e["lines"]:
            c += len(ln["text"])
        chars.append(c)
    rows = []
    for step in ["episode", "shots"]:
        a, b, ca, cb = EP[step]
        pt = 0
        ct = 0
        for c in chars:
            pt += a + b * c
            ct += ca + cb * c
        rows.append({"step": step, "calls": n, "prompt_tokens": pt, "completion_tokens": ct})
    chunks = max(1, math.ceil(n / cfg["bible"].get("chunk_size", 10)))
    rows.append({"step": "bible", "calls": chunks, "prompt_tokens": 4000 * chunks, "completion_tokens": 12000 * chunks})
    if chunks > 1:
        rows.append({"step": "bible_merge", "calls": 1, "prompt_tokens": 10000, "completion_tokens": 33000})
    rows.append({"step": "scripts", "calls": 1, "prompt_tokens": 2000 + 480 * n, "completion_tokens": 30000 + 400 * n})
    ns = cfg["scripts"]["num_scripts"]
    rows.append({"step": "match", "calls": ns, "prompt_tokens": ns * (1500 + 40 * n), "completion_tokens": ns * 720})
    # judged items: assembled promos (about 0.7 per script) plus one baseline clip per episode, in batches
    items = round(0.7 * ns)
    if cfg.get("_baseline", True):
        items += n
    batches = math.ceil(items / cfg["judge"].get("batch_size", 5))
    rows.append({"step": "judge_full", "calls": batches, "prompt_tokens": 3500 * batches, "completion_tokens": 3000 * batches})
    if cfg["judge"].get("fresh", True):
        rows.append({"step": "judge_fresh", "calls": batches, "prompt_tokens": 2200 * batches, "completion_tokens": 2700 * batches})
    pi, po = llm.price(model, cfg)
    tot = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0}
    for r in rows:
        r["prompt_tokens"] = int(r["prompt_tokens"])
        r["completion_tokens"] = int(r["completion_tokens"])
        r["usd"] = (r["prompt_tokens"] / 1e6 * pi + r["completion_tokens"] / 1e6 * po) * BILLING_MARGIN
        for k in tot:
            tot[k] += r[k]
    return {"episodes": n, "model": model, "price_per_1m": [pi, po], "rows": rows, "total": tot}
