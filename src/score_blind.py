# score a filled-in blind sheet: pass rates for tool vs baseline per criterion, and how far the
# human agrees with the llm judge. judge "pass" means a 0-10 score >= --thr (standalone uses fresh-viewer clarity).
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.common import load, save

CRIT = [("hook", "hook"), ("cliffhanger", "cliffhanger"), ("standalone", "clarity")]


def ranks(xs):
    # average ranks for ties
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j+1 < len(order) and xs[order[j+1]] == xs[order[i]]:
            j += 1
        for k in range(i, j+1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra = ranks(a)
    rb = ranks(b)
    ma = sum(ra) / len(ra)
    mb = sum(rb) / len(rb)
    num = 0.0
    da = 0.0
    db = 0.0
    for x, y in zip(ra, rb):
        num += (x - ma) * (y - mb)
        da += (x - ma) ** 2
        db += (y - mb) ** 2
    if da == 0 or db == 0:
        return None
    return round(num / (da * db) ** 0.5, 3)


def kappa(pairs):
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for h, j in pairs if h == j) / n
    ph = sum(1 for h, j in pairs if h) / n
    pj = sum(1 for h, j in pairs if j) / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    if pe == 1:
        return None
    return round((po - pe) / (1 - pe), 3)


def rate(xs):
    if not xs:
        return None
    return round(sum(1 for x in xs if x) / len(xs), 3)


def score(res, key, thr):
    if res.get("sheet_id") != key["sheet_id"]:
        raise SystemExit(f"results are for sheet {res.get('sheet_id')}, key is for {key['sheet_id']}")
    out = {"sheet_id": key["sheet_id"], "judge_pass_threshold": thr, "rated": 0, "skipped": [], "pass_rate": {}, "agreement": {}}
    rows = []
    for bid, k in key["items"].items():
        r = res["items"].get(bid) or {}
        hum = {}
        for c, _ in CRIT:
            v = r.get(c)
            if v in ["pass", "fail"]:
                hum[c] = v == "pass"
        if len(hum) < len(CRIT):
            out["skipped"].append(bid)
        rows.append({"id": bid, "source": k["source"], "human": hum, "judge": k["judge"], "notes": r.get("notes", "")})
    out["rated"] = len(rows) - len(out["skipped"])
    for c, jk in CRIT:
        pr = {}
        for src in ["tool", "baseline"]:
            xs = []
            for x in rows:
                if x["source"] == src and c in x["human"]:
                    xs.append(x["human"][c])
            pr[src] = {"pass_rate": rate(xs), "n": len(xs)}
        out["pass_rate"][c] = pr
        pairs = []
        for x in rows:
            if c in x["human"]:
                pairs.append((x["human"][c], x["judge"][jk] >= thr))
        agree = None
        if pairs:
            agree = round(sum(1 for h, j in pairs if h == j) / len(pairs), 3)
        out["agreement"][c] = {"agree": agree, "kappa": kappa(pairs), "n": len(pairs),
                               "judge_pass_human_fail": sum(1 for h, j in pairs if j and not h),
                               "human_pass_judge_fail": sum(1 for h, j in pairs if h and not j)}
    # rank agreement: human passes (0-3) against the judge total
    full = [x for x in rows if len(x["human"]) == len(CRIT)]
    hp = [sum(1 for v in x["human"].values() if v) for x in full]
    jt = [x["judge"]["total"] for x in full]
    out["spearman_human_passes_vs_judge_total"] = {"all": spearman(hp, jt), "n": len(full)}
    for src in ["tool", "baseline"]:
        sub = [i for i, x in enumerate(full) if x["source"] == src]
        out["spearman_human_passes_vs_judge_total"][src] = spearman([hp[i] for i in sub], [jt[i] for i in sub])
    allp = {}
    for src in ["tool", "baseline"]:
        xs = [hp[i] == len(CRIT) for i, x in enumerate(full) if x["source"] == src]
        allp[src] = rate(xs)
    out["pass_all_three"] = allp
    out["items"] = rows
    return out


def show(out):
    print(f"sheet {out['sheet_id']}: {out['rated']} fully rated, skipped {out['skipped'] or 'none'}")
    print(f"\n{'criterion':12} {'tool':>12} {'baseline':>12}   judge agreement (pass = score >= {out['judge_pass_threshold']})")
    for c, _ in CRIT:
        pr = out["pass_rate"][c]
        ag = out["agreement"][c]
        cells = []
        for src in ["tool", "baseline"]:
            v = pr[src]["pass_rate"]
            txt = "-"
            if v is not None:
                txt = f"{round(v*100)}% of {pr[src]['n']}"
            cells.append(txt)
        agree = "-"
        if ag["agree"] is not None:
            agree = f"{round(ag['agree']*100)}%"
        print(f"{c:12} {cells[0]:>12} {cells[1]:>12}   {agree} agree, kappa {ag['kappa']}, judge-only passes {ag['judge_pass_human_fail']}, human-only passes {ag['human_pass_judge_fail']}")
    pa = out["pass_all_three"]
    print(f"\npass all three: tool {pa['tool']}, baseline {pa['baseline']}")
    sp = out["spearman_human_passes_vs_judge_total"]
    print(f"spearman (human passes vs judge total): all {sp['all']} (n={sp['n']}), tool {sp['tool']}, baseline {sp['baseline']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="json exported from blind_sheet.html")
    ap.add_argument("--key", default=os.path.join("eval", "blind_key.json"))
    ap.add_argument("--thr", type=float, default=7.0)
    ap.add_argument("--out", default=os.path.join("eval", "blind_scored.json"))
    a = ap.parse_args()
    out = score(load(a.results), load(a.key), a.thr)
    show(out)
    save(a.out, out)
    print(f"\nsaved {a.out}")


if __name__ == "__main__":
    main()
