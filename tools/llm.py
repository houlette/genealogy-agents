#!/usr/bin/env python3
"""One-shot calls to the models in tools/models.json, with a usage ledger.

    from llm import call
    r = call('opus', system='...', text='...', images=['sources/x.jpg'], task='bakeoff:e2')
    r['text'], r['usage'], r['cost']

Each call is a single request with no agent loop, so it costs a few thousand
tokens. Every call, including a failed one, appends a line to
usage-ledger.jsonl in the repository root.

Keys are read from the environment or from the keyfile named in project.json
(default ~/.config/genealogy-agents.env; KEY=value lines; kept outside the repository so they are never committed):
ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY. A gemini model falls
back to OpenRouter when GEMINI_API_KEY is not set.

A project whose project.json sets "gemini_route": "plan" sends each gemini
call first through the owner's Google AI plan, with the Antigravity CLI (agy)
headless in an empty temporary folder, so the worker sees only the prompt and
the images; its model name is the "agy" field in models.json. Those calls cost
nothing extra and are logged with backend "agy" and cost 0 (list_cost is what
the API would have charged). When the plan's quota is used up the call goes to
the API key instead, and agy is skipped until the reset time, kept in
scratch/dispatch/quota.json as tools/dispatch.py does. Any other agy failure
is raised as an error, never retried on the API silently. The environment
variable LLM_GEMINI_ROUTE (plan or api) overrides the setting for one run.

    python3 tools/llm.py --check    # which keys are set; test each model id

Prompts sent through this module go to outside services. Do not put the owner's
name, email or other personal details in them (historical record text is the
point and is fine).
"""

import base64
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, 'usage-ledger.jsonl')
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import project_config as cfg  # noqa: E402

KEYFILE = cfg.KEYFILE
STATE = os.path.join(ROOT, 'scratch', 'dispatch', 'quota.json')  # shared with tools/dispatch.py
AGY_TIMEOUT_MIN = 15
MODELS = {k: v for k, v in json.load(open(os.path.join(ROOT, 'tools', 'models.json'))).items()
          if not k.startswith('_')}


class LLMError(RuntimeError):
    """The call failed or was refused. Never treat this as a reading."""


def key(name):
    if os.environ.get(name):
        return os.environ[name]
    if os.path.exists(KEYFILE):
        for line in open(KEYFILE):
            k, _, v = line.strip().partition('=')
            if k.strip() == name and v.strip():
                return v.strip().strip('"\'')
    return None


def encode_image(path, max_side=2000):
    """Return (media_type, base64) of the image, downscaled so its long side is at most max_side."""
    from PIL import Image
    img = Image.open(path)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=90)
    return 'image/jpeg', base64.b64encode(buf.getvalue()).decode('ascii')


def cost_of(spec, usage):
    fresh = max(usage['in'] - usage['cached'], 0)
    return round((fresh * spec['in'] + usage['cached'] * spec['cached'] + usage['out'] * spec['out']) / 1e6, 6)


def log(row):
    row = dict(ts=datetime.datetime.now().isoformat(timespec='seconds'), **row)
    with open(LEDGER, 'a') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def _anthropic(spec, system, text, images, effort, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=key('ANTHROPIC_API_KEY'))
    content = []
    for media, data in images:
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': data}})
    content.append({'type': 'text', 'text': text})
    try:
        # No server-side fallback here on purpose: a silent switch to another
        # model would spoil a comparison. A refusal is raised as an error.
        with client.messages.stream(model=spec['id'], max_tokens=max_tokens, system=system,
                                    output_config={'effort': effort},
                                    messages=[{'role': 'user', 'content': content}]) as stream:
            r = stream.get_final_message()
    except anthropic.RateLimitError as e:
        raise LLMError(f'rate limited: {e.message}') from e
    except anthropic.APIStatusError as e:
        raise LLMError(f'HTTP {e.status_code}: {e.message}') from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f'connection error: {e}') from e
    if r.stop_reason == 'refusal':
        raise LLMError(f'refused: {getattr(r.stop_details, "category", None)}')
    out = ''.join(b.text for b in r.content if b.type == 'text')
    u = r.usage
    cached = u.cache_read_input_tokens or 0
    usage = {'in': u.input_tokens + cached + (u.cache_creation_input_tokens or 0),
             'out': u.output_tokens, 'cached': cached}
    return out, usage, r.stop_reason


def _post(url, headers, body, timeout=600):
    import requests
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            if attempt == 2:
                raise LLMError(f'connection error: {e}') from e
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise LLMError(f'HTTP {resp.status_code}: {resp.text[:400]}')
        return resp.json()


