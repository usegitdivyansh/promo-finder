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


def model_for(kind="main"):
    lc = state["cfg"]["llm"]
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


def throttle():
    rpm = state["cfg"]["llm"].get("rpm_limit") or 0
    if rpm <= 0:
        return
    gap = 60.0 / rpm
    wait = state["last_call"] + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    state["last_call"] = time.time()


def raw_call(model, prompt):
    import litellm
    litellm.drop_params = True
    litellm.suppress_debug_info = True
    lc = state["cfg"]["llm"]
    tries = 0
    while True:
        throttle()
        t0 = time.time()
        try:
            resp = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                      temperature=lc.get("temperature", 0), max_tokens=lc.get("max_tokens", 8192),
                                      response_format={"type": "json_object"}, timeout=lc.get("timeout_sec", 180))
        except Exception as ex:
            name = type(ex).__name__
            retryable = False
            for k in ["RateLimit", "Timeout", "ServiceUnavailable", "InternalServer", "APIConnection", "BadGateway"]:
                if k in name:
                    retryable = True
            if "429" in str(ex) or "503" in str(ex) or "overloaded" in str(ex).lower():
                retryable = True
            tries += 1
            if not retryable or tries > 6:
                raise LLMError(f"{name}: {str(ex)[:300]}")
            wait = 5 * (2 ** (tries-1))
            if wait > 120:
                wait = 120
            print(f"    {name}, backing off {wait}s")
            time.sleep(wait)
            continue
        text = resp.choices[0].message.content or ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        if getattr(resp, "usage", None):
            usage["prompt_tokens"] = resp.usage.prompt_tokens or 0
            usage["completion_tokens"] = resp.usage.completion_tokens or 0
        usage["latency"] = round(time.time() - t0, 2)
        return text, usage


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
    if lc.get("mock"):
        text = load_mock(prompt_name, mock_key)
        obj, errs = validate(schema, text, check)
        if obj is None:
            raise LLMError(f"mock {prompt_name}/{mock_key} invalid: {errs}")
        log_usage({"step": step, "prompt": prompt_name, "model": "mock", "prompt_tokens": len(prompt)//4,
                   "completion_tokens": len(text)//4, "latency": 0, "cached": False, "attempts": 1, "check_errors": errs})
        return obj
    if model is None:
        model = model_for("main")
    if not model:
        raise LLMError("no model set: fill llm.provider_model in config.yaml or pass --model")
    key = hashlib.sha256((model + "\n" + prompt + "\n" + schema.__name__).encode("utf-8")).hexdigest()
    cpath = os.path.join(state["cache_dir"], prompt_name, key + ".json")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as f:
            hit = json.load(f)
        obj = schema.model_validate(hit["value"])
        log_usage({"step": step, "prompt": prompt_name, "model": model, "prompt_tokens": hit["usage"]["prompt_tokens"],
                   "completion_tokens": hit["usage"]["completion_tokens"], "latency": 0, "cached": True, "attempts": 0})
        return obj
    cur = prompt
    total = {"prompt_tokens": 0, "completion_tokens": 0, "latency": 0.0}
    last_obj = None
    last_errs = []
    attempts = 0
    for attempt in range(lc.get("max_retries", 3) + 1):
        attempts += 1
        text, usage = raw_call(model, cur)
        for k in total:
            total[k] += usage[k]
        obj, errs = validate(schema, text, check)
        if obj is not None:
            last_obj = obj
        last_errs = errs
        if not errs:
            break
        print(f"    {prompt_name} attempt {attempt+1} failed: {errs[0][:160]}")
        temp = "\n".join("- " + e for e in errs[:15])
        cur = prompt + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n" + temp + "\n\nPrevious answer (fix it, return the full corrected JSON only):\n" + text[:6000]
    log_usage({"step": step, "prompt": prompt_name, "model": model, "prompt_tokens": total["prompt_tokens"],
               "completion_tokens": total["completion_tokens"], "latency": round(total["latency"], 2), "cached": False,
               "attempts": attempts, "check_errors": last_errs[:5]})
    if last_obj is None:
        raise LLMError(f"{prompt_name}: no valid output after {attempts} attempts: {last_errs[:2]}")
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump({"value": last_obj.model_dump(), "usage": total, "model": model, "check_errors": last_errs}, f, ensure_ascii=False)
    return last_obj
