import pytest
from src import score_blind


def key_and_results():
    def j(h, c, cl, t):
        return {"hook": h, "cliffhanger": c, "clarity": cl, "total": t}
    key = {"sheet_id": "s1", "items": {
        "X01": {"source": "tool", "judge": j(8, 8, 8, 90)},
        "X02": {"source": "tool", "judge": j(5, 8, 6, 70)},
        "X03": {"source": "baseline", "judge": j(8, 5, 8, 80)},
        "X04": {"source": "baseline", "judge": j(5, 5, 5, 60)}}}
    def r(h, c, s):
        return {"hook": h, "cliffhanger": c, "standalone": s, "notes": ""}
    res = {"sheet_id": "s1", "items": {
        "X01": r("pass", "pass", "pass"), "X02": r("fail", "pass", "fail"),
        "X03": r("pass", "fail", "pass"), "X04": r("fail", "fail", "pass")}}
    return key, res


def test_pass_rates_and_agreement():
    key, res = key_and_results()
    out = score_blind.score(res, key, 7)
    assert out["pass_rate"]["hook"]["tool"]["pass_rate"] == 0.5
    assert out["pass_rate"]["cliffhanger"]["tool"]["pass_rate"] == 1.0
    assert out["pass_rate"]["standalone"]["baseline"]["pass_rate"] == 1.0
    assert out["agreement"]["hook"]["agree"] == 1.0
    assert out["agreement"]["standalone"]["agree"] == 0.75
    assert out["agreement"]["standalone"]["human_pass_judge_fail"] == 1
    assert out["spearman_human_passes_vs_judge_total"]["all"] == pytest.approx(0.949, abs=0.001)


def test_wrong_sheet_rejected():
    key, res = key_and_results()
    res["sheet_id"] = "other"
    with pytest.raises(SystemExit):
        score_blind.score(res, key, 7)
