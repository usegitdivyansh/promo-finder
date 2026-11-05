import os
import re
import json
import time
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
state = {"cfg": None, "run_dir": None, "cache_dir": os.path.join(ROOT, "cache"),
         "mock_dir": os.path.join(ROOT, "tests", "fixtures", "mock_responses"), "last_call": 0.0}


class LLMError(Exception):
    pass


def load_env(path=None):
    if path is None:
        path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v and not os.environ.get(k):
                os.environ[k] = v


def setup(cfg, run_dir=None, cache_dir=None, mock_dir=None):
    state["cfg"] = cfg
    state["run_dir"] = run_dir
    state["run_id"] = time.strftime("%Y%m%d-%H%M%S")
    if cache_dir:
        state["cache_dir"] = cache_dir
    if mock_dir:
        state["mock_dir"] = mock_dir
    load_env()


def model_for(kind="main", prompt_name=None):
    lc = state["cfg"]["llm"]
    if lc.get("confidential"):
        return lc["confidential_model"]
    if prompt_name and lc.get("step_models", {}).get(prompt_name):
        return lc["step_models"][prompt_name]
    if kind == "judge" and lc.get("judge_model"):
        return lc["judge_model"]
    return lc.get("provider_model") or ""


def fill(prompt_name, variables):
    with open(os.path.join(ROOT, "prompts", prompt_name + ".txt"), encoding="utf-8") as f:
        res = f.read()
    for k, v in variables.items():
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        res = res.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{(\w+)\}\}", res)
    if left:
        raise LLMError(f"prompt {prompt_name} has unfilled vars: {left}")
    return res


def extract_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    a = text.find("{")
    b = text.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no json object in response")
    return json.loads(text[a:b+1])


