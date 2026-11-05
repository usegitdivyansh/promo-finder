# Failure gallery

By Divyansh Gupta.

Promos that passed every hard rule but still had something wrong a human reviewer would catch, and promos the rules let through until a rule was added. Show names, character names and script text are left out on purpose; the run folders under raw_outputs/ hold the details.

## 1. Cliffhanger summary uses a word the viewer never sees (Kuku pack, montage, final ep of the pack)

- promo: montage of 3 cuts from one episode, total 74.86, cliffhanger score 8 (P06 in the review, P05 in the final list)
- what the output says: the cliffhanger is a minor character discovering a "lethal" secret
- what the screenplay shows: the character witnesses a secret from out of sight and reacts in shock. No line of dialogue, action or sound in the cut says or shows any danger.
- where the word came from: the last camera direction in the cut uses it. It is the writer's note to the director, not something on screen, and the summary copied it as if the promo showed it.
- why it matters: the "why it works" text sells a stake the clip doesn't deliver. An editor reading the CSV would expect a threat on screen, and a viewer would not get one.
- first reported as: an overstatement of the script. On a full re-read that was half right: the word is in the script, but only in a camera note.
- possible fix (not done): give the cliffhanger summary only dialogue, action and sound lines, or tell it that CAMERA lines are directions, not events.

## 2. Two selected promos used the same footage (Kuku pack, fixed)

- promos: an ending_scene (76.34) and a romance_tension (71.58) both cut ep46 01:52-02:13
- why the rules missed it: the diversity rules were max 2 per thread and one hook shot per promo. The two promos had different hook shots and different threads, so both passed.
- fix: select.no_shared_footage. Two selected promos may not use the same episode over an overlapping time range, and check_rules reports any pair as shared_footage. Category coverage runs first, so the romance_tension promo (the only one of its template) kept the footage and the higher-scoring ending_scene was dropped. Its slot went to the next eligible promo (70.5).

## 3. Hook 20.6 s into a single-scene promo (Kuku pack, fixed)

- promo: single_scene, top of the v5 list at 89.16
- what went wrong: when a long scene had no hook-role shot inside it, assembly kept the scene's own start and never moved it to the key line, so the hook landed 20.6 s in, far past the 3 s rule. check_rules missed it too, because it read the hook position the assembly had recorded.
- caught by: an independent verifier that recomputed hook timing from the parsed screenplay
- fix: the generic hook-window move now also applies to these promos; regression test added