def _gemini(spec, system, text, images, effort, max_tokens):
    parts = [{'inline_data': {'mime_type': m, 'data': d}} for m, d in images] + [{'text': text}]
    body = {'contents': [{'role': 'user', 'parts': parts}],
            'systemInstruction': {'parts': [{'text': system}]},
            'generationConfig': {'maxOutputTokens': max_tokens}}
    if spec.get('thinking'):  # Neon at its default or with thinkingBudget thinks to maxOutputTokens and returns no text; thinkingLevel works (20 Nov 2026)
        body['generationConfig']['thinkingConfig'] = spec['thinking']
    r = _post(f'https://generativelanguage.googleapis.com/v1beta/models/{spec["id"]}:generateContent',
              {'x-goog-api-key': key('GEMINI_API_KEY')}, body)
    cands = r.get('candidates') or []
    if not cands or not cands[0].get('content', {}).get('parts'):
        raise LLMError(f'no output: {json.dumps(r.get("promptFeedback") or cands)[:300]}')
    out = ''.join(p.get('text', '') for p in cands[0]['content']['parts'] if not p.get('thought'))
    m = r.get('usageMetadata', {})
    usage = {'in': m.get('promptTokenCount', 0),
             'out': m.get('candidatesTokenCount', 0) + m.get('thoughtsTokenCount', 0),
             'cached': m.get('cachedContentTokenCount', 0)}
    return out, usage, cands[0].get('finishReason')


def _openrouter(spec, system, text, images, effort, max_tokens, model_id=None):
    content = [{'type': 'image_url', 'image_url': {'url': f'data:{m};base64,{d}'}} for m, d in images]
    content.append({'type': 'text', 'text': text})
    body = {'model': model_id or spec['id'], 'max_tokens': max_tokens,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
            'reasoning': {'effort': 'high' if effort in ('xhigh', 'max') else effort},
            'usage': {'include': True}}
    if spec.get('provider_pin'):
        body['provider'] = {'order': [spec['provider_pin']], 'allow_fallbacks': False}
    r = _post('https://openrouter.ai/api/v1/chat/completions',
              {'Authorization': f'Bearer {key("OPENROUTER_API_KEY")}'}, body)
    if r.get('error') or not r.get('choices'):
        raise LLMError(f'no output: {json.dumps(r.get("error") or r)[:300]}')
    out = r['choices'][0]['message'].get('content') or ''
    u = r.get('usage', {})
    usage = {'in': u.get('prompt_tokens', 0), 'out': u.get('completion_tokens', 0),
             'cached': (u.get('prompt_tokens_details') or {}).get('cached_tokens', 0)}
    if u.get('cost') is not None:
        usage['billed'] = u['cost']
    served = r.get('provider')
    return out, usage, f'{r["choices"][0].get("finish_reason")} via {served}'


class PlanQuota(LLMError):
    """The Google plan's quota is used up; the call may go to the API instead."""


def _plan_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def plan_blocked():
    """The time agy's quota resets, if it is used up now; else None."""
    until = _plan_state().get('agy')
    return until if until and until > datetime.datetime.now().isoformat() else None


def note_plan_quota(message):
    import re
    m = re.search(r'Resets in (?:(\d+)h)?(?:(\d+)m)?', message or '')
    wait = (datetime.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))
            if m and (m.group(1) or m.group(2)) else datetime.timedelta(hours=5))
    state = _plan_state()
    state['agy'] = (datetime.datetime.now() + wait).isoformat(timespec='minutes')
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w'), indent=1)


def use_plan(spec):
    """True when this gemini model should go through the owner's Google plan first."""
    route_setting = (os.environ.get('LLM_GEMINI_ROUTE') or getattr(cfg, 'GEMINI_ROUTE', 'api') or 'api').lower()
    return (route_setting == 'plan' and spec.get('provider') == 'gemini' and bool(spec.get('agy'))
            and bool(shutil.which('agy')) and not plan_blocked())


