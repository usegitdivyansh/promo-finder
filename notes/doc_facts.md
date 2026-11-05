# Doc facts (raw material for the 2-page doc)

By Divyansh Gupta.

Facts only, for writing the doc in your own words. No show names, plot or script text. "Kuku pack" = the 50-episode input pack; "synthetic show" = the made-up 5-episode test show in `tests/fixtures/synthetic_show`. Numbers are from the run folders, `usage.jsonl` files and `cache/_spend.jsonl` as of 2026-11-05.

## 1. Problem framing and why Problem 1

- Kuku makes AI-generated vertical microdramas (1 to 2 minute episodes, new shows every week). Today an editor watches every episode and hand-picks promo clips for Instagram, TikTok and YouTube Shorts. That does not scale with weekly releases.
- Problem 1 ("Find the Promos"): read timestamped screenplays, output a ranked list of promos. Each promo = one or more segments in story order (episode + start/end time), a hook (stops the scroll in the first 3 s), a cliffhanger (unresolved ending), and one line on why it works.
- The tool replaces the editor's selection work, not the editing: it outputs cut lists, and the editor approves and cuts. It does not touch video.
- Why Problem 1: it is a selection bottleneck on a recurring weekly task, the input (timestamped screenplays) is already structured, and the output is easy to check (timestamps, durations, rules) and easy to compare against a simple baseline.
- Brief constraints that shaped the design: free tiers (a paid API may be described), the model must be easy to swap, it must rerun on a new show with one command, raw outputs must be kept unedited, the input pack is confidential.

## 2. Assumptions

1. Output 12 to 18 promos per show (configurable). The tool does not pad: promos scoring under 60/100 are dropped, so a show can return fewer (the Kuku pack returned 16).
2. Promo length: hard limits 20 to 60 s; 30 to 45 s is the target.
3. The hook must start within the first 3 s of the promo.
4. Line timing: the Kuku pack gives explicit start and end times per block (camera, action, sound, dialogue blocks overlap in time), so a line's end is the running maximum of block ends. For screenplays with start times only, a line ends where the next starts; the last line gets 2.5 words/s, minimum 2 s.
5. Recaps at the start of an episode are not new moments and are never used (the Kuku pack had none).
6. A promo may tease a major twist but must not show its payoff. A twist paid off in episode 1 is the premise, not a spoiler, because a promo sends viewers to episode 1.
7. At most 2 promos per story thread, no two promos open on the same hook shot, and no two promos use the same footage (same episode, overlapping time range).
8. Text only: visual-only moments (a look, an expression) are invisible to the tool.
9. An editor reviews every promo before posting (human in the loop; the CSV has an `editor_decision` column).

## 3. Pipeline (one line per step) and who does what

