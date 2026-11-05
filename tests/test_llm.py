import os
import sys
import copy
import types
import yaml
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import llm
from src.schemas import MatchOut

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


class RateLimitError(Exception):
    pass


def fake_resp(text, pt=1000, ct=200):
    r = types.SimpleNamespace()
    r.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
    r.usage = types.SimpleNamespace(prompt_tokens=pt, completion_tokens=ct)
    r._hidden_params = {}
    return r


def setup(tmp_path, monkeypatch, behaviour):
    import litellm
    cfg = copy.deepcopy(CFG)
    cfg["llm"]["rpm_limit"] = 0
    cfg["llm"]["rate_retries"] = 1
    cfg["llm"]["step_models"] = {}
    llm.setup(cfg, run_dir=str(tmp_path / "run"), cache_dir=str(tmp_path / "cache"))
    llm.state["primary_down"] = False
    calls = []

    def fake(model, **kw):
        calls.append(model)
        return behaviour(model)
    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return cfg, calls


GOOD = '{"script_id": "C1", "picks": [{"beat_index": 0, "shot_id": "none"}]}'
VARS = {"script_id": "C1", "template": "mini_story", "idea": "x", "beats": "BEAT 0"}


def test_daily_quota_switches_to_fallback_and_sticks(tmp_path, monkeypatch):
    def beh(model):
        if model.startswith("gemini/"):
            raise RateLimitError("429 RESOURCE_EXHAUSTED quota GenerateRequestsPerDayPerProjectPerModel")
        return fake_resp(GOOD)
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.call_llm("match", VARS, MatchOut)
    vs = dict(VARS)
    vs["idea"] = "y"
    llm.call_llm("match", vs, MatchOut)
    fb = llm.real_name(cfg["llm"]["fallback_model"], cfg)
    # second call goes straight to the fallback
    assert calls == [llm.real_name(cfg["llm"]["provider_model"], cfg), fb, fb]
    assert llm.spent() > 0


def test_per_minute_limit_falls_back_for_one_call(tmp_path, monkeypatch):
    def beh(model):
        if model.startswith("gemini/"):
            raise RateLimitError("429 quota exceeded GenerateRequestsPerMinutePerProjectPerModel")
        return fake_resp(GOOD)
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.call_llm("match", VARS, MatchOut)
    assert llm.state.get("primary_down") is False
    assert calls[-1] == llm.real_name(cfg["llm"]["fallback_model"], cfg)


def test_budget_stops_run(tmp_path, monkeypatch):
    def beh(model):
        if model.startswith("gemini/"):
            raise RateLimitError("429 per day")
        return fake_resp(GOOD, pt=10_000_000, ct=10_000_000)
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.state["cfg"]["llm"]["budget_usd"] = 0.5
    llm.call_llm("match", VARS, MatchOut)
    vs = dict(VARS)
    vs["idea"] = "z"
    with pytest.raises(llm.BudgetError):
        llm.call_llm("match", vs, MatchOut)


def test_cache_hit_costs_nothing(tmp_path, monkeypatch):
    cfg, calls = setup(tmp_path, monkeypatch, lambda m: fake_resp(GOOD))
    llm.call_llm("match", VARS, MatchOut)
    llm.call_llm("match", VARS, MatchOut)
    assert len(calls) == 1


def test_run_stops_when_both_providers_down(tmp_path, monkeypatch):
    def beh(model):
        if model.startswith("gemini/"):
            raise RateLimitError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        raise Exception("AuthenticationError: OpenrouterException - User not found. 401")
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    with pytest.raises(llm.ProvidersDown):
        llm.call_llm("match", VARS, MatchOut)


def test_step_models_and_confidential(tmp_path, monkeypatch):
    cfg, calls = setup(tmp_path, monkeypatch, lambda m: fake_resp(GOOD))
    llm.state["cfg"]["llm"]["step_models"] = {"match": "openrouter/x/y", "episode": "gemini/free"}
    assert llm.model_for("main", "match") == "openrouter/x/y"
    assert llm.model_for("main", "episode") == "gemini/free"
    llm.state["cfg"]["llm"]["confidential"] = True
    assert llm.model_for("main", "episode") == llm.real_name(llm.state["cfg"]["llm"]["confidential_model"])
    assert llm.model_for("judge", "judge_full") == llm.real_name(llm.state["cfg"]["llm"]["confidential_model"])


