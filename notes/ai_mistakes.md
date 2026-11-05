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
