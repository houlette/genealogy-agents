#!/usr/bin/env python3
"""Run a task brief on a worker agent outside the main Claude session.

    python3 tools/dispatch.py scratch/dispatch/brief-schmidt-1734.md --role retrieve
    python3 tools/dispatch.py brief.md --role retrieve --backend claude-api:haiku
    python3 tools/dispatch.py --status

A brief is a self-contained Markdown file: the exact URL or search, the
procedure, the output filenames in sources/, and the facts to report. The
worker sees the brief and the repository's own instructions (AGENTS.md), and
nothing from the main session.

Backends, tried in the order the role lists them:

- agy:<model>         Antigravity CLI on the owner's Google plan, headless,
                      permission prompts off. Use it only if the owner has
                      allowed that for this repository.
- loop:<model>        A small agent loop in this file, the same for every model,
                      with a spending cap. <model> is
                      any key of tools/models.json (haiku, sonnet, flash, argon,
                      sol, terra, kimi, ...), billed to that model's own key:
                      Anthropic, Gemini or OpenRouter. The worker has a shell
                      and an image viewer and no permission prompts, like agy.
                      Use it to compare models in one harness; it says nothing
                      about agy or Claude Code, which are harnesses of their own.
- opencode:<model>    OpenCode, a third-party open-source harness, headless.
                      <model> is a key of
                      tools/models.json; every model runs through the
                      OpenRouter key. This file enforces the spending cap,
                      because OpenCode has none.
- claude-api:<model>  `claude -p` billed to ANTHROPIC_API_KEY, with a spending cap.
- claude-plan:<model> `claude -p` on the Claude plan.

A backend whose plan quota is used up is skipped until its reset time, and the
next one in the chain is tried. Only a quota error moves on to the next
backend: a worker that ran and failed is reported as failed, because a second
model repeating a failed retrieval hides the failure (AGENTS.md, evidence
rule 7). The worker's report is written beside the brief as
<brief>.report.md, and one line goes to usage-ledger.jsonl.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

ROOT = llm.ROOT
STATE = os.path.join(ROOT, 'scratch', 'dispatch', 'quota.json')
ROLES = {
    'retrieve': ['agy:gemini-3.8-flash-high', 'claude-api:haiku'],
    'screen':   ['agy:gemini-3.8-flash-medium', 'claude-api:haiku'],
    'argue':    ['claude-plan:opus'],
}
PREAMBLE = """You are a worker agent for this genealogy repository, started by tools/dispatch.py. Your agent name is {agent}.
Do only the task in the brief below, then stop. The rules in AGENTS.md apply, above all the evidence rules.
- Drive Chrome only through the bridge in tools/browser_bridge/ with BRIDGE_AGENT={agent} (already set in your environment). Claim each site, and release your sites when you finish. Do not use any browser extension or built-in browser tool.
- Do not edit tree.ged, tasks.md, AGENTS.md or biographies.md, and do not commit. Save files in sources/ under the names the brief gives.
- A retrieval that fails (refusal, timeout, sign-out, "Chrome is not connected") is not a negative result. Stop and report it as failed, with the URL and the failure.
- Never spend money, send messages, or click a purchase or upgrade prompt.
End with a report in exactly this form:
## Report
- Outcome: done | partly done | failed
- Files saved: (paths, or none)
- Facts found: (each with the file and the place in it that shows it)
- Failed retrievals: (source, failure mode; or none)
- Not checked: (what you did not verify)

# Brief