def test_confidential_never_falls_back_to_free(tmp_path, monkeypatch):
    def beh(model):
        raise RateLimitError("429 RESOURCE_EXHAUSTED per day")
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.state["cfg"]["llm"]["confidential"] = True
    llm.state["cfg"]["llm"]["fallback_model"] = "gemini/free"
    with pytest.raises(llm.ProvidersDown):
        llm.call_llm("match", VARS, MatchOut)
    for c in calls:
        assert not c.startswith("gemini/")


class ServiceUnavailableError(Exception):
    pass


def test_outage_falls_back_for_one_call(tmp_path, monkeypatch):
    def beh(model):
        if model.startswith("gemini/"):
            raise ServiceUnavailableError("503 This model is currently experiencing high demand")
        return fake_resp(GOOD)
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.call_llm("match", VARS, MatchOut)
    assert calls[-1] == llm.real_name(cfg["llm"]["fallback_model"], cfg)
    assert not llm.state.get("primary_down")


def test_outage_in_confidential_mode_does_not_fall_back(tmp_path, monkeypatch):
    def beh(model):
        raise ServiceUnavailableError("503 overloaded")
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.state["cfg"]["llm"]["confidential"] = True
    with pytest.raises(llm.OutageError):
        llm.call_llm("match", VARS, MatchOut)
    for m in calls:
        assert m == llm.real_name(cfg["llm"]["confidential_model"], cfg)


def test_model_slots_and_unknown_price(tmp_path, monkeypatch):
    cfg, calls = setup(tmp_path, monkeypatch, lambda m: fake_resp(GOOD))
    lc = llm.state["cfg"]["llm"]
    lc["models"] = {"strong": "acme/brand-new-model"}
    lc["step_models"] = {"match": "strong"}
    lc["confidential"] = False
    assert llm.model_for("main", "match") == "acme/brand-new-model"
    lc["confidential"] = True
    lc["confidential_model"] = "strong"
    assert llm.model_for("main", "episode") == "acme/brand-new-model"
    # a model with no configured or known price is still budgeted, never treated as free
    pi, po = llm.price("acme/brand-new-model")
    assert pi > 0 and po > 0


class AuthenticationError(Exception):
    pass


def test_auth_error_stops_on_first_call(tmp_path, monkeypatch):
    def beh(model):
        raise AuthenticationError('{"error":{"message":"User not found.","code":401}}')
    cfg, calls = setup(tmp_path, monkeypatch, beh)
    llm.state["cfg"]["llm"]["fallback_model"] = None
    with pytest.raises(llm.ProvidersDown):
        llm.call_llm("match", VARS, MatchOut)
    assert len(calls) == 1


def test_session_key_passed_per_call_and_redacted(tmp_path, monkeypatch):
    import litellm
    key = "sk-test-" + "a1b2c3d4" * 6
    cfg = copy.deepcopy(CFG)
    cfg["llm"]["rpm_limit"] = 0
    cfg["llm"]["step_models"] = {}
    cfg["llm"]["fallback_model"] = None
    llm.setup(cfg, run_dir=str(tmp_path / "run"), cache_dir=str(tmp_path / "cache"), api_key=key, use_env=False)
    seen = []

    def fake(model, **kw):
        seen.append(kw.get("api_key"))
        raise AuthenticationError("Incorrect API key provided: " + key[:12] + "****" + key[-8:])
    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(llm.ProvidersDown) as ex:
        llm.call_llm("match", VARS, MatchOut)
    assert seen == [key]
    assert key[:12] not in str(ex.value) and key[-8:] not in str(ex.value)
    llm.forget_key()
    assert llm.state["api_key"] is None
    log = tmp_path / "run" / "usage.jsonl"
    if log.exists():
        assert key[-8:] not in log.read_text(encoding="utf-8")


def test_run_cap_stops_before_going_over(tmp_path, monkeypatch):
    # the check bounds the next call by its prompt and max_tokens: $0.6 of output per call, cap $1
    cfg, calls = setup(tmp_path, monkeypatch, lambda m: fake_resp(GOOD, pt=0, ct=600000))
    lc = llm.state["cfg"]["llm"]
    lc["provider_model"] = "acme/paid"
    lc["fallback_model"] = None
    lc["prices"] = {"acme/paid": [0.0, 1.0]}
    lc["max_tokens"] = 600000
    lc["max_tokens_by_prompt"] = {}
    lc["run_cap_usd"] = 1.0
    llm.call_llm("match", VARS, MatchOut)
    assert round(llm.state["run_spent"], 3) == 0.6
    v2 = dict(VARS)
    v2["idea"] = "y"
    with pytest.raises(llm.BudgetError):
        llm.call_llm("match", v2, MatchOut)
    assert len(calls) == 1