def log_usage(row):
    if not state["run_dir"]:
        return
    os.makedirs(state["run_dir"], exist_ok=True)
    row["run_id"] = state.get("run_id")
    with open(os.path.join(state["run_dir"], "usage.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def throttle(model):
    lc = state["cfg"]["llm"]
    rpm = lc.get("rpm_limits", {}).get(model, lc.get("rpm_limit")) or 0
    if rpm <= 0:
        return
    gap = 60.0 / rpm
    last = state.setdefault("last_by_model", {}).get(model, 0.0)
    wait = last + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    state["last_by_model"][model] = time.time()


class QuotaError(LLMError):
    pass


# not an Exception subclass on purpose: steps catch Exception, but going over budget must stop the run
class BudgetError(SystemExit):
    pass


def price(model):
    p = state["cfg"]["llm"].get("prices", {}).get(model)
    if not p:
        return 0.0, 0.0
    return float(p[0]), float(p[1])


def spend_path():
    return os.path.join(state["cache_dir"], "_spend.jsonl")


def spent():
    res = 0.0
    p = spend_path()
    if not os.path.exists(p):
        return res
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                res += json.loads(ln)["cost_usd"]
    return res


def cur_max():
    lc = state["cfg"]["llm"]
    if state.get("max_tokens"):
        return state["max_tokens"]
    return lc.get("max_tokens", 8192)


def check_budget(model, prompt):
    pi, po = price(model)
    if pi == 0 and po == 0:
        return
    lc = state["cfg"]["llm"]
    est = len(prompt) / 4 / 1e6 * pi + cur_max() / 1e6 * po
    cur = spent()
    if cur + est > lc.get("budget_usd", 2.0):
        raise BudgetError(f"paid spend would go over budget: spent ${cur:.4f}, next call up to ${est:.4f}, budget ${lc.get('budget_usd', 2.0)}")


def is_daily(msg):
    m = msg.lower().replace(" ", "")
    for k in ["perday", "daily", "requestsperday", "quotaexceededformetric"]:
        if k in m:
            if "perminute" in m and "perday" not in m:
                return False
            return True
    return False


def one_call(model, prompt):
    import litellm
    litellm.drop_params = True
    litellm.suppress_debug_info = True
    lc = state["cfg"]["llm"]
    check_budget(model, prompt)
    tries = 0
    rate_tries = 0
    while True:
        throttle(model)
        t0 = time.time()
        kw = {}
        if lc.get("reasoning_effort"):
            kw["reasoning_effort"] = lc["reasoning_effort"]
        try:
            resp = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                      temperature=lc.get("temperature", 0), max_tokens=cur_max(),
                                      response_format={"type": "json_object"}, timeout=lc.get("timeout_sec", 180), **kw)
        except Exception as ex:
            name = type(ex).__name__
            msg = str(ex)
            rate = "RateLimit" in name or "429" in msg or "RESOURCE_EXHAUSTED" in msg
            if rate:
                if is_daily(msg):
                    raise QuotaError(f"daily quota: {msg[:300]}")
                rate_tries += 1
                if rate_tries > lc.get("rate_retries", 3):
                    raise QuotaError(f"rate limited {rate_tries} times: {msg[:300]}")
                wait = 15 * (2 ** (rate_tries-1))
                print(f"    {model} rate limited, backing off {wait}s")
                time.sleep(wait)
                continue
            retryable = False
            for k in ["Timeout", "ServiceUnavailable", "InternalServer", "APIConnection", "BadGateway"]:
                if k in name:
                    retryable = True
            if "503" in msg or "500" in msg or "overloaded" in msg.lower():
                retryable = True
            tries += 1
            if not retryable or tries > 4:
                raise LLMError(f"{name}: {msg[:300]}")
            wait = 5 * (2 ** (tries-1))
            print(f"    {name}, backing off {wait}s")
            time.sleep(wait)
            continue
        text = resp.choices[0].message.content or ""
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            print(f"    output cut off at max_tokens {cur_max()}; raise llm.max_tokens_by_prompt for this prompt")
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        if getattr(resp, "usage", None):
            usage["prompt_tokens"] = resp.usage.prompt_tokens or 0
            usage["completion_tokens"] = resp.usage.completion_tokens or 0
        usage["latency"] = round(time.time() - t0, 2)
        usage["model"] = model
        pi, po = price(model)
        cost = usage["prompt_tokens"] / 1e6 * pi + usage["completion_tokens"] / 1e6 * po
        rep = None
        try:
            rep = resp._hidden_params.get("response_cost")
        except Exception:
            rep = None
        # litellm prices every model at paid rates; only trust it for models we pay for
        if (pi > 0 or po > 0) and rep and rep > cost:
            cost = rep
        usage["cost_usd"] = round(cost, 6)
        if cost > 0:
            os.makedirs(state["cache_dir"], exist_ok=True)
            with open(spend_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "model": model, "cost_usd": cost, "prompt_tokens": usage["prompt_tokens"],
                                    "completion_tokens": usage["completion_tokens"]}) + "\n")
        return text, usage


# not an Exception subclass: when no provider can answer, the run must stop, not write empty outputs
class ProvidersDown(SystemExit):
    pass


def dead(ex):
    s = str(ex)
    if isinstance(ex, QuotaError):
        return True
    for k in ["AuthenticationError", "401", "403", "User not found", "invalid api key", "API key not valid", "PermissionDenied"]:
        if k.lower() in s.lower():
            return True
    return False


def call_fallback(fb, prompt, why):
    try:
        return one_call(fb, prompt)
    except LLMError as ex:
        if dead(ex):
            raise ProvidersDown(f"no provider available. primary: {why[:200]} | fallback {fb}: {str(ex)[:200]}")
        raise


def raw_call(model, prompt):
    fb = state["cfg"]["llm"].get("fallback_model")
    if state["cfg"]["llm"].get("confidential"):
        fb = None
    if fb and model != fb and state.get("primary_down"):
        return call_fallback(fb, prompt, state["primary_down"])
    try:
        return one_call(model, prompt)
    except QuotaError as ex:
        if not fb or model == fb:
            raise ProvidersDown(f"{model}: {str(ex)[:300]}")
        if "daily quota" in str(ex):
            state["primary_down"] = str(ex)
            print(f"    {model} daily quota used up, switching to {fb} for the rest of the run")
        else:
            print(f"    {model} still rate limited, using {fb} for this call")
        return call_fallback(fb, prompt, str(ex))