def _last_json(text):
    for line in reversed((text or '').strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def _agy(spec, system, text, image_paths, max_side):
    """One headless agy turn in an empty folder holding only the images."""
    with tempfile.TemporaryDirectory(prefix='llm-agy-') as work:
        names = []
        for i, path in enumerate(image_paths, 1):
            _, data = encode_image(path, max_side)
            names.append(f'image{i}.jpg')
            with open(os.path.join(work, names[-1]), 'wb') as f:
                f.write(base64.b64decode(data))
        look = (f'Open these image files in this folder with your file viewer, in this order: {", ".join(names)}. '
                if names else '')
        prompt = (f'{system}\n\n{look}Do not run shell commands, and do not open, search or write any other file. '
                  f'Answer in your reply only.\n\n{text}')
        cmd = ['agy', '-p', prompt, '--model', spec['agy'], '--output-format', 'json', '--sandbox',
               '--print-timeout', f'{AGY_TIMEOUT_MIN}m']
        try:
            p = subprocess.run(cmd, cwd=work, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=AGY_TIMEOUT_MIN * 60 + 120)
        except subprocess.TimeoutExpired:
            raise LLMError(f'agy timed out after {AGY_TIMEOUT_MIN} minutes')
    r = _last_json(p.stdout)
    err = r.get('error') or ''
    if not r:
        err = (p.stderr or p.stdout or f'agy exited {p.returncode} with no output')[-400:]
    if 'RESOURCE_EXHAUSTED' in (p.stdout + p.stderr) or 'quota' in str(err).lower():
        raise PlanQuota(f'agy quota: {str(err)[:300]}')
    if err or r.get('status') not in (None, 'SUCCESS') or not r.get('response'):
        raise LLMError(f'agy: {str(err or r.get("status") or "empty reply")[:300]}')
    u = r.get('usage') or {}
    usage = {'in': u.get('input_tokens', 0), 'out': u.get('output_tokens', 0), 'cached': u.get('cache_read_tokens', 0)}
    return r['response'], usage, r.get('status')


def route(name):
    """Return (provider, spec) for a model key, falling back to OpenRouter for gemini without a key."""
    spec = MODELS[name]
    provider = spec['provider']
    if provider == 'gemini' and not key('GEMINI_API_KEY') and spec.get('openrouter') and key('OPENROUTER_API_KEY'):
        return 'openrouter', dict(spec, id=spec['openrouter'])
    return provider, spec


def call(name, system, text, images=(), effort='high', max_tokens=16000, task='', max_side=2000):
    """Run one request. Returns {'text', 'usage', 'cost', 'model', 'stop'}; raises LLMError on failure."""
    if use_plan(MODELS[name]):
        spec = MODELS[name]
        started = time.time()
        try:
            out, usage, stop = _agy(spec, system, text, images, max_side)
        except PlanQuota as e:
            note_plan_quota(str(e))
            log(dict(kind='call', task=task, backend='agy', model=spec['agy'], outcome=f'quota: {e}'[:300],
                     seconds=round(time.time() - started, 1)))
        except LLMError as e:
            log(dict(kind='call', task=task, backend='agy', model=spec['agy'], outcome=f'error: {e}'[:300],
                     seconds=round(time.time() - started, 1)))
            raise
        else:
            log(dict(kind='call', task=task, backend='agy', model=spec['agy'], outcome='ok', stop=stop,
                     tokens_in=usage['in'], tokens_out=usage['out'], tokens_cached=usage['cached'],
                     cost=0.0, list_cost=cost_of(spec, usage), seconds=round(time.time() - started, 1)))
            return {'text': out, 'usage': usage, 'cost': 0.0, 'model': spec['agy'], 'stop': stop}
    provider, spec = route(name)
    needed = {'anthropic': 'ANTHROPIC_API_KEY', 'gemini': 'GEMINI_API_KEY', 'openrouter': 'OPENROUTER_API_KEY'}[provider]
    if not key(needed):
        raise LLMError(f'{needed} is not set (environment or {KEYFILE})')
    # A model whose reasoning counts against the output limit can set a higher
    # floor in models.json (carbon stopped at MAX_TOKENS with no text at 16,000).
    max_tokens = max(max_tokens, spec.get('max_tokens', 0))
    imgs = [encode_image(p, max_side) for p in images]
    fn ={'anthropic': _anthropic, 'gemini': _gemini, 'openrouter': _openrouter}[provider]
    started = time.time()
    try:
        out, usage, stop = fn(spec, system, text, imgs, effort, max_tokens)
    except LLMError as e:
        log(dict(kind='call', task=task, backend=provider, model=spec['id'], outcome=f'error: {e}'[:300],
                 seconds=round(time.time() - started, 1)))
        raise
    cost = usage.get('billed', cost_of(spec, usage))
    log(dict(kind='call', task=task, backend=provider, model=spec['id'], outcome='ok', stop=stop,
             tokens_in=usage['in'], tokens_out=usage['out'], tokens_cached=usage['cached'],
             cost=round(cost, 6), list_cost=cost_of(spec, usage), seconds=round(time.time() - started, 1)))
    return {'text': out, 'usage': usage, 'cost': cost, 'model': spec['id'], 'stop': stop}


def parse_json(text):
    """Extract the first JSON object from a model reply; raise LLMError if there is none."""
    start = text.find('{')
    if start < 0:
        raise LLMError(f'no JSON in reply: {text[:200]!r}')
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as e:
        raise LLMError(f'bad JSON in reply ({e}): {text[start:start + 200]!r}') from e
    return obj


def check():
    for k in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY'):
        print(f'{k}: {"set" if key(k) else "NOT SET"}')
    for name in MODELS:
        provider, spec = route(name)
        try:
            r = call(name, 'Reply with the single word OK.', 'Say OK.', effort='low', max_tokens=2000, task='check')
            print(f'{name:8} {provider:10} {spec["id"]:32} ok  ({r["usage"]["in"]} in, {r["usage"]["out"]} out, ${r["cost"]:.5f})')
        except LLMError as e:
            print(f'{name:8} {provider:10} {spec["id"]:32} FAILED: {str(e)[:120]}')


if __name__ == '__main__':
    if '--check' in sys.argv:
        check()
    else:
        print(__doc__)
