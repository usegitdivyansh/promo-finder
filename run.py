import os
import sys
import time
import argparse
import yaml

from src import llm, parse, episodes, bible, shots, scripts, assemble, score, baseline, report
from src import select as selector
from src.common import save, load, load_dir

STEPS = ["step1", "step2", "step3", "step4", "step5", "step6", "step7", "step8", "baseline", "step9"]
ROOT = os.path.dirname(os.path.abspath(__file__))


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ep_filter(spec):
    if not spec:
        return None
    res = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            for i in range(int(a), int(b)+1):
                res.add(i)
        elif part:
            res.add(int(part))
    return res


def save_logs(out, name, drops, failed):
    save(os.path.join(out, "logs", f"drops_{name}.json"), drops)
    save(os.path.join(out, "logs", f"failed_{name}.json"), failed)


def run(inp, out, cfg, start="step1", only=None, progress=None, stop=None):
    os.makedirs(out, exist_ok=True)
    i0 = STEPS.index(start)
    todo = STEPS[i0:]
    if stop:
        todo = STEPS[i0:STEPS.index(stop)+1]
    if only:
        todo = [only]
    t0 = time.time()

    def tick(name):
        print(f"\n== {name} ==")
        if progress:
            progress(name, STEPS.index(name), len(STEPS))

    eps = None
    keep = ep_filter(cfg.get("_episodes"))
    if "step1" in todo:
        tick("step1")
        drops = []
        failed = []
        eps, pf = parse.parse_folder(inp, cfg)
        if keep:
            arr = []
            for e in eps:
                if e["episode"] in keep:
                    arr.append(e)
            eps = arr
            parse.mark_recaps(eps, cfg["parse"])
        d = os.path.join(out, "01_parsed")
        os.makedirs(d, exist_ok=True)
        for e in eps:
            save(os.path.join(d, f"ep_{e['episode']:02d}.json"), e)
        save(os.path.join(d, "_report.json"), parse.make_report(eps, pf))
        for f in pf:
            failed.append({"step": "parse", "file": f["file"], "error": f["error"]})
        parse.print_report(eps, pf)
        save_logs(out, "step1", drops, failed)
        if not eps:
            raise SystemExit("no episodes parsed, stopping")
    else:
        eps = load_dir(os.path.join(out, "01_parsed"))

    summ = None
    if "step2" in todo:
        tick("step2")
        drops = []
        failed = []
        summ = episodes.run(eps, cfg, out, drops, failed)
        save_logs(out, "step2", drops, failed)
    if summ is None:
        summ = load_dir(os.path.join(out, "02_episodes"))
    summ_by = {}
    for s in summ:
        summ_by[s["episode"]] = s

    bib = None
    if "step3" in todo:
        tick("step3")
        drops = []
        failed = []
        try:
            bib = bible.run(summ, cfg, out, drops)
        except Exception as ex:
            failed.append({"step": "bible", "error": str(ex)[:300]})
            save_logs(out, "step3", drops, failed)
            raise SystemExit(f"bible failed, cannot continue: {ex}")
        save_logs(out, "step3", drops, failed)
    if bib is None and STEPS.index(todo[-1]) >= 3:
        bib = load(os.path.join(out, "03_bible.json"))
    if bib is not None and cfg.get("_aliases") and bib.get("overrides_file") != os.path.basename(cfg["_aliases"]):
        bib = bible.apply_overrides(bib, cfg["_aliases"])
        save(os.path.join(out, "03_bible.json"), bib)
        print(f"  alias overrides applied from {cfg['_aliases']}")

    bank = None
    if "step4" in todo:
        tick("step4")
        drops = []
        failed = []
        bank = shots.run(eps, summ_by, bib, cfg, out, drops, failed)
        save_logs(out, "step4", drops, failed)
    if bank is None and STEPS.index(todo[-1]) >= 4:
        bank = {}
        for d in load_dir(os.path.join(out, "04_shots")):
            bank[d["episode"]] = d["shots"]

    cands = None
    if "step5" in todo:
        tick("step5")
        drops = []
        failed = []
        cands = scripts.run(summ, bib, bank, eps, cfg, out, drops, failed)
        save_logs(out, "step5", drops, failed)

    promos = None
    if "step6" in todo:
        tick("step6")
        drops = []
        if cands is None:
            cands = load(os.path.join(out, "05_candidates.json"))
        promos = assemble.run(cands["candidates"], bank, eps, bib, summ, cfg, out, drops)
        save_logs(out, "step6", drops, [])

    scored = None
    if "step7" in todo:
        tick("step7")
        drops = []
        failed = []
        if promos is None:
            promos = load(os.path.join(out, "06_assembled.json"))
        scored = score.run(promos, eps, bib, cfg, out, drops, failed)
        save_logs(out, "step7", drops, failed)

    sel = None
    if "step8" in todo:
        tick("step8")
        drops = []
        if scored is None:
            scored = load(os.path.join(out, "07_scored.json"))
        sel = selector.run(scored, eps, cfg, out, drops)
        save_logs(out, "step8", drops, [])

    if "baseline" in todo and cfg.get("_baseline", True):
        tick("baseline")
        drops = []
        failed = []
        if sel is None:
            sel = load(os.path.join(out, "08_selected.json"))
        baseline.run(eps, bib, sel["selected"], cfg, out, drops, failed)
        save_logs(out, "baseline", drops, failed)

    res = None
    if "step9" in todo:
        tick("step9")
        if sel is None:
            sel = load(os.path.join(out, "08_selected.json"))
        res = report.run(sel, eps, cfg, out)
    print(f"\ndone in {round(time.time() - t0, 1)}s -> {out}")
    return res