| Step | What | LLM | Code | Editor |
|---|---|---|---|---|
| 1 Parse | screenplay files → lines with ids, start/end seconds, type, speaker, recap flag | – | all | – |
| 2 Episodes | per episode: summary, key events by line id, labels, peak, cliffhanger | 1 call per episode | validates line ids | – |
| 3 Bible | characters and aliases, story threads, twists (major/minor) with setup → payoff | chunks of 10 episodes + 1 merge | premise rule (episode-1 payoff is not a spoiler), alias overrides | optional aliases file |
| 4 Shot bank | clip-able moments per episode: role, hook type, intensity, quote | 1 call per episode | quote check against the script (drops fabricated quotes); adds a closing-scene shot per episode that ends on its cliffhanger | – |
| 5 Candidates | promo scripts from templates, beats matched to shots | 1 scripts call + 1 match call per script | story-order check, safety-net candidates, closing-scene candidates | – |
| 6 Assembly | cut list per promo | – | all: cuts on line boundaries, hook within 3 s, trim/extend to 20–60 s, recap and spoiler guard | – |
| 7 Scoring | hook, cliffhanger, clarity, coherence | 2 judges in batches of 5: full (knows the story) and fresh-viewer (doesn't) | code coherence (shared thread/character), duration score, weighted total | – |
| 8 Selection | final ranked list | – | all: score ≥ 60, ≤ 2 per thread, unique hook, no shared footage, template coverage | – |
| 9 Report | promos.json, promos.csv, report.html/.md | – | all | approves picks, cuts video |
| Baseline | last 30 s of every episode, same judges | judges | builds clips, spoiler flag | – |

- Rule of thumb: the LLM judges story, emotion and hooks, and only ever names line ids and event ids. Code computes every timestamp and enforces every hard rule, so a timestamp cannot be made up. The editor approves.
- Score: `total = 10 × (0.28 hook + 0.28 cliffhanger + 0.22 clarity + 0.17 coherence + 0.05 duration) − spoiler_penalty`; hook = 0.7 judge + 0.3 shot intensity; coherence = 0.5 judge + 0.5 code; clarity from the fresh-viewer judge.
- Templates: single_scene, reveal_tease, mini_story, underdog_revenge, romance_tension, montage (LLM-written), ending_scene (code-built). An eighth, hook_ending (code-built), was tried in v4 and is off.

## 4. Models and why

- Kuku pack: **DeepSeek V4.1 Flash via OpenRouter, paid, for every step** (confidential mode, automatic for any input under `data/`; no free-tier fallback).
- Why paid for the Kuku pack: the Gemini API free tier states its prompts may be used to improve Google's products. The scripts are confidential, so no free tier was used on them. OpenRouter's account privacy setting was set to training endpoints off (set by the user, not verifiable from code).
- Synthetic show: Gemini 3.5 Flash-Lite (free tier) for Step 2 summaries; DeepSeek for every other step, because Flash-Lite was weak on the bible, shots, scripts and judging (lower fill, a twist misread, out-of-order scripts). Gemini 3.6 Flash was tried first; its free tier (20 requests/day) is too small for one show.
- Listed price: DeepSeek V4.1 Flash $0.045 in / $0.50 out per 1M tokens (openrouter.ai, 2026-11-05). Billed cost ran 1.57× the listed price (provider routing); the ledger records the billed amount.
- Swapping the model is one line: `config.yaml` line 6 (`strong: ...`). Every step, the fallback and confidential mode follow that slot. A model with no known price is budgeted at a cautious default ($5/$15 per 1M), never as free.
- The app uses one model for all steps with the viewer's own key, typed in the sidebar and used for that run only.

## 5. Final results (Kuku pack, `raw_outputs/show1/09_final/`)

- Episodes: 50 (3,827 blocks, episodes 70 to 201 s, no recaps, 0 parse failures).
- Pipeline counts: 232 shots kept; 24 scripts written, 24 filled (fill rate 100%); 52 candidates; 39 assembled; 34 eligible after rules; **16 selected**.
- Drops: spoiler 5, too short 8, below score 60: 5, over template cap 4, shot size cap 8, shot too short 1.
- Score range of the 16: **60.05 to 87.19**. Durations 20 to 59 s (mean 34.5 s). 7 single-cut, 9 multi-cut (3 of them cross-episode). Confidence: 4 high, 11 medium, 1 low. Footage from 21 of 50 episodes.
- Templates selected: ending_scene 7, montage 3, reveal_tease 2, mini_story 2, romance_tension 1, underdog_revenge 1.
- Means (selected, sorted by total): **top 5 79.08, top 10 75.42, top 15 72.97**, all 16: 72.16.
- Baseline (last 30 s of each episode, same judges): top 5 83.6, top 10 81.52, **top 15 79.45**. No-spoiler baseline top 15: 79.39.
- **Gap, top 15: tool −6.48 vs baseline** (−6.42 vs the no-spoiler baseline). The baseline wins on this show.
- Per-metric, top 15 (tool vs baseline, gap): hook 7.27 vs 7.40 (−0.13), cliffhanger 7.13 vs 7.93 (−0.80), clarity 6.40 vs 7.27 (−0.87), coherence 8.77 vs 9.17 (−0.40).
- Rule check (`check_rules`): all 6 groups pass (recaps, aliases with 8 extras excluded, quotes, per-promo rules, major twist payoffs, diversity incl. shared footage).
- Independent verifier (recomputed from the parsed screenplay, not the pipeline's own code): 16 of 16 pass. Line ids exist, shown cut times match the screenplay to 0.01 s, 20–60 s, hook 0.0 to 3.0 s in, in order with no overlap, no recap lines, quotes match, ≤ 2 per thread, none of the 5 major twist payoffs shown.
- Synthetic show (current code, demo results): 4 promos selected of 15 eligible (86.1, 77.19, 69.4, 62.01); tool 73.67 vs baseline 70.19 (tool +3.48). Gold set: 0 of 3 hand-picked promos hit at IoU ≥ 0.5 (an earlier DeepSeek run hit 1 of 3; one gold promo ends on a major twist payoff the tool is not allowed to show).
- App test run on the synthetic show (DeepSeek, $0.10 cap, fresh cache): 41 calls, $0.085, 2 promos, all rules pass.
- **Blind human check (1 rater, 20 items: top 10 tool + top 10 baseline, shuffled, unlabelled; judge pass = score ≥ 7):**

  | Criterion | Tool pass | Baseline pass | Agreement with judge | Kappa |
  |---|---|---|---|---|
  | Hook | 10/10 | 9/10 | 70% | −0.09 |
  | Cliffhanger | 9/10 | 10/10 | 90% | −0.05 |
  | Standalone | 9/10 | 9/10 | 70% | −0.15 |

  Pass all three: tool 8/10, baseline 8/10. Spearman (human passes vs judge total): −0.28 overall (n = 20), tool −0.44, baseline −0.09.
  The 4 fails: X03 tool rank 1 (ending_scene, judge 87.19), cliffhanger; X06 tool rank 5 (montage, 74.86), standalone; X09 baseline (78.55), standalone; X19 baseline (86.8), hook.
  Reading: the human found tool and baseline about equal (8/10 each pass all three), unlike the judges, which put the baseline 6.5 points ahead. Kappa and Spearman are not reliable here: the rater failed only 4 of 60 checks (1–2 per criterion), so kappa rests on one or two items (moving the single hook fail onto an item the judge also failed would change hook kappa from −0.09 to +0.27), and human scores per item take only two values (3 passes for 16 items, 2 for 4), so Spearman is mostly ties. They show no measurable agreement, not disagreement. One rater, no second rater.

## 6. v1 → v5 (Kuku pack, all runs kept in the raw outputs zip)

| Version | What changed and why | Selected / eligible | Tool top 15 vs baseline 79.45 | New paid cost |
|---|---|---|---|---|
| try (3 eps) | test of Steps 1–4 on the new block format; parser adapted (overlapping blocks, delivery notes, camera/sound lines); bible split 3 episodes into 8 threads | – | – | $0.039 |
| v1 | first full run. To fix the fragmented threads the prompt asked for 3–8 threads; the bible made 5 broad ones incl. a catch-all (116 events), 9 of 15 eligible promos shared it, so the 2-per-thread limit capped output at 7. Bible merge and scripts were cut off at 16k tokens first (per-prompt max_tokens added) | 7 / 15 | no baseline | $0.522 |
| v2 | thread = one specific story line, 2–5 per 10 episodes → 10 threads; episode-1 premise no longer a spoiler (code rule); 20 scripts. 9 of 16 assembled promos still hooked from one thread | 9 / 16 | 70.27 (−9.18) | $0.391 |
| v3 | scripts prompt states the per-thread limit and spreads hooks (≤ 3 scripts per hook thread); 28 scripts requested, 24 written, 24 filled | 12 / 18 (one at 46.87, before the ≥ 60 rule) | 72.38 (−7.07) | $0.081 |
| v4a | each episode's ending added as a cliffhanger shot + code-built "hook + episode ending" template; promos under 60 dropped. Cliffhanger up, clarity down (cross-episode cut confuses a new viewer); 16 of 30 pairs too short | 13 / 25 | 71.35 (−8.10) | $0.100 |
| v4 | prefer hook/ending pairs that reach 20 s: one more promo, clarity fell further (cliffhanger 6.42 → 7.57, clarity 6.83 → 4.64 vs v3) | 14 / 26 | 69.15 (−10.30) | $0.029 |
| v5 | single-scene promos ending on each episode's cliffhanger (20–45 s, hook within 3 s, same scene, no cross-episode hook); 23 of 50 episodes had one; judged together with v3's pool. Top 5 82.16 vs v3 79.21 | 16 / 37 | 74.41 (−5.04) | $0.029 |
| final | independent review fixes (a single-scene promo's hook was 20.6 s in; cut times were rounded; outage fallback; one-line model swap) + no-shared-footage rule (rerun from Step 8, no new calls) | 16 / 34 | 72.97 (−6.48) | $0.029 |

- Judges score batches relatively, so totals compare pools, not single promos across runs (v3's promos re-judged inside the v5 pool averaged 67.5 vs 73.8 for the new ending-scene promos).
- The lesson from v4 → v5: context, not the cliffhanger line alone, is what the baseline's endings have. Single-scene endings kept clarity (6.3) and coherence (9.1) that cross-episode pairs lost (4.4–5.6 clarity).

## 7. Cost

- **Total paid spend, whole project: $1.6158** of a $2.00 cap. 586 paid calls, 1.93M prompt + 1.88M completion tokens (3.81M), all DeepSeek V4.1 Flash. Blended billed rate $0.42 per 1M tokens.
- Of that, Kuku pack (all 8 runs, each call counted once): **$1.22**. The rest: synthetic show runs, tests, the no-cache review run ($0.092) and the app test run ($0.085).
- One full 50-episode run of the final pipeline (footprint rebuilt from the logs): **169 calls, 0.70M prompt + 0.63M completion = 1.33M tokens, about $0.54 billed**.
  - Per episode: about **$0.011** and 3.4 calls.
  - Per show (50 episodes): about **$0.54**; the app's pre-run estimate for the same show says $0.61 (it errs high by design).
  - Per 100 shows: about **$54**.
  - Biggest steps by tokens: shots (0.51M), episode summaries (0.31M), judges (0.23M), match (0.11M).
- Reruns: every call is cached (model + prompt + schema), so a rerun of an unchanged show costs $0, and a change from Step 5 on costs only the later steps (v5 from Step 4 with cached shots: $0.029, about 6 minutes).
- Wall time: calls run one at a time under a per-model throttle (8 requests/minute). Summed LLM latency for Steps 2–4 on 50 episodes was about 49 minutes (episode summaries 13 min, bible 9 min, shots 27 min); Steps 5–9 took 7 to 11 minutes.

## 8. How it scales

- **Parallel shows:** each show is an independent folder run with its own outputs; shows can run side by side (the app allows one real run per server process today because the LLM wrapper keeps module-level state; the CLI has no such limit beyond provider rate limits).
- **Within a show:** Steps 2 and 4 are one call per episode and independent, so they can run in parallel up to the provider's rate limit; today they run sequentially (the main source of wall time).
- **Caching:** content-addressed cache per call; reruns and parameter changes after Step 4 reuse the expensive per-episode steps.
- **Cheap vs strong model:** the per-episode bulk (Steps 2 and 4, about 60% of tokens) can go to a cheap model and scripts/judging to a stronger one: per-step model slots in `config.yaml` (`step_models`). Confidential inputs force the paid model for every step.
- **Editor feedback loop:** `promos.csv` has an `editor_decision` column; approved/rejected picks become labels to tune prompts, score weights and the min score, and to grow the gold set.
- **Main upgrade:** a vision model on the actual video for visual-only moments.

## 9. Top 5 AI mistakes (from `notes/ai_mistakes.md`, 35 entries) and the failure gallery

1. **Premise marked as a major twist (pipeline LLM, DeepSeek, Kuku pack).** The bible marked the show's episode-1 inciting event as a major twist paid off in episode 1; the spoiler guard would have dropped every promo built on the show's strongest hook. Caught by reading the twists before Step 5. Fix: code rule, an episode-1 payoff is the premise.
2. **Over-correction of the thread rule (Claude Code).** Fixing 8 fragmented threads with "3 to 8 threads" produced 5 broad threads incl. a catch-all; the 2-per-thread limit then capped output at 7 promos. Fix: a thread is one specific story line, 2–5 per 10 episodes. Run kept as v1.
3. **Hook 20.6 s into a promo, missed by the rule checker (Claude Code).** Single-scene promos with no hook shot inside kept the scene start; `check_rules` passed it because it read the hook position assembly had recorded. Caught by an independent verifier recomputing from the screenplay. Fix + regression test.
4. **A spoiler heuristic that would have shown the biggest spoiler (Claude Code over-correcting the LLM).** After the LLM marked an end-of-episode reveal as a major payoff, a code rule nulled any payoff within 2 lines of an episode ending; it also nulled a genuine major reveal in the sample show. Caught by the end-to-end test. Fix: heuristic removed.
5. **Token cut-offs that looked like bad JSON (config gap, Claude Code).** On 50 episodes the bible merge and the scripts call were cut off at 16k tokens and failed as "no JSON"; about $0.03 spent on futile retries. Fix: per-prompt max_tokens and an explicit "output cut off" warning.

Other notable ones: an unknown model was treated as free so the budget check never fired (fixed with cautious default pricing); cut times shown rounded to whole seconds would clip a line (fixed, exact MM:SS.ss); a free-model 503 dropped a whole episode (fixed, per-call paid fallback).

**Failure gallery** (`notes/failure_gallery.md`):
1. A cliffhanger summary sold a "lethal" stake that only a camera direction mentions; on screen there is a shocked discovery, no danger. Rules passed; a reviewer would catch it. Possible fix: summarise from dialogue/action/sound only.
2. Two selected promos cut the same 21 s of one episode; the old diversity rules (thread, hook shot) let it through. Fixed with the no-shared-footage rule.
3. A single-scene promo's hook landed 20.6 s in (see mistake 3). Fixed.

## 10. Known limits

- **Text only:** visual-only beats (a look, an expression, a reveal shown not said) are invisible.
- **Judge bias:** LLM judges score batches relatively (totals shift with the pool) and can favour loud scenes; the fresh-viewer judge and the blind human check are the counterweights. The same judges score tool and baseline.
- **Small gold set:** 3 hand-picked promos on the synthetic show only; none for the Kuku pack.
- **The baseline wins on this show:** the last 30 s of each episode scores 79.45 vs the tool's 72.97 (top 15). The episode endings are written as cliffhangers with their own setup, and the judges reward exactly that. The tool's best promos (top 5: 79.08) approach it.
- **Aliases:** 8 unnamed extras excluded from the alias check; one named character was added by hand (git-ignored aliases file).
- **Error propagation:** a wrong episode summary spreads to the bible, shots and scripts; code catches invalid ids and quotes but not a wrong interpretation.
- **Cost estimate:** fitted on 3 runs of one model; reasoning models may use more tokens. The per-run cap is the hard limit.
- **Hosting:** on Streamlit Community Cloud the app runs one real run at a time and the disk is temporary (cache lost on restart); the "Hosted with Streamlit" badge outside the app frame cannot be hidden by the app.
