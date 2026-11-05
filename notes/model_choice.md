# Model choice and data handling

## Decision (Rishabh, 2026-11-05)

- The assignment brief allows AI tools. It asks only for free tiers, and for any paid API to be described in the doc.
- **Kuku's real input pack runs on a paid tier only**: DeepSeek V4.1 Flash via OpenRouter, for every step, with a hard spend cap of $2 across all runs.
- Reason: on Google's pricing page, the Gemini API free tier says "Used to improve our products: Yes", so free-tier prompts may be used by Google. Kuku's screenplays are confidential and must not be sent anywhere their content may be reused for training. The paid tier was chosen so that no free-tier data use applies to the confidential scripts.
- Check before the real run: confirm in the OpenRouter account's privacy settings that prompt logging / training is turned off, and that the routed DeepSeek provider does not train on inputs. This was not verified from code.

## How the code enforces it

- `config.yaml` → `llm.confidential_model` names the paid model.
- `run.py` turns on **confidential mode** automatically for any `--input` under `data/` (or with `--confidential`). In that mode every step, the judges included, uses `confidential_model`. The free-tier fallback is disabled, so if the paid provider fails the run stops instead of switching to a free model. Tests: `tests/test_llm.py::test_confidential_never_falls_back_to_free`.
- Paid spend is logged per call in `usage.jsonl` and in a cross-run ledger at `cache/_spend.jsonl`. A call that could take total spend past `llm.budget_usd` ($2) stops the run before it is sent.

## Synthetic show (made up, not confidential)

- Step 2 (episode summaries): Gemini 3.5 Flash-Lite, free tier. Its summaries and labels were checked and judged good enough.
- Bible, shots, scripts, matching and both judges: DeepSeek V4.1 Flash via OpenRouter, because Flash-Lite's quality on these steps was weak (see `notes/ai_mistakes.md` and the run comparison).
- Gemini 3.6 Flash was tried first. Its free tier allows only 20 requests per day per model, too few for one show (about 140 calls for 50 episodes).

## Prices used (checked 2026-11-05)

| Model | Input $/1M | Output $/1M | Source |
|---|---|---|---|
| openrouter/deepseek/deepseek-v4.1-flash | 0.045 | 0.50 | openrouter.ai/api/v1/models |
| gemini/gemini-3.5-flash-lite (free tier) | 0 | 0 | aistudio.google.com/docs/pricing |
| gemini/gemini-3.5-flash-lite (paid, for reference) | 0.30 | 2.50 | same |