def main():
    ap = argparse.ArgumentParser(description="find promo clips in timestamped screenplays")
    ap.add_argument("--input", required=True, help="folder with the episode screenplays")
    ap.add_argument("--out", help="output folder (default raw_outputs/<input folder name>)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--from", dest="start", default="step1", choices=STEPS, help="resume from this step using saved outputs")
    ap.add_argument("--only", choices=STEPS, help="run just this step")
    ap.add_argument("--to", choices=STEPS, help="stop after this step")
    ap.add_argument("--aliases", help="hand-checked alias overrides (json), kept next to the input, e.g. data/show1_aliases.json")
    ap.add_argument("--episodes", help="only these episodes, e.g. 1-3 or 1,4,7")
    ap.add_argument("--model", help="use this model for every step (clears step_models and judge_model)")
    ap.add_argument("--confidential", action="store_true", help="paid confidential_model for every step, no free tier, no fallback (automatic for inputs under data/)")
    ap.add_argument("--judge-model", help="override llm.judge_model")
    ap.add_argument("--num-promos", type=int, help="override promo.num_promos_max")
    ap.add_argument("--mock", action="store_true", help="offline: read llm responses from mock files")
    ap.add_argument("--mock-dir", help="folder of mock responses (default tests/fixtures/mock_responses)")
    ap.add_argument("--no-baseline", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = load_cfg(a.config)
    if a.model:
        cfg["llm"]["provider_model"] = a.model
        cfg["llm"]["step_models"] = {}
        cfg["llm"]["judge_model"] = ""
    inside_data = os.path.abspath(a.input).lower().startswith(os.path.join(ROOT, "data").lower())
    if a.confidential or inside_data:
        cfg["llm"]["confidential"] = True
        print(f"confidential mode: every step uses {cfg['llm']['confidential_model']}, no free tier, no fallback")
    if a.judge_model:
        cfg["llm"]["judge_model"] = a.judge_model
    if a.num_promos:
        cfg["promo"]["num_promos_max"] = a.num_promos
        if cfg["promo"]["num_promos_min"] > a.num_promos:
            cfg["promo"]["num_promos_min"] = a.num_promos
    if a.mock:
        cfg["llm"]["mock"] = True
    cfg["_episodes"] = a.episodes
    cfg["_aliases"] = a.aliases
    if a.no_baseline:
        cfg["_baseline"] = False
    out = a.out
    if not out:
        out = os.path.join(ROOT, "raw_outputs", os.path.basename(os.path.normpath(a.input)))
    llm.setup(cfg, run_dir=out, mock_dir=a.mock_dir)
    run(a.input, out, cfg, start=a.start, only=a.only, stop=a.to)


if __name__ == "__main__":
    main()
