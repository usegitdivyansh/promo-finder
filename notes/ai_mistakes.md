# AI mistakes log

Every mistake noticed while building or running, from Claude Code (writing this repo) or from the pipeline LLM.

- step: assemble (build time, Claude Code)
  what: for single_scene promos the hook rule picked a hook shot inside the long scene, but the promo kept the long shot as its hook_shot_id. Two promos opening on the exact same line (sample show "Who let the maid in?") passed the "no reused hook shot" rule in selection.
  caught by: reading the mock run output (X002 and X005 both open at ep3 00:08)
  fix: hook_shot_id now records the shot whose key line actually opens the promo

- step: select (build time, Claude Code, following the plan literally)
  what: the category-coverage pass walked templates in a fixed order (single_scene first). On the sample show the single_scene promo (78.8) took the wine-pour hook shot first, so the best promo overall (underdog_revenge, 89.2) was blocked by the "no reused hook shot" rule and dropped from the output.
  caught by: the end-to-end mock test asserting the underdog promo ranks first
  fix: coverage pass now visits templates in order of their best score

- step: tests (build time, Claude Code)
  what: wrote a "too long is dropped" test with two 44 s segments, forgetting that the rules allow trimming the hook segment's end and the cliffhanger segment's start; the code correctly trimmed it to 60 s and the test failed
  caught by: pytest
  fix: test now uses one merged 68 s segment, which really can't be trimmed

- step: parse (build time, Claude Code)
  what: the parser turned a title line above "EPISODE N" into a fake extra episode, numbered from the filename. On the synthetic show that clashed with the real episode number, and 3 of 5 episodes failed as "duplicate episode number".
  caught by: the parse report on the synthetic show (7 episodes found, 3 failed)
  fix: when a file has episode headers, untimed text before the first header is skipped; regression test added

- step: parse (build time, Claude Code)
  what: any "Word:" at the start of a line was taken as a speaker, so the action line "Inside: a legal document and an old photograph..." became dialogue spoken by "Inside"
  caught by: listing all parsed speaker names on the synthetic show
  fix: if most speaker names in a file are upper case, title-case "Name:" prefixes are treated as action; regression test added

