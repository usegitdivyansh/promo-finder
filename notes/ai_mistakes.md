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