def load_mock(prompt_name, mock_key):
    d = os.path.join(state["mock_dir"], prompt_name)
    for name in [str(mock_key), "default"]:
        p = os.path.join(d, name + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise LLMError(f"no mock response for {prompt_name}/{mock_key}")


def validate(schema, text, check):
    try:
        data = extract_json(text)
    except Exception as ex:
        return None, [f"invalid json: {ex}"]
    # some models wrap the object in a list, or return just the list field
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict) and set(data[0].keys()) & set(schema.model_fields.keys()):
            data = data[0]
        else:
            for name, f in schema.model_fields.items():
                if "List" in str(f.annotation) or "list" in str(f.annotation):
                    data = {name: data}
                    break
    try:
        obj = schema.model_validate(data)
    except Exception as ex:
        return None, [f"schema error: {str(ex)[:1500]}"]
    if check:
        errs = check(obj)
        if errs:
            return obj, errs
    return obj, []


def call_llm(prompt_name, variables, schema, model=None, check=None, mock_key="default", step=None):
    cfg = state["cfg"]
    lc = cfg["llm"]
    if step is None:
        step = prompt_name
    prompt = fill(prompt_name, variables)
    state["max_tokens"] = lc.get("max_tokens_by_prompt", {}).get(prompt_name, lc.get("max_tokens", 8192))
    if lc.get("mock"):
        text = load_mock(prompt_name, mock_key)
        obj, errs = validate(schema, text, check)
        if obj is None:
            raise LLMError(f"mock {prompt_name}/{mock_key} invalid: {errs}")
        log_usage({"step": step, "prompt": prompt_name, "model": "mock", "prompt_tokens": len(prompt)//4,
                   "completion_tokens": len(text)//4, "latency": 0, "cached": False, "attempts": 1, "check_errors": errs})
        return obj
    if model is None:
        model = model_for("main", prompt_name)
    if not model:
        raise LLMError("no model set: fill llm.provider_model in config.yaml or pass --model")
    key = hashlib.sha256((model + "\n" + prompt + "\n" + schema.__name__).encode("utf-8")).hexdigest()
    cpath = os.path.join(state["cache_dir"], prompt_name, key + ".json")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as f:
            hit = json.load(f)
        obj = schema.model_validate(hit["value"])
        log_usage({"step": step, "prompt": prompt_name, "model": hit.get("model", model), "prompt_tokens": hit["usage"]["prompt_tokens"],
                   "completion_tokens": hit["usage"]["completion_tokens"], "latency": 0, "cached": True, "attempts": 0, "cost_usd": 0})
        return obj
    cur = prompt
    total = {"prompt_tokens": 0, "completion_tokens": 0, "latency": 0.0, "cost_usd": 0.0}
    used = model
    last_obj = None
    last_errs = []
    attempts = 0
    for attempt in range(lc.get("max_retries", 3) + 1):
        attempts += 1
        text, usage = raw_call(model, cur)
        for k in total:
            total[k] += usage[k]
        used = usage["model"]
        obj, errs = validate(schema, text, check)
        if obj is not None:
            last_obj = obj
        last_errs = errs
        if not errs:
            break
        print(f"    {prompt_name} attempt {attempt+1} failed: {errs[0][:160]}")
        temp = "\n".join("- " + e for e in errs[:15])
        cur = prompt + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n" + temp + "\n\nPrevious answer (fix it, return the full corrected JSON only):\n" + text[:6000]
    log_usage({"step": step, "prompt": prompt_name, "model": used, "requested_model": model, "prompt_tokens": total["prompt_tokens"],
               "completion_tokens": total["completion_tokens"], "latency": round(total["latency"], 2), "cached": False,
               "attempts": attempts, "check_errors": last_errs[:5], "cost_usd": round(total["cost_usd"], 6)})
    if last_obj is None:
        raise LLMError(f"{prompt_name}: no valid output after {attempts} attempts: {last_errs[:2]}")
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump({"value": last_obj.model_dump(), "usage": total, "model": used, "check_errors": last_errs}, f, ensure_ascii=False)
    return last_obj