- step: llm wrapper cost tracking (build time, Claude Code)
  what: the wrapper charged each call at max(config price, litellm's reported cost). litellm prices Gemini at paid rates even on the free tier, so free calls would have eaten into the $2 OpenRouter budget and could have stopped the run early
  caught by: the provider smoke test printed a nonzero response_cost for free-tier gemini-3.6-flash
  fix: litellm's cost is used only for models with a nonzero configured price

- step: episodes (pipeline LLM, gemini-3.6-flash)
  what: synthetic ep 3 is a quiet setup episode, but it was labelled primary "betrayal" with secondary reveal and power_shift; a phone call accepting an inheritance was called a power_shift
  caught by: reading Step 2 output on 3 episodes
  fix: prompt now says the primary label is what dominates screen time and that quiet decisions or discoveries are not power_shift or betrayal. Partly fixed: after the change ep 3 is "reveal" + "betrayal" (power_shift gone), not "setup"

- step: shots (pipeline LLM, gemini-3.6-flash)
  what: ep 2 got only 2 shots and none covered its own ending ("This can't be true"), and quiet ep 3's phone call got intensity 9 as an "underdog_flip"
  caught by: reading Step 4 output on 3 episodes
  fix: prompt gives an intensity scale and asks for a cliffhanger shot when the ending is strong; on rerun ep 2 got a cliffhanger shot and ep 3 dropped to intensity 6

- step: shots (pipeline LLM + code)
  what: the LLM labelled three ep 1 shots "small" although their line ranges were 15 to 19 s, and proposed a "long" shot spanning 65 s. After relabelling by duration, the 2-medium cap cut the episode's only cliffhanger shot ("It's about your father"), which tied on intensity with another shot
  caught by: comparing the raw LLM shots in the cache with the kept shots
  fix: per-size cap still keeps the highest intensity but breaks ties in favour of cliffhanger, then hook roles

- step: llm wrapper / run control (build time, Claude Code)
  what: when Gemini's free daily quota ran out (20 requests/day for gemini-3.6-flash) and the OpenRouter fallback returned 401, every step logged a failure and carried on, so the "full run" finished in 56 s with 0 scored promos and an empty report
  caught by: the first real full run on the synthetic show
  fix: if the primary is out of quota and the fallback is out of quota or rejects its key, the wrapper raises ProvidersDown and the run stops with the reason; test added

- step: model choice (build time, Claude Code)
  what: picked gemini-3.6-flash as the free-tier main model without knowing its daily cap; it turned out to be 20 requests per day per model, far below the ~140 calls a 50-episode show needs
  caught by: the 429 body on the full synthetic run (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue 20)
  fix: pending the user's choice of model or provider

- step: shots quote check (build time, Claude Code)
  what: the hallucination guard used one plain similarity ratio, so real quotes that left out a parenthetical or a leading clause failed: "This can't be true." vs "(whispering) This can't be true." scored 0.77, and "I'm carrying your child, Arjun." vs "Means nothing? I'm carrying your child, Arjun." scored 0.81. Two of the show's strongest lines were dropped as "bad_quote"
  caught by: reading drops_step4.json after the first full synthetic run
  fix: parentheticals are stripped before matching, and an exact substring covering at least half the line counts as a match; fabricated quotes still fail (the "Emma slaps Lily" test still drops)

- step: bible (pipeline LLM, gemini-3.5-flash-lite)
  what: marked the ep 5 cliffhanger "He was murdered" as the PAYOFF of a major twist, but it is a new open mystery. The spoiler guard then dropped the montage that ended on the show's best cliffhanger. The real minor twist (the affair, lipstick in ep 1 to earring in ep 3) was not listed, and "father didn't walk out" was over-rated as major
  caught by: assemble drop "spoiler W3 payoff E5_L26", checked against the show's story notes
  fix: bible prompt defines payoff as where the truth is confirmed, says an end-of-episode mystery is a setup with null payoff, and asks for minor twists too

- step: scripts + match (pipeline LLM + missing code check)
  what: scripts listed the cliffhanger event before the escalation event (e.g. C1: escalation ep5 00:33, cliffhanger ep5 00:17), and the matcher picked cliffhanger shots from the previous episode, so 3 filled scripts were dropped as not chronological at assembly
  caught by: assemble drops "cliffhanger is not last in story order"
  fix: code check makes scripts list events in story order (retry if not), and the beat filter only offers shots between the neighbouring beats' events

- step: all JSON steps (pipeline LLM, gemini-3.5-flash-lite)
  what: sometimes returned a bare JSON array instead of the requested object, and the judge wrote ids as "PROMO B01", which cost a retry each time
  caught by: retry messages in the run log
  fix: the wrapper unwraps one-element arrays or wraps a bare list into the schema's list field; the judge strips the "PROMO" prefix

- step: bible (Claude Code, over-correcting the LLM)
  what: after Flash-Lite again marked the ep 5 murder call as a major twist payoff, Claude added a code rule nulling any payoff within 2 lines of an episode's ending cliffhanger. That rule also nulled the genuine major reveal in the sample show ("Her real name is Emma Sterling", one line before "Then who is the woman I married?"), which would have let a promo show the show's biggest spoiler. On a second look, the LLM's reading was defensible: the ep 5 letter ("it was not an accident") sets it up and the call confirms the murder
  caught by: the end-to-end mock test (no spoiler drop any more)
  fix: heuristic removed; only "payoff equals setup" is nulled in code. The ep 5 murder call stays a major payoff, so promos may not show it; gold promo G3, which ends on that call, now breaks the tool's spoiler rule, which is a limit of the gold set, not a tool bug

- step: scripts (design gap, Claude Code)
  what: the script writer saw only event descriptions, not which events had clip-able shots, so it built beats on events like "Desai approaches Mira" with no hook shot; on the rerun 12 of 19 scripts failed to fill
  caught by: fill rate 7/19 and match answers of "none" for shot-less events
  fix: each event in the scripts prompt is tagged with the nearby shots' role, size and intensity, or "[no shot]"

- step: baseline comparison (design gap, Claude Code)
  what: the last-30-s baseline was scored without the spoiler rule the tool must follow. On the synthetic show the top baseline clip (B05, 89.2) is the ep 5 ending, which shows a major twist payoff, so "baseline beats tool" (74.8 vs 71.1) partly compared a rule-breaking clip with rule-following ones
  caught by: reading the baseline ranking after the run
  fix: baseline clips that show a major payoff are flagged and a no-spoiler baseline mean is reported next to the raw one (71.3 vs tool 71.1, a tie). Both numbers are reported; neither is hidden

- step: scripts (pipeline LLM, gemini-3.5-flash-lite)
  what: kept listing script beats out of story order even after the error was fed back (4 retries on the final run, and 1 script still dropped), and labelled a "You're fired" to "nobody learns my name" promo as romance_tension, which has no romance
  caught by: retry log and reading the final promos
  fix: none yet; the order check drops the bad scripts, and the template label is cosmetic. A stronger model for the scripts step is the likely fix

- step: bible (pipeline LLM, deepseek-v4.1-flash, synthetic_deepseek run)
  what: invented a major twist W5, "a legal document about Mira's mother hides a shocking family secret", which the script never says. It also split the Arjun/Tanya betrayal into two minor twists, one being "Tanya is pregnant", with the same setup event
  caught by: comparing 03_bible.json across the two runs
  fix: none needed for the promos; W5 has no payoff, so the spoiler guard ignores it. The earlier Flash-Lite mistake (the ep 5 murder call marked as a major payoff) did not happen: DeepSeek left that payoff empty, as the prompt asks

- step: match (pipeline LLM, deepseek-v4.1-flash)
  what: picked an event (E5_S4) that was not among the beat's candidates on the first attempt, and left 6 beats empty (5 cliffhangers, 1 hook), so the fill rate is 14/20, below Flash-Lite's 16/19
  caught by: retry log and drops_step5.json
  fix: the candidate check rejected the bad pick and the retry fixed it. The empty beats are correct refusals: no shot covered those cliffhangers inside the beat's story-order bounds. DeepSeek found 26 shots against Flash-Lite's 12, so its scripts asked for more specific moments than the shot bank could cover

- step: parse (design gap, Claude Code)
  what: the first recap check on Kuku's pack flagged episode 31's opening scene heading as a recap, because the previous episode ends in the same location. Scene headings and camera notes repeat without being recaps
  caught by: the parse report (1 recap in 50 episodes) and reading the flagged line
  fix: recap matching now looks only at dialogue and action lines. The pack has no recaps: every episode picks up straight from the previous cliffhanger, as its continuity notes say

- step: bible (pipeline LLM, deepseek-v4.1-flash, Kuku pack, 3-episode test)
  what: split 3 episodes into 8 overlapping threads (one conflict and the answer to it as two threads), which would make the 2-per-thread promo limit meaningless. It also left a descriptive speaker tag for a recurring parent character (53 lines across the show) unlinked to that character
  caught by: reading 03_bible.json and resolving every speaker tag against the alias map
  fix: the thread rule now defines a thread as a cross-episode story line and asks for 3 to 8. The bible digest now shows the speaker tag of each key event's line and each episode's speaker tags, and the prompt asks for every tag to go in an alias list. Rerun: 5 threads, and the tag resolves to the right character

- step: bible_merge (config gap, Claude Code)
  what: on 50 episodes the merge of five chunk bibles failed 4 times ("no json object in response", once a schema error with an empty object). The chunk calls already used 13 to 14k completion tokens against max_tokens 16384, so the merge output (with reasoning) was being cut off. The run stopped at Step 3, as designed, after about $0.03 of failed calls
  caught by: run log plus completion token counts in usage.jsonl
  fix: max_tokens is now set per prompt (bible 32768, bible_merge 65536; DeepSeek allows about 940k output tokens), the budget check uses the same limit, and a cut-off response now prints "output cut off at max_tokens" instead of looking like bad JSON

- step: scripts (config gap, Claude Code), same cause as the bible merge
  what: on 50 episodes the single scripts call (20 promo scripts over the whole show) was cut off at 16384 tokens and returned broken JSON. I had raised the limit only for the bible and should have checked the other whole-show prompts at the same time
  caught by: the new "output cut off" warning on the first attempt
  fix: the run was stopped before the remaining retries spent money on the same cut-off; scripts 49152 and match 32768 were added to llm.max_tokens_by_prompt, and the run resumed from Step 5 (Steps 1 to 4 cached)

- step: shots (pipeline LLM, deepseek-v4.1-flash, Kuku pack)
  what: returned an empty JSON object "{}" on 3 of 50 episodes (not a token cut-off; the largest shots answer used 10.5k of 16k)
  caught by: schema errors in the run log
  fix: the existing retry with the error in the prompt recovered every case; no shots episode failed

- step: bible thread rule (over-correction, Claude Code)
  what: to fix the 3-episode test's 8 fragmented threads I asked for "3 to 8 threads for a whole series". On 50 episodes DeepSeek then made 5 broad threads, one of them a catch-all theme rather than a story line (116 events). 9 of the 15 eligible promos had that thread, so the plan's 2-per-thread limit capped selection at 7 promos, below the minimum of 12
  caught by: selection note "only 7 promos passed", then counting main_thread over 07_scored.json
  fix: a thread is now defined as one specific story line with its own goal or antagonist (not a theme), about 2 to 5 per 10 episodes, and no thread may hold most of the events. The merge keeps separate story lines apart. The run was kept as raw_outputs/show1_v1_broadthreads and rerun from Step 3

- step: bible twists (pipeline LLM, deepseek-v4.1-flash, Kuku pack)
  what: marked the show's premise (the inciting event of episode 1) as a MAJOR twist paid off in episode 1. That is the show's premise and its strongest hook; the spoiler guard would have dropped every promo built on it
  caught by: reading the major twists in 03_bible.json before Step 5
  fix: rule in code (bible.premise): a twist paid off in episode 1 is the premise, not a spoiler, because a promo sends viewers to episode 1. Assembly, script spoiler events, the baseline flag and check_rules all skip it. Fixed in code rather than the prompt, so the 50 cached shot calls stay valid. Test: test_episode_one_payoff_is_premise

- step: scripts (pipeline LLM, deepseek-v4.1-flash, Kuku pack)
  what: even with 10 balanced threads, 9 of 16 assembled promos took their hook from T1 (the thread that covers the first act, episodes 1 to 18), so the 2-per-thread rule left 9 promos, below the minimum of 12. The writer kept picking the hook-rich first act
  caught by: selection note plus main_thread counts in 07_scored.json (v2 run kept as raw_outputs/show1_v2_20scripts)
  fix: the scripts prompt now states the 2-per-thread limit and asks for at most 3 scripts per hook thread across all threads, and num_scripts went from 20 to 28. The quality bar was not lowered

- step: bible aliases (pipeline LLM, deepseek-v4.1-flash, Kuku pack)
  what: 9 recurring speaker tags stay unresolved (3 to 19 lines each). Most are unnamed extras, but one is a named character missing from the bible, and one character has two entries although a twist says they are the same person. One descriptive tag sits under two characters because the script uses it for two different people
  caught by: check_rules aliases FAIL
  fix: none yet; left as a reported failure. Effect is limited to thread and character diversity and code coherence, not to quotes or timing