{brief}
"""


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w'), indent=1)


def blocked_until(backend):
    until = load_state().get(backend.split(':')[0])
    return until if until and until > datetime.datetime.now().isoformat() else None


def note_quota(backend, message):
    m = re.search(r'Resets in (?:(\d+)h)?(?:(\d+)m)?', message or '')
    wait = datetime.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0)) if m and (m.group(1) or m.group(2)) else datetime.timedelta(hours=5)
    state = load_state()
    state[backend.split(':')[0]] = (datetime.datetime.now() + wait).isoformat(timespec='minutes')
    save_state(state)


def last_json(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def run_agy(model, prompt, agent, timeout_min, budget):
    cmd = ['agy', '-p', prompt, '--model', model, '--output-format', 'json',
           '--dangerously-skip-permissions', '--print-timeout', f'{timeout_min}m']
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout_min * 60 + 120,
                       env=dict(os.environ, BRIDGE_AGENT=agent))
    r = last_json(p.stdout)
    err = r.get('error') or ('' if r else (p.stderr or p.stdout)[-400:])
    u = r.get('usage') or {}
    return {'text': r.get('response', ''), 'error': err,
            'quota': 'RESOURCE_EXHAUSTED' in (p.stdout + p.stderr) or 'quota' in err.lower(),
            'tokens_in': u.get('input_tokens', 0) + u.get('cache_read_tokens', 0),
            'tokens_cached': u.get('cache_read_tokens', 0),
            'tokens_out': u.get('output_tokens', 0) + u.get('thinking_tokens', 0),
            'turns': r.get('num_turns'), 'cost': 0.0}


def run_claude(model, prompt, agent, timeout_min, budget, api):
    env = dict(os.environ, BRIDGE_AGENT=agent)
    env.pop('ANTHROPIC_API_KEY', None)
    if api:
        k = llm.key('ANTHROPIC_API_KEY')
        if not k:
            return {'text': '', 'error': 'ANTHROPIC_API_KEY is not set', 'quota': True, 'nokey': True, 'cost': 0.0}
        env['ANTHROPIC_API_KEY'] = k
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json',
           '--tools', 'Bash,Read,Write,Edit,Glob,Grep', '--allowedTools', 'Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep',
           '--strict-mcp-config', '--no-session-persistence']
    if api:
        cmd += ['--max-budget-usd', str(budget)]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout_min * 60, env=env)
    r = last_json(p.stdout)
    u = r.get('usage') or {}
    cached = u.get('cache_read_input_tokens', 0)
    err = '' if r and not r.get('is_error') else (r.get('result') or p.stderr or p.stdout)[-400:]
    return {'text': r.get('result', '') if not err else '', 'error': err,
            'quota': bool(re.search(r'usage limit|rate limit|quota|credit balance', err, re.I)),
            'tokens_in': u.get('input_tokens', 0) + cached + u.get('cache_creation_input_tokens', 0),
            'tokens_cached': cached, 'tokens_out': u.get('output_tokens', 0), 'turns': r.get('num_turns'),
            'cost': (r.get('total_cost_usd') or 0.0) if api else 0.0, 'list_cost': r.get('total_cost_usd') or 0.0}


LOOP_TOOLS = [
    {'name': 'bash',
     'description': 'Run a shell command in the repository root and return its exit code and combined output '
                    '(truncated to 20,000 characters). Use it for the bridge client, curl, python3, ls, and for '
                    'reading and writing text files.',
     'parameters': {'type': 'object', 'required': ['command'],
                    'properties': {'command': {'type': 'string'},
                                   'timeout_s': {'type': 'integer', 'description': 'default 120, at most 600'}}}},
    {'name': 'view_image',
     'description': 'Look at an image file (path relative to the repository root or absolute). It is reduced so its '
                    'long side is at most 1,600 pixels; crop first to read small writing.',
     'parameters': {'type': 'object', 'required': ['path'], 'properties': {'path': {'type': 'string'}}}},
]
LOOP_SYSTEM = ('You are a careful worker agent with two tools, bash and view_image. Work step by step with the tools '
               'until the task in the message is done, then answer with the final report as plain text and no tool call. '
               'Read the files the task points to before acting. Keep commands short and their output small.')
LOOP_MAX_TOKENS = 16000


def _run_tool(call, env):
    """Run one tool call. Returns (result dict, image or None), where image is (media type, base64)."""
    args = call.get('args') if isinstance(call.get('args'), dict) else {}
    if '_bad_json' in args:
        return {'output': 'the tool arguments were not valid JSON; send the call again'}, None
    if call.get('name') == 'bash':
        try:
            limit = max(1, min(int(args.get('timeout_s') or 120), 600))
        except (TypeError, ValueError):
            limit = 120
        try:
            p = subprocess.run(['/bin/zsh', '-c', str(args.get('command', ''))], cwd=ROOT, env=env,
                               capture_output=True, text=True, errors='replace', timeout=limit)
            return {'exit_code': p.returncode, 'output': (p.stdout + p.stderr)[-20000:]}, None
        except subprocess.TimeoutExpired:
            return {'exit_code': None, 'output': f'the command was stopped after {limit} seconds'}, None
    if call.get('name') == 'view_image':
        path = os.path.join(ROOT, os.path.expanduser(str(args.get('path', ''))))
        try:
            return {'output': f'the image {args.get("path")} follows'}, llm.encode_image(path, 1600)
        except Exception as e:
            return {'output': f'could not open the image: {type(e).__name__}: {e}'}, None
    return {'output': f'unknown tool {call.get("name")}'}, None


class _GeminiChat:
    """Gemini generateContent with function calling."""

    def __init__(self, spec, prompt):
        self.spec = spec
        self.contents = [{'role': 'user', 'parts': [{'text': prompt}]}]

    def step(self):
        r = llm._post(f'https://generativelanguage.googleapis.com/v1beta/models/{self.spec["id"]}:generateContent',
                      {'x-goog-api-key': llm.key('GEMINI_API_KEY')},
                      {'contents': self.contents, 'tools': [{'functionDeclarations': LOOP_TOOLS}],
                       'systemInstruction': {'parts': [{'text': LOOP_SYSTEM}]},
                       'generationConfig': {'maxOutputTokens': LOOP_MAX_TOKENS}})
        m = r.get('usageMetadata', {})
        usage = {'in': m.get('promptTokenCount', 0),
                 'out': m.get('candidatesTokenCount', 0) + m.get('thoughtsTokenCount', 0),
                 'cached': m.get('cachedContentTokenCount', 0)}
        content = ((r.get('candidates') or [{}])[0]).get('content') or {}
        parts = content.get('parts') or []
        if parts:
            self.contents.append(content)  # unchanged, so the model's thought signatures are kept
        calls = [{'id': p['functionCall'].get('id'), 'name': p['functionCall'].get('name'),
                  'args': p['functionCall'].get('args') or {}} for p in parts if p.get('functionCall')]
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought')).strip()
        return text, calls, usage, None

    def results(self, results):
        replies, images = [], []
        for call, out, image in results:
            reply = {'name': call['name'], 'response': out}
            if call.get('id'):
                reply['id'] = call['id']
            replies.append({'functionResponse': reply})
            if image:
                images.append({'inline_data': {'mime_type': image[0], 'data': image[1]}})
        self.contents.append({'role': 'user', 'parts': replies + images})

    def nudge(self, text):
        self.contents.append({'role': 'user', 'parts': [{'text': text}]})


class _OpenRouterChat:
    """OpenRouter chat completions with tool calling (GPT, Kimi, Qwen and others)."""

    def __init__(self, spec, prompt):
        self.spec = spec
        self.messages = [{'role': 'system', 'content': LOOP_SYSTEM}, {'role': 'user', 'content': prompt}]

    def step(self):
        body = {'model': self.spec['id'], 'messages': self.messages, 'max_tokens': LOOP_MAX_TOKENS,
                'tools': [{'type': 'function', 'function': t} for t in LOOP_TOOLS], 'usage': {'include': True}}
        if self.spec.get('provider_pin'):
            body['provider'] = {'order': [self.spec['provider_pin']], 'allow_fallbacks': False}
        r = llm._post('https://openrouter.ai/api/v1/chat/completions',
                      {'Authorization': f'Bearer {llm.key("OPENROUTER_API_KEY")}'}, body)
        if r.get('error') or not r.get('choices'):
            raise llm.LLMError(f'no output: {json.dumps(r.get("error") or r)[:300]}')
        msg = r['choices'][0]['message']
        self.messages.append(msg)  # unchanged, so any reasoning details the model needs are kept
        calls = []
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {'_bad_json': fn.get('arguments')}
            calls.append({'id': tc.get('id'), 'name': fn.get('name'), 'args': args})
        u = r.get('usage', {})
        usage = {'in': u.get('prompt_tokens', 0), 'out': u.get('completion_tokens', 0),
                 'cached': (u.get('prompt_tokens_details') or {}).get('cached_tokens', 0)}
        text = msg.get('content') if isinstance(msg.get('content'), str) else ''
        return (text or '').strip(), calls, usage, u.get('cost')

    def results(self, results):
        images = []
        for call, out, image in results:
            self.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(out, ensure_ascii=False)})
            if image:
                images.append({'type': 'image_url', 'image_url': {'url': f'data:{image[0]};base64,{image[1]}'}})
        if images:  # a tool message cannot carry an image in this format, so it follows as a user message
            self.messages.append({'role': 'user', 'content': [{'type': 'text', 'text': 'The image you asked to view:'}] + images})

    def nudge(self, text):
        self.messages.append({'role': 'user', 'content': text})


class _AnthropicChat:
    """Anthropic Messages API with tool use, through the official SDK."""

    def __init__(self, spec, prompt):
        import anthropic
        self.anthropic = anthropic
        self.spec = spec
        self.client = anthropic.Anthropic(api_key=llm.key('ANTHROPIC_API_KEY'))
        self.messages = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}]
        self.tools = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']} for t in LOOP_TOOLS]

    def step(self):
        anthropic = self.anthropic
        # One cache breakpoint, moved to the end of the conversation each turn, so
        # every request reads the previous turn's prefix from the cache.
        for m in self.messages:
            if m['role'] == 'user':
                for block in m['content']:
                    block.pop('cache_control', None)
        self.messages[-1]['content'][-1]['cache_control'] = {'type': 'ephemeral'}
        try:
            # No fallback model, as in llm.py: a silent switch would spoil a comparison.
            r = self.client.messages.create(model=self.spec['id'], max_tokens=LOOP_MAX_TOKENS, system=LOOP_SYSTEM,
                                            tools=self.tools, output_config={'effort': 'medium'}, messages=self.messages)
        except anthropic.RateLimitError as e:
            raise llm.LLMError(f'rate limited: {e.message}') from e
        except anthropic.APIStatusError as e:
            raise llm.LLMError(f'HTTP {e.status_code}: {e.message}') from e
        except anthropic.APIConnectionError as e:
            raise llm.LLMError(f'connection error: {e}') from e
        if r.stop_reason == 'refusal':
            raise llm.LLMError(f'refused: {getattr(r.stop_details, "category", None)}')
        if r.content:
            self.messages.append({'role': 'assistant', 'content': r.content})  # unchanged, thinking blocks included
        calls = [{'id': b.id, 'name': b.name, 'args': b.input} for b in r.content if b.type == 'tool_use']
        text = ''.join(b.text for b in r.content if b.type == 'text').strip()
        u = r.usage
        cached = u.cache_read_input_tokens or 0
        usage = {'in': u.input_tokens + cached + (u.cache_creation_input_tokens or 0), 'out': u.output_tokens, 'cached': cached}
        return text, calls, usage, None

    def results(self, results):
        blocks = []
        for call, out, image in results:  # every result goes back in one user message
            content = [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False)}]
            if image:
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': image[0], 'data': image[1]}})
            blocks.append({'type': 'tool_result', 'tool_use_id': call['id'], 'content': content})
        self.messages.append({'role': 'user', 'content': blocks})

    def nudge(self, text):
        self.messages.append({'role': 'user', 'content': [{'type': 'text', 'text': text}]})


LOOP_CHATS = {'gemini': (_GeminiChat, 'GEMINI_API_KEY'), 'openrouter': (_OpenRouterChat, 'OPENROUTER_API_KEY'),
              'anthropic': (_AnthropicChat, 'ANTHROPIC_API_KEY')}


def run_loop(model, prompt, agent, timeout_min, budget):
    """One small agent loop for every model in tools/models.json, billed to that model's own API key.

    The same two tools, system prompt and limits apply to every model, so a difference between two runs
    comes from the model and not from the harness. The loop stops at the dollar budget, the time limit
    or 120 turns, whichever comes first, and reports that as a failed worker. It does not trim the
    conversation, so a long session costs more with every turn.
    """
    spec = llm.MODELS.get(model)
    if not spec:
        return {'text': '', 'error': f'{model} is not a model in tools/models.json', 'quota': False, 'cost': 0.0}
    chat_class, key_name = LOOP_CHATS[spec['provider']]
    if not llm.key(key_name):
        return {'text': '', 'error': f'{key_name} is not set', 'quota': True, 'nokey': True, 'cost': 0.0}
    env = dict(os.environ, BRIDGE_AGENT=agent)
    chat = chat_class(spec, prompt)
    total = {'in': 0, 'out': 0, 'cached': 0}
    state = {'cost': 0.0, 'turns': 0}
    started, nudged = time.time(), False

    def result(text, error):
        return {'text': text, 'error': error, 'quota': False, 'tokens_in': total['in'], 'tokens_cached': total['cached'],
                'tokens_out': total['out'], 'turns': state['turns'], 'cost': round(state['cost'], 6),
                'list_cost': llm.cost_of(spec, total)}

    while True:
        if state['cost'] >= budget:
            return result('', f'stopped at the ${budget} budget after {state["turns"]} turns')
        if time.time() - started > timeout_min * 60:
            return result('', f'timed out after {timeout_min} minutes and {state["turns"]} turns')
        if state['turns'] >= 120:
            return result('', 'stopped after 120 turns')
        try:
            text, calls, usage, billed = chat.step()
        except llm.LLMError as e:
            return result('', f'API call failed on turn {state["turns"] + 1}: {e}')
        state['turns'] += 1
        for k in total:
            total[k] += usage[k]
        state['cost'] += billed if billed is not None else llm.cost_of(spec, usage)
        if calls:
            chat.results([(c,) + _run_tool(c, env) for c in calls])
            continue
        if text:
            return result(text, '')
        if nudged:
            return result('', f'no output on turn {state["turns"]}')
        nudged = True  # one empty turn is allowed; ask once for the report
        chat.nudge('Continue. If the task is done, give the final report now.')


def run_opencode(model, prompt, agent, timeout_min, budget):
    """OpenCode (a third-party open-source harness), headless.

    Every model goes through the OpenRouter key, so the cost lands on the OpenRouter balance. `model` is a key
    of tools/models.json or a full OpenCode id such as openrouter/openai/gpt-6.1-terra. OpenCode has no
    spending cap of its own, so this reads its event stream and stops the process at the budget or the time
    limit. `--auto` approves every tool call that the project's OpenCode config does not deny.
    """
    if '/' in model:
        oc_model = model
    else:
        spec = llm.MODELS.get(model) or {}
        route = spec.get('id') if spec.get('provider') == 'openrouter' else spec.get('openrouter')
        if not route:
            return {'text': '', 'error': f'{model} has no OpenRouter id in tools/models.json', 'quota': False, 'cost': 0.0}
        oc_model = 'openrouter/' + route
    k = llm.key('OPENROUTER_API_KEY')
    if not k:
        return {'text': '', 'error': 'OPENROUTER_API_KEY is not set', 'quota': True, 'nokey': True, 'cost': 0.0}
    env = dict(os.environ, BRIDGE_AGENT=agent, OPENROUTER_API_KEY=k)
    # --standalone: a private server for this run, so no background service is left behind.
    cmd = ['opencode', 'run', '--standalone', '--auto', '--archived', '--format', 'json', '-m', oc_model, prompt]
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')
    stopped = {}

    def stop(reason):
        stopped.setdefault('why', reason)
        p.kill()

    timer = threading.Timer(timeout_min * 60, stop, [f'timed out after {timeout_min} minutes'])
    timer.start()
    cost, steps, texts, errors = 0.0, 0, [], []
    tokens = {'in': 0, 'out': 0, 'cached': 0}
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            part = ev.get('part') or {}
            if ev.get('type') == 'step_start':
                texts = []  # keep only the text of the last step: the final report
            elif ev.get('type') == 'text':
                texts.append(part.get('text', ''))
            elif ev.get('type') == 'error':
                errors.append(json.dumps(ev.get('error') or ev)[:300])
            elif ev.get('type') == 'step_finish':
                steps += 1
                cost += part.get('cost') or 0.0
                t = part.get('tokens') or {}
                cache = t.get('cache') or {}
                tokens['in'] += (t.get('input') or 0) + (cache.get('read') or 0) + (cache.get('write') or 0)
                tokens['cached'] += cache.get('read') or 0
                tokens['out'] += (t.get('output') or 0) + (t.get('reasoning') or 0)
                if cost >= budget:
                    stop(f'stopped at the ${budget} budget after {steps} steps')
        p.wait()
    finally:
        timer.cancel()
    err = stopped.get('why') or '; '.join(errors) or ('' if p.returncode == 0 else (p.stderr.read() or '')[-400:] or f'exit code {p.returncode}')
    text = '\n'.join(x for x in texts if x).strip()
    if not err and not text:
        err = 'OpenCode ended without a final text'
    return {'text': '' if err else text, 'error': err, 'quota': False, 'tokens_in': tokens['in'],
            'tokens_cached': tokens['cached'], 'tokens_out': tokens['out'], 'turns': steps,
            'cost': round(cost, 6), 'list_cost': round(cost, 6)}


def dispatch(brief_path, role, backends=None, agent=None, timeout_min=45, budget=2.0):
    brief = open(brief_path).read()
    if role == 'retrieve':
        import prior_work
        problems = prior_work.check_brief(brief)
        if problems:
            for p in problems:
                print('dispatch: not sent: ' + p)
            return 3
    task = os.path.splitext(os.path.basename(brief_path))[0]
    for backend in backends or ROLES[role]:
        kind, _, model = backend.partition(':')
        until = blocked_until(backend)
        if until:
            print(f'{backend}: quota used up until {until}, skipping')
            continue
        name = agent or {'agy': 'gemini', 'loop': f'loop-{model}', 'opencode': f'opencode-{model.split("/")[-1]}', 'claude-api': 'claude-sub-api',
                         'claude-plan': 'claude-sub-plan'}[kind]
        prompt = PREAMBLE.format(agent=name, brief=brief)
        print(f'{backend}: running {task} as {name} ...', flush=True)
        started = time.time()
        try:
            r = run_agy(model, prompt, name, timeout_min, budget) if kind == 'agy' else \
                run_loop(model, prompt, name, timeout_min, budget) if kind == 'loop' else \
                run_opencode(model, prompt, name, timeout_min, budget) if kind == 'opencode' else \
                run_claude(model, prompt, name, timeout_min, budget, api=(kind == 'claude-api'))
        except subprocess.TimeoutExpired:
            r = {'text': '', 'error': f'timed out after {timeout_min} minutes', 'quota': False, 'cost': 0.0}
        outcome = 'ok' if not r['error'] else ('quota' if r['quota'] else 'error: ' + r['error'][:200])
        llm.log(dict(kind='worker', task=f'{role}:{task}', backend=kind, model=model, outcome=outcome,
                     tokens_in=r.get('tokens_in', 0), tokens_out=r.get('tokens_out', 0),
                     tokens_cached=r.get('tokens_cached', 0), turns=r.get('turns'), cost=r.get('cost', 0.0),
                     list_cost=r.get('list_cost', 0.0), seconds=round(time.time() - started)))
        if r['error'] and r['quota']:
            if not r.get('nokey'):
                note_quota(backend, r['error'])
            print(f'{backend}: not available ({r["error"][:120]}); trying the next backend')
            continue
        report = os.path.splitext(brief_path)[0] + '.report.md'
        head = f'Worker report for `{os.path.basename(brief_path)}`: {backend}, agent {name}, {datetime.date.today()}, {r.get("turns")} turns, {r.get("tokens_in", 0):,} tokens in.\n\n'
        open(report, 'w').write(head + (r['text'] or f'## Report\n- Outcome: failed\n- Failed retrievals: the worker itself failed: {r["error"]}\n'))
        print(f'{backend}: {outcome}; report at {report}')
        return 0 if not r['error'] else 1
    print('no backend available: every one in the chain is out of quota or has no key. This is not a result; run the brief again later.')
    return 2


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('brief', nargs='?')
    ap.add_argument('--role', choices=sorted(ROLES), default='retrieve')
    ap.add_argument('--backend', action='append', help='override the chain, e.g. claude-api:haiku (repeatable)')
    ap.add_argument('--agent', help='bridge agent name, when two workers run at once on different sites')
    ap.add_argument('--timeout', type=int, default=45, help='minutes')
    ap.add_argument('--budget', type=float, default=2.0, help='dollar cap for one claude-api run')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.status or not a.brief:
        for role, chain in ROLES.items():
            print(role + ':', ', '.join(f'{b} (out of quota until {blocked_until(b)})' if blocked_until(b) else b for b in chain))
        print('ANTHROPIC_API_KEY:', 'set' if llm.key('ANTHROPIC_API_KEY') else 'not set')
        sys.exit(0)
    sys.exit(dispatch(a.brief, a.role, a.backend, a.agent, a.timeout, a.budget))
