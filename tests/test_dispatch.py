import base64
import datetime
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import llm  # noqa: E402
import dispatch  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

KEYS = ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.state = os.path.join(t, 'scratch', 'dispatch', 'quota.json')
        self.ledger = os.path.join(t, 'ledger.jsonl')
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        for p in (mock.patch.object(dispatch, 'STATE', self.state), mock.patch.object(dispatch, 'ROOT', t),
                  mock.patch.object(llm, 'LEDGER', self.ledger),
                  mock.patch.object(llm, 'KEYFILE', os.path.join(t, 'nokeys.env')),
                  mock.patch.dict(os.environ, env, clear=True), mock.patch('time.sleep')):
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        if not os.path.exists(self.ledger):
            return []
        return [json.loads(l) for l in open(self.ledger) if l.strip()]

    def brief(self, text='Fetch the thing.', name='brief-x.md'):
        p = os.path.join(self.tmp.name, name)
        open(p, 'w').write(text)
        return p


def completed(stdout='', stderr='', code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class QuotaTests(Base):
    def test_load_state_missing_or_corrupt(self):
        self.assertEqual(dispatch.load_state(), {})
        os.makedirs(os.path.dirname(self.state))
        open(self.state, 'w').write('{not json')
        self.assertEqual(dispatch.load_state(), {})

    def test_note_quota_parses_hours_and_minutes(self):
        before = datetime.datetime.now()
        dispatch.note_quota('claude-plan:opus', 'You have hit your usage limit. Resets in 2h30m.')
        until = datetime.datetime.fromisoformat(dispatch.load_state()['claude-plan'])
        delta = until - before
        self.assertAlmostEqual(delta.total_seconds(), 150 * 60, delta=130)

    def test_note_quota_minutes_only_and_hours_only(self):
        now = datetime.datetime.now()
        dispatch.note_quota('agy:x', 'Resets in 45m')
        self.assertAlmostEqual((datetime.datetime.fromisoformat(dispatch.load_state()['agy']) - now).total_seconds(), 2700, delta=130)
        dispatch.note_quota('agy:x', 'Resets in 3h')
        self.assertAlmostEqual((datetime.datetime.fromisoformat(dispatch.load_state()['agy']) - now).total_seconds(), 10800, delta=130)

    def test_note_quota_default_five_hours(self):
        now = datetime.datetime.now()
        for msg in ('quota exceeded', None, 'Resets in soon', ''):
            dispatch.note_quota('claude-api:haiku', msg)
            until = datetime.datetime.fromisoformat(dispatch.load_state()['claude-api'])
            self.assertAlmostEqual((until - now).total_seconds(), 5 * 3600, delta=130, msg=msg)

    def test_note_quota_keeps_other_backends(self):
        dispatch.note_quota('agy:a', 'Resets in 1h')
        dispatch.note_quota('loop:b', 'Resets in 1h')
        self.assertEqual(set(dispatch.load_state()), {'agy', 'loop'})

    def test_blocked_until(self):
        self.assertIsNone(dispatch.blocked_until('agy:x'))
        future = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(timespec='minutes')
        past = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat(timespec='minutes')
        dispatch.save_state({'agy': future, 'loop': past})
        self.assertEqual(dispatch.blocked_until('agy:gemini-3.8-flash-high'), future)  # keyed on the part before ':'
        self.assertEqual(dispatch.blocked_until('agy:other-model'), future)
        self.assertIsNone(dispatch.blocked_until('loop:haiku'))  # reset time passed

    def test_save_state_creates_directory(self):
        dispatch.save_state({'a': 'b'})
        self.assertEqual(json.load(open(self.state)), {'a': 'b'})


class LastJsonTests(unittest.TestCase):
    def test_takes_last_valid_json_line(self):
        self.assertEqual(dispatch.last_json('noise\n{"a": 1}\n{"b": 2}\ntrailing\n'), {'b': 2})

    def test_skips_invalid_braces(self):
        self.assertEqual(dispatch.last_json('{"a": 1}\n{broken\n'), {'a': 1})

    def test_none(self):
        self.assertEqual(dispatch.last_json('nothing'), {})
        self.assertEqual(dispatch.last_json(''), {})


class RolesTests(unittest.TestCase):
    def test_roles_chain_shape(self):
        self.assertEqual(set(dispatch.ROLES), {'retrieve', 'screen', 'argue'})
        for chain in dispatch.ROLES.values():
            for b in chain:
                kind, sep, model = b.partition(':')
                self.assertTrue(sep and model, b)
                self.assertIn(kind, ('agy', 'loop', 'opencode', 'claude-api', 'claude-plan'))
        self.assertEqual(dispatch.ROLES['argue'], ['claude-plan:opus'])
        self.assertEqual(dispatch.ROLES['retrieve'][1], 'claude-api:haiku')

    def test_preamble_formats(self):
        s = dispatch.PREAMBLE.format(agent='bob', brief='THE BRIEF')
        self.assertIn('Your agent name is bob.', s)
        self.assertIn('BRIDGE_AGENT=bob', s)
        self.assertTrue(s.rstrip().endswith('THE BRIEF'))


class RunAgyTests(Base):
    def test_success(self):
        out = json.dumps({'response': 'done', 'usage': {'input_tokens': 10, 'cache_read_tokens': 5, 'output_tokens': 3,
                                                          'thinking_tokens': 2}, 'num_turns': 4})
        with mock.patch('subprocess.run', return_value=completed(out)) as run:
            r = dispatch.run_agy('gemini-x', 'PROMPT', 'gem', 10, 1.0)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ['agy', '-p', 'PROMPT'])
        self.assertIn('--dangerously-skip-permissions', cmd)
        self.assertEqual(cmd[cmd.index('--print-timeout') + 1], '10m')
        self.assertEqual(run.call_args.kwargs['env']['BRIDGE_AGENT'], 'gem')
        self.assertEqual(run.call_args.kwargs['timeout'], 720)
        self.assertEqual((r['text'], r['error'], r['quota'], r['tokens_in'], r['tokens_cached'], r['tokens_out'], r['turns'], r['cost']),
                         ('done', '', False, 15, 5, 5, 4, 0.0))

    def test_quota_by_resource_exhausted(self):
        with mock.patch('subprocess.run', return_value=completed('', 'RESOURCE_EXHAUSTED: too many', 1)):
            r = dispatch.run_agy('m', 'p', 'a', 5, 1)
        self.assertTrue(r['quota'])
        self.assertIn('RESOURCE_EXHAUSTED', r['error'])

    def test_quota_by_error_text(self):
        with mock.patch('subprocess.run', return_value=completed(json.dumps({'error': 'Daily Quota reached'}))):
            r = dispatch.run_agy('m', 'p', 'a', 5, 1)
        self.assertTrue(r['quota'])

    def test_plain_failure_is_not_quota(self):
        with mock.patch('subprocess.run', return_value=completed('', 'segfault', 1)):
            r = dispatch.run_agy('m', 'p', 'a', 5, 1)
        self.assertFalse(r['quota'])
        self.assertEqual(r['error'], 'segfault')


class RunClaudeTests(Base):
    def ok(self, **kw):
        d = {'result': 'report', 'is_error': False, 'num_turns': 3, 'total_cost_usd': 0.42,
             'usage': {'input_tokens': 10, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20, 'output_tokens': 7}}
        d.update(kw)
        return json.dumps(d)

    def test_api_without_key(self):
        with mock.patch('subprocess.run') as run:
            r = dispatch.run_claude('haiku', 'p', 'a', 5, 1.0, api=True)
        run.assert_not_called()
        self.assertTrue(r['quota'] and r['nokey'])
        self.assertIn('ANTHROPIC_API_KEY is not set', r['error'])

    def test_api_success_passes_key_and_budget(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), \
                mock.patch('subprocess.run', return_value=completed(self.ok())) as run:
            r = dispatch.run_claude('haiku', 'p', 'a', 5, 1.5, api=True)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--max-budget-usd') + 1], '1.5')
        self.assertEqual(run.call_args.kwargs['env']['ANTHROPIC_API_KEY'], 'k')
        self.assertEqual(run.call_args.kwargs['env']['BRIDGE_AGENT'], 'a')
        self.assertEqual((r['text'], r['error'], r['quota'], r['turns']), ('report', '', False, 3))
        self.assertEqual((r['tokens_in'], r['tokens_cached'], r['tokens_out']), (130, 100, 7))
        self.assertEqual((r['cost'], r['list_cost']), (0.42, 0.42))

    def test_plan_run_drops_api_key_and_reports_zero_cost(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), \
                mock.patch('subprocess.run', return_value=completed(self.ok())) as run:
            r = dispatch.run_claude('opus', 'p', 'a', 5, 1.5, api=False)
        self.assertNotIn('ANTHROPIC_API_KEY', run.call_args.kwargs['env'])
        self.assertNotIn('--max-budget-usd', run.call_args.args[0])
        self.assertEqual(r['cost'], 0.0)
        self.assertEqual(r['list_cost'], 0.42)

    def test_error_quota_detection(self):
        for msg in ('Claude usage limit reached', 'rate limit', 'Your credit balance is too low', 'quota'):
            out = self.ok(is_error=True, result=msg)
            with mock.patch('subprocess.run', return_value=completed(out)):
                r = dispatch.run_claude('opus', 'p', 'a', 5, 1, api=False)
            self.assertTrue(r['quota'], msg)
            self.assertEqual(r['text'], '')

    def test_error_not_quota(self):
        with mock.patch('subprocess.run', return_value=completed(self.ok(is_error=True, result='tool crashed'))):
            r = dispatch.run_claude('opus', 'p', 'a', 5, 1, api=False)
        self.assertFalse(r['quota'])
        self.assertEqual(r['error'], 'tool crashed')

    def test_no_json_output_uses_stderr(self):
        with mock.patch('subprocess.run', return_value=completed('', 'boom', 1)):
            r = dispatch.run_claude('opus', 'p', 'a', 5, 1, api=False)
        self.assertEqual(r['error'], 'boom')
        self.assertEqual(r['text'], '')


class RunToolTests(Base):
    def test_bad_json_arguments(self):
        out, img = dispatch._run_tool({'name': 'bash', 'args': {'_bad_json': '{x'}}, {})
        self.assertIn('not valid JSON', out['output'])
        self.assertIsNone(img)

    def test_bash_runs_and_truncates(self):
        big = 'x' * 30000
        with mock.patch('subprocess.run', return_value=completed(big, 'ERR', 3)) as run:
            out, img = dispatch._run_tool({'name': 'bash', 'args': {'command': 'ls', 'timeout_s': 9999}}, {'E': '1'})
        self.assertEqual(out['exit_code'], 3)
        self.assertEqual(len(out['output']), 20000)
        self.assertTrue(out['output'].endswith('ERR'))  # the tail is kept
        self.assertEqual(run.call_args.args[0], ['/bin/zsh', '-c', 'ls'])
        self.assertEqual(run.call_args.kwargs['timeout'], 600)  # clamped
        self.assertEqual(run.call_args.kwargs['cwd'], self.tmp.name)

    def test_bash_timeout_clamp_low_and_bad_values(self):
        with mock.patch('subprocess.run', return_value=completed('')) as run:
            dispatch._run_tool({'name': 'bash', 'args': {'command': 'x', 'timeout_s': -5}}, {})
            self.assertEqual(run.call_args.kwargs['timeout'], 1)
            dispatch._run_tool({'name': 'bash', 'args': {'command': 'x', 'timeout_s': 'abc'}}, {})
            self.assertEqual(run.call_args.kwargs['timeout'], 120)
            dispatch._run_tool({'name': 'bash', 'args': {'command': 'x'}}, {})
            self.assertEqual(run.call_args.kwargs['timeout'], 120)

    def test_bash_timeout_reported(self):
        with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('x', 3)):
            out, _ = dispatch._run_tool({'name': 'bash', 'args': {'command': 'sleep 9', 'timeout_s': 3}}, {})
        self.assertIsNone(out['exit_code'])
        self.assertIn('stopped after 3 seconds', out['output'])

    def test_non_dict_args_treated_as_empty(self):
        with mock.patch('subprocess.run', return_value=completed('')) as run:
            dispatch._run_tool({'name': 'bash', 'args': 'oops'}, {})
        self.assertEqual(run.call_args.args[0][-1], '')

    def test_unknown_tool(self):
        out, img = dispatch._run_tool({'name': 'rm', 'args': {}}, {})
        self.assertEqual(out['output'], 'unknown tool rm')

    @unittest.skipIf(Image is None, 'Pillow missing')
    def test_view_image_downscaled_to_1600(self):
        Image.new('RGB', (3200, 800), 'white').save(os.path.join(self.tmp.name, 'big.png'))
        out, img = dispatch._run_tool({'name': 'view_image', 'args': {'path': 'big.png'}}, {})
        self.assertIn('big.png', out['output'])
        self.assertEqual(img[0], 'image/jpeg')
        im = Image.open(io.BytesIO(base64.b64decode(img[1])))
        self.assertEqual(im.size, (1600, 400))

    def test_view_image_missing_file(self):
        out, img = dispatch._run_tool({'name': 'view_image', 'args': {'path': 'nope.png'}}, {})
        self.assertIsNone(img)
        self.assertIn('could not open the image', out['output'])


class GeminiChatTests(Base):
    def test_step_parses_text_calls_usage(self):
        reply = {'candidates': [{'content': {'role': 'model', 'parts': [
            {'text': 'thinking', 'thought': True}, {'text': ' visible '},
            {'functionCall': {'id': 'c1', 'name': 'bash', 'args': {'command': 'ls'}}}]}}],
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 4,
                              'cachedContentTokenCount': 2}}
        chat = dispatch._GeminiChat(llm.MODELS['argon'], 'PROMPT')
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'g'}), mock.patch.object(llm, '_post', return_value=reply) as post:
            text, calls, usage, billed = chat.step()
        url, headers, body = post.call_args.args
        self.assertIn('gemini-4-argon:generateContent', url)
        self.assertEqual(headers, {'x-goog-api-key': 'g'})
        self.assertEqual(body['contents'][0]['parts'][0]['text'], 'PROMPT')
        self.assertEqual(body['tools'][0]['functionDeclarations'], dispatch.LOOP_TOOLS)
        self.assertEqual(text, 'visible')
        self.assertEqual(calls, [{'id': 'c1', 'name': 'bash', 'args': {'command': 'ls'}}])
        self.assertEqual(usage, {'in': 10, 'out': 7, 'cached': 2})
        self.assertIsNone(billed)
        self.assertEqual(len(chat.contents), 2)  # model content appended unchanged

    def test_step_empty_candidates(self):
        chat = dispatch._GeminiChat(llm.MODELS['argon'], 'P')
        with mock.patch.object(llm, '_post', return_value={}):
            text, calls, usage, _ = chat.step()
        self.assertEqual((text, calls, usage), ('', [], {'in': 0, 'out': 0, 'cached': 0}))
        self.assertEqual(len(chat.contents), 1)

    def test_results_and_nudge(self):
        chat = dispatch._GeminiChat(llm.MODELS['argon'], 'P')
        chat.results([({'name': 'bash', 'id': 'c1'}, {'output': 'a'}, None),
                      ({'name': 'view_image'}, {'output': 'b'}, ('image/jpeg', 'ZZ'))])
        parts = chat.contents[-1]['parts']
        self.assertEqual(parts[0], {'functionResponse': {'name': 'bash', 'response': {'output': 'a'}, 'id': 'c1'}})
        self.assertEqual(parts[1], {'functionResponse': {'name': 'view_image', 'response': {'output': 'b'}}})
        self.assertEqual(parts[2], {'inline_data': {'mime_type': 'image/jpeg', 'data': 'ZZ'}})
        chat.nudge('go')
        self.assertEqual(chat.contents[-1], {'role': 'user', 'parts': [{'text': 'go'}]})


class OpenRouterChatTests(Base):
    def reply(self, msg, **usage):
        u = {'prompt_tokens': 10, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 3}}
        u.update(usage)
        return {'choices': [{'message': msg}], 'usage': u}

    def test_step_with_tool_calls_and_bad_json(self):
        msg = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 't1', 'function': {'name': 'bash', 'arguments': '{"command": "ls"}'}},
            {'id': 't2', 'function': {'name': 'bash', 'arguments': '{oops'}},
            {'id': 't3', 'function': {'name': 'view_image'}}]}
        chat = dispatch._OpenRouterChat(dict(llm.MODELS['sol'], provider_pin='Pin'), 'P')
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'o'}), \
                mock.patch.object(llm, '_post', return_value=self.reply(msg, cost=0.07)) as post:
            text, calls, usage, billed = chat.step()
        body = post.call_args.args[2]
        self.assertEqual(body['messages'][0]['role'], 'system')
        self.assertEqual(body['tools'][0]['function'], dispatch.LOOP_TOOLS[0])
        self.assertEqual(body['provider'], {'order': ['Pin'], 'allow_fallbacks': False})
        self.assertEqual(post.call_args.args[1], {'Authorization': 'Bearer o'})
        self.assertEqual(text, '')
        self.assertEqual(calls[0], {'id': 't1', 'name': 'bash', 'args': {'command': 'ls'}})
        self.assertEqual(calls[1]['args'], {'_bad_json': '{oops'})
        self.assertEqual(calls[2]['args'], {})
        self.assertEqual(usage, {'in': 10, 'out': 5, 'cached': 3})
        self.assertEqual(billed, 0.07)
        self.assertIs(chat.messages[-1], msg)

    def test_step_text_only(self):
        chat = dispatch._OpenRouterChat(llm.MODELS['sol'], 'P')
        with mock.patch.object(llm, '_post', return_value=self.reply({'role': 'assistant', 'content': ' final '})):
            text, calls, usage, billed = chat.step()
        self.assertEqual((text, calls, billed), ('final', [], None))

    def test_step_error_raises(self):
        chat = dispatch._OpenRouterChat(llm.MODELS['sol'], 'P')
        with mock.patch.object(llm, '_post', return_value={'error': {'message': 'x'}}):
            with self.assertRaises(llm.LLMError):
                chat.step()

    def test_results_put_images_in_following_user_message(self):
        chat = dispatch._OpenRouterChat(llm.MODELS['sol'], 'P')
        chat.results([({'id': 't1'}, {'output': 'é'}, ('image/jpeg', 'ZZ')), ({'id': 't2'}, {'output': 'b'}, None)])
        self.assertEqual(chat.messages[-3], {'role': 'tool', 'tool_call_id': 't1', 'content': '{"output": "é"}'})
        self.assertEqual(chat.messages[-2]['tool_call_id'], 't2')
        last = chat.messages[-1]
        self.assertEqual(last['role'], 'user')
        self.assertEqual(last['content'][1]['image_url']['url'], 'data:image/jpeg;base64,ZZ')

    def test_results_no_image_no_user_message(self):
        chat = dispatch._OpenRouterChat(llm.MODELS['sol'], 'P')
        chat.results([({'id': 't1'}, {'output': 'a'}, None)])
        self.assertEqual(chat.messages[-1]['role'], 'tool')
        chat.nudge('go')
        self.assertEqual(chat.messages[-1], {'role': 'user', 'content': 'go'})


class FakeAnthropicModule(types.ModuleType):
    class RateLimitError(Exception):
        def __init__(self, message='slow'):
            self.message = message

    class APIStatusError(Exception):
        def __init__(self, status_code=500, message='server'):
            self.status_code, self.message = status_code, message

    class APIConnectionError(Exception):
        pass

    def __init__(self, responses):
        super().__init__('anthropic')
        self.responses = list(responses)
        self.requests = []
        outer = self

        class Messages:
            def create(s, **kw):
                outer.requests.append(json.loads(json.dumps(kw, default=lambda o: '<obj>')))
                r = outer.responses.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r

        class Client:
            def __init__(c, api_key=None):
                outer.api_key = api_key
                c.messages = Messages()

        self.Anthropic = Client


def amsg(blocks, stop='end_turn', inp=10, out=5, cr=0, cc=0, category=None):
    return types.SimpleNamespace(content=blocks, stop_reason=stop, stop_details=types.SimpleNamespace(category=category),
                                 usage=types.SimpleNamespace(input_tokens=inp, output_tokens=out,
                                                             cache_read_input_tokens=cr, cache_creation_input_tokens=cc))


def tb(text):
    return types.SimpleNamespace(type='text', text=text)


def ub(id, name, input):
    return types.SimpleNamespace(type='tool_use', id=id, name=name, input=input)


class AnthropicChatTests(Base):
    def make(self, responses):
        mod = FakeAnthropicModule(responses)
        p = mock.patch.dict(sys.modules, {'anthropic': mod})
        p.start()
        self.addCleanup(p.stop)
        os.environ['ANTHROPIC_API_KEY'] = 'ak'
        return mod, dispatch._AnthropicChat(llm.MODELS['haiku'], 'PROMPT')

    def test_step_parses_and_counts(self):
        mod, chat = self.make([amsg([tb(' hi '), ub('u1', 'bash', {'command': 'ls'})], inp=10, out=5, cr=100, cc=20)])
        text, calls, usage, billed = chat.step()
        self.assertEqual(mod.api_key, 'ak')
        self.assertEqual(text, 'hi')
        self.assertEqual(calls, [{'id': 'u1', 'name': 'bash', 'args': {'command': 'ls'}}])
        self.assertEqual(usage, {'in': 130, 'out': 5, 'cached': 100})
        self.assertIsNone(billed)
        req = mod.requests[0]
        self.assertEqual(req['model'], 'claude-haiku-5-5')
        self.assertEqual([t['name'] for t in req['tools']], ['bash', 'view_image'])
        self.assertIn('input_schema', req['tools'][0])
        self.assertEqual(req['messages'][-1]['content'][-1]['cache_control'], {'type': 'ephemeral'})
        self.assertEqual(chat.messages[-1]['role'], 'assistant')

    def test_cache_breakpoint_moves(self):
        mod, chat = self.make([amsg([tb('a')]), amsg([tb('b')])])
        chat.step()
        chat.nudge('again')
        chat.step()
        first_user = chat.messages[0]['content'][0]
        self.assertNotIn('cache_control', first_user)
        self.assertEqual(chat.messages[2]['content'][-1]['cache_control'], {'type': 'ephemeral'})

    def test_errors_translated(self):
        for exc, expect in ((FakeAnthropicModule.RateLimitError('slow'), 'rate limited: slow'),
                            (FakeAnthropicModule.APIStatusError(500, 'bad'), 'HTTP 500: bad'),
                            (FakeAnthropicModule.APIConnectionError('x'), 'connection error')):
            _, chat = self.make([exc])
            with self.assertRaises(llm.LLMError) as cm:
                chat.step()
            self.assertIn(expect, str(cm.exception))

    def test_refusal(self):
        _, chat = self.make([amsg([], stop='refusal', category='bio')])
        with self.assertRaises(llm.LLMError) as cm:
            chat.step()
        self.assertIn('refused: bio', str(cm.exception))

    def test_results_one_user_message_with_image(self):
        _, chat = self.make([])
        chat.results([({'id': 'u1'}, {'output': 'a'}, ('image/jpeg', 'ZZ')), ({'id': 'u2'}, {'output': 'b'}, None)])
        msg = chat.messages[-1]
        self.assertEqual(msg['role'], 'user')
        self.assertEqual([b['tool_use_id'] for b in msg['content']], ['u1', 'u2'])
        self.assertEqual(msg['content'][0]['content'][1]['source']['data'], 'ZZ')
        self.assertEqual(len(msg['content'][1]['content']), 1)


class ScriptedChat:
    """A chat that plays back (text, calls, usage, billed) tuples; records what it was sent."""
    script = []
    instances = []

    def __init__(self, spec, prompt):
        self.prompt = prompt
        self.received, self.nudges = [], []
        ScriptedChat.instances.append(self)

    def step(self):
        item = ScriptedChat.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def results(self, results):
        self.received.extend(results)

    def nudge(self, text):
        self.nudges.append(text)


def U(i=100, o=50, c=0):
    return {'in': i, 'out': o, 'cached': c}


class RunLoopTests(Base):
    def setUp(self):
        super().setUp()
        ScriptedChat.script, ScriptedChat.instances = [], []
        p = mock.patch.dict(dispatch.LOOP_CHATS, {'anthropic': (ScriptedChat, 'ANTHROPIC_API_KEY'),
                                                  'gemini': (ScriptedChat, 'GEMINI_API_KEY'),
                                                  'openrouter': (ScriptedChat, 'OPENROUTER_API_KEY')})
        p.start()
        self.addCleanup(p.stop)
        os.environ['ANTHROPIC_API_KEY'] = 'k'

    def test_unknown_model(self):
        r = dispatch.run_loop('nonesuch', 'p', 'a', 5, 1.0)
        self.assertIn('not a model in tools/models.json', r['error'])
        self.assertFalse(r['quota'])

    def test_missing_key_is_quota_and_nokey(self):
        del os.environ['ANTHROPIC_API_KEY']
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertTrue(r['quota'] and r['nokey'])
        self.assertIn('ANTHROPIC_API_KEY is not set', r['error'])

    def test_final_text_first_turn(self):
        ScriptedChat.script = [('## Report\n- Outcome: done', [], U(1000, 500, 200), None)]
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual((r['text'], r['error'], r['turns']), ('## Report\n- Outcome: done', '', 1))
        self.assertEqual((r['tokens_in'], r['tokens_out'], r['tokens_cached']), (1000, 500, 200))
        expected = llm.cost_of(llm.MODELS['haiku'], U(1000, 500, 200))
        self.assertEqual(r['cost'], round(expected, 6))
        self.assertEqual(r['list_cost'], expected)

    def test_tool_call_then_final(self):
        call = {'id': 'c', 'name': 'bash', 'args': {'command': 'echo'}}
        ScriptedChat.script = [('', [call], U(), None), ('done', [], U(), None)]
        with mock.patch.object(dispatch, '_run_tool', return_value=({'output': 'o'}, None)) as rt:
            r = dispatch.run_loop('haiku', 'p', 'agent7', 5, 1.0)
        self.assertEqual(r['turns'], 2)
        self.assertEqual(r['text'], 'done')
        self.assertEqual(ScriptedChat.instances[0].received, [(call, {'output': 'o'}, None)])
        self.assertEqual(rt.call_args.args[1]['BRIDGE_AGENT'], 'agent7')

    def test_billed_cost_used_when_given(self):
        ScriptedChat.script = [('done', [], U(), 0.25)]
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual(r['cost'], 0.25)
        self.assertEqual(r['list_cost'], llm.cost_of(llm.MODELS['haiku'], U()))

    def test_budget_stops_the_loop(self):
        call = {'id': 'c', 'name': 'bash', 'args': {}}
        ScriptedChat.script = [('', [call], U(), 0.6), ('', [call], U(), 0.6), ('never', [], U(), 0)]
        with mock.patch.object(dispatch, '_run_tool', return_value=({'output': ''}, None)):
            r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual(r['text'], '')
        self.assertIn('stopped at the $1.0 budget after 2 turns', r['error'])
        self.assertFalse(r['quota'])
        self.assertEqual(len(ScriptedChat.script), 1)  # the third step was never requested

    def test_turn_limit(self):
        call = {'id': 'c', 'name': 'bash', 'args': {}}
        ScriptedChat.script = [('', [call], U(1, 1), None)] * 130
        with mock.patch.object(dispatch, '_run_tool', return_value=({'output': ''}, None)):
            r = dispatch.run_loop('haiku', 'p', 'a', 5, 100.0)
        self.assertEqual(r['error'], 'stopped after 120 turns')
        self.assertEqual(r['turns'], 120)

    def test_timeout(self):
        ScriptedChat.script = [('x', [], U(), None)]
        clock = iter([1000.0, 1000.0 + 6 * 60])
        with mock.patch('time.time', side_effect=lambda: next(clock)):
            r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertIn('timed out after 5 minutes and 0 turns', r['error'])
        self.assertEqual(ScriptedChat.script, [('x', [], U(), None)])  # no request was made

    def test_api_error_reported_with_turn(self):
        ScriptedChat.script = [llm.LLMError('HTTP 500')]
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual(r['error'], 'API call failed on turn 1: HTTP 500')
        self.assertFalse(r['quota'])

    def test_one_empty_turn_is_nudged(self):
        ScriptedChat.script = [('', [], U(), None), ('report', [], U(), None)]
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual(r['text'], 'report')
        self.assertEqual(len(ScriptedChat.instances[0].nudges), 1)

    def test_two_empty_turns_fail(self):
        ScriptedChat.script = [('', [], U(), None), ('', [], U(), None)]
        r = dispatch.run_loop('haiku', 'p', 'a', 5, 1.0)
        self.assertEqual(r['error'], 'no output on turn 2')


class FakeProc:
    def __init__(self, lines, returncode=0, stderr=''):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.stderr = io.StringIO(stderr)
        self.killed = False

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


def ev(type, **part):
    return json.dumps({'type': type, 'part': part}) + '\n'


class RunOpencodeTests(Base):
    def setUp(self):
        super().setUp()
        os.environ['OPENROUTER_API_KEY'] = 'ok'

    def run_it(self, lines, model='sol', budget=5.0, **kw):
        proc = FakeProc(lines, **kw)
        with mock.patch('subprocess.Popen', return_value=proc) as popen:
            r = dispatch.run_opencode(model, 'PROMPT', 'oc', 5, budget)
        return r, proc, popen

    def test_success_keeps_last_step_text_and_sums_tokens(self):
        lines = [ev('step_start'), ev('text', text='thinking aloud'),
                 ev('step_finish', cost=0.01, tokens={'input': 10, 'output': 2, 'reasoning': 3, 'cache': {'read': 4, 'write': 1}}),
                 ev('step_start'), ev('text', text='part one'), ev('text', text='part two'),
                 ev('step_finish', cost=0.02, tokens={'input': 5, 'output': 1}), 'garbage not json\n']
        r, _, popen = self.run_it(lines)
        self.assertEqual(r['text'], 'part one\npart two')
        self.assertEqual(r['error'], '')
        self.assertEqual(r['turns'], 2)
        self.assertEqual((r['tokens_in'], r['tokens_cached'], r['tokens_out']), (20, 4, 6))
        self.assertAlmostEqual(r['cost'], 0.03)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:2], ['opencode', 'run'])
        self.assertEqual(cmd[cmd.index('-m') + 1], 'openrouter/openai/gpt-6.1-sol')
        self.assertEqual(cmd[-1], 'PROMPT')
        env = popen.call_args.kwargs['env']
        self.assertEqual((env['BRIDGE_AGENT'], env['OPENROUTER_API_KEY']), ('oc', 'ok'))

    def test_gemini_model_uses_openrouter_alias_and_full_id_passes_through(self):
        _, _, popen = self.run_it([ev('step_start'), ev('text', text='x')], model='argon')
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index('-m') + 1], 'openrouter/google/gemini-4-argon')
        _, _, popen = self.run_it([ev('text', text='x')], model='openrouter/vendor/model')
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index('-m') + 1], 'openrouter/vendor/model')

    def test_model_without_openrouter_id(self):
        with mock.patch.dict(llm.MODELS, {'nomap': {'provider': 'anthropic', 'id': 'x', 'in': 1, 'out': 1, 'cached': 1}}):
            with mock.patch('subprocess.Popen') as popen2:
                r = dispatch.run_opencode('nomap', 'p', 'a', 5, 1.0)
        popen2.assert_not_called()
        self.assertIn('no OpenRouter id', r['error'])

    def test_missing_key(self):
        del os.environ['OPENROUTER_API_KEY']
        with mock.patch('subprocess.Popen') as popen:
            r = dispatch.run_opencode('sol', 'p', 'a', 5, 1.0)
        popen.assert_not_called()
        self.assertTrue(r['quota'] and r['nokey'])

    def test_budget_kills_process(self):
        lines = [ev('step_start'), ev('text', text='x'), ev('step_finish', cost=1.5, tokens={})]
        r, proc, _ = self.run_it(lines, budget=1.0)
        self.assertTrue(proc.killed)
        self.assertIn('stopped at the $1.0 budget after 1 steps', r['error'])
        self.assertEqual(r['text'], '')

    def test_error_events(self):
        r, _, _ = self.run_it([ev('step_start'), json.dumps({'type': 'error', 'error': {'message': 'bad'}}) + '\n'], returncode=1)
        self.assertIn('bad', r['error'])
        self.assertFalse(r['quota'])

    def test_nonzero_exit_uses_stderr(self):
        r, _, _ = self.run_it([], returncode=2, stderr='crashed')
        self.assertEqual(r['error'], 'crashed')

    def test_no_final_text(self):
        r, _, _ = self.run_it([ev('step_start'), ev('step_finish', cost=0, tokens={})])
        self.assertEqual(r['error'], 'OpenCode ended without a final text')


class DispatchTests(Base):
    def ok(self, text='## Report\n- Outcome: done', **kw):
        d = {'text': text, 'error': '', 'quota': False, 'tokens_in': 1234, 'tokens_out': 5, 'tokens_cached': 0, 'turns': 2,
             'cost': 0.12, 'list_cost': 0.15}
        d.update(kw)
        return d

    def quota(self, msg='usage limit. Resets in 1h', **kw):
        d = {'text': '', 'error': msg, 'quota': True, 'cost': 0.0}
        d.update(kw)
        return d

    def run_d(self, brief, backends, **kw):
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            code = dispatch.dispatch(brief, 'retrieve', backends, **kw)
        return code, buf.getvalue()

    def test_success_writes_report_and_ledger(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_claude', return_value=self.ok()) as rc:
            code, out = self.run_d(brief, ['claude-api:haiku'])
        self.assertEqual(code, 0)
        report = os.path.join(self.tmp.name, 'brief-x.report.md')
        text = open(report).read()
        self.assertTrue(text.startswith('Worker report for `brief-x.md`: claude-api:haiku, agent claude-sub-api, '))
        self.assertIn(str(datetime.date.today()), text.splitlines()[0])
        self.assertIn('2 turns, 1,234 tokens in.', text.splitlines()[0])
        self.assertIn('- Outcome: done', text)
        prompt = rc.call_args.args[1]
        self.assertIn('Fetch the thing.', prompt)
        self.assertIn('agent name is claude-sub-api', prompt)
        self.assertTrue(rc.call_args.kwargs['api'])
        row = self.rows()[0]
        self.assertEqual((row['kind'], row['task'], row['backend'], row['model'], row['outcome']),
                         ('worker', 'retrieve:brief-x', 'claude-api', 'haiku', 'ok'))
        self.assertEqual((row['cost'], row['list_cost'], row['tokens_in'], row['turns']), (0.12, 0.15, 1234, 2))

    def test_agent_name_override_and_default_names(self):
        brief = self.brief()
        seen = {}

        def grab(kind):
            def f(model, prompt, name, *a, **k):
                seen[kind] = name
                return self.ok()
            return f
        with mock.patch.object(dispatch, 'run_agy', grab('agy')), mock.patch.object(dispatch, 'run_loop', grab('loop')), \
                mock.patch.object(dispatch, 'run_opencode', grab('opencode')), \
                mock.patch.object(dispatch, 'run_claude', grab('claude')):
            for b in ('agy:m', 'loop:haiku', 'opencode:openai/gpt-x', 'claude-plan:opus'):
                self.run_d(brief, [b])
            self.assertEqual(seen, {'agy': 'gemini', 'loop': 'loop-haiku', 'opencode': 'opencode-gpt-x', 'claude': 'claude-sub-plan'})
            self.run_d(brief, ['loop:haiku'], agent='custom')
            self.assertEqual(seen['loop'], 'custom')

    def test_quota_falls_through_to_next_backend_and_is_noted(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', return_value=self.quota('RESOURCE_EXHAUSTED Resets in 2h')), \
                mock.patch.object(dispatch, 'run_claude', return_value=self.ok()) as rc:
            code, out = self.run_d(brief, ['agy:m', 'claude-api:haiku'])
        self.assertEqual(code, 0)
        rc.assert_called_once()
        self.assertIn('not available', out)
        self.assertIn('agy', dispatch.load_state())
        outcomes = [r['outcome'] for r in self.rows()]
        self.assertEqual(outcomes, ['quota', 'ok'])
        self.assertIn('claude-api:haiku, agent', open(os.path.join(self.tmp.name, 'brief-x.report.md')).read())

    def test_ordinary_failure_does_not_fall_through(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', return_value=self.ok(text='', error='page would not load')), \
                mock.patch.object(dispatch, 'run_claude') as rc:
            code, out = self.run_d(brief, ['agy:m', 'claude-api:haiku'])
        self.assertEqual(code, 1)
        rc.assert_not_called()
        self.assertEqual(dispatch.load_state(), {})  # no quota note for a plain failure
        report = open(os.path.join(self.tmp.name, 'brief-x.report.md')).read()
        self.assertIn('- Outcome: failed', report)
        self.assertIn('the worker itself failed: page would not load', report)
        self.assertEqual(self.rows()[0]['outcome'], 'error: page would not load')

    def test_failure_report_uses_workers_own_text_if_present(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', return_value=self.ok(text='partial report', error='late error')):
            code, _ = self.run_d(brief, ['agy:m'])
        self.assertEqual(code, 1)
        self.assertIn('partial report', open(os.path.join(self.tmp.name, 'brief-x.report.md')).read())

    def test_nokey_falls_through_without_noting_quota(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_claude', side_effect=[self.quota('ANTHROPIC_API_KEY is not set', nokey=True), self.ok()]):
            code, _ = self.run_d(brief, ['claude-api:haiku', 'claude-plan:opus'])
        self.assertEqual(code, 0)
        self.assertEqual(dispatch.load_state(), {})

    def test_blocked_backend_is_skipped_without_running(self):
        brief = self.brief()
        future = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(timespec='minutes')
        dispatch.save_state({'agy': future})
        with mock.patch.object(dispatch, 'run_agy') as agy, mock.patch.object(dispatch, 'run_claude', return_value=self.ok()):
            code, out = self.run_d(brief, ['agy:m', 'claude-api:haiku'])
        agy.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn(f'quota used up until {future}, skipping', out)
        self.assertEqual(len(self.rows()), 1)

    def test_expired_block_runs_again(self):
        brief = self.brief()
        past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec='minutes')
        dispatch.save_state({'agy': past})
        with mock.patch.object(dispatch, 'run_agy', return_value=self.ok()) as agy:
            code, _ = self.run_d(brief, ['agy:m'])
        agy.assert_called_once()
        self.assertEqual(code, 0)

    def test_all_backends_unavailable_returns_2(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', return_value=self.quota()), \
                mock.patch.object(dispatch, 'run_claude', return_value=self.quota('credit balance', nokey=True)):
            code, out = self.run_d(brief, ['agy:m', 'claude-api:haiku'])
        self.assertEqual(code, 2)
        self.assertIn('no backend available', out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'brief-x.report.md')))

    def test_timeout_becomes_error_without_fallthrough(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', side_effect=subprocess.TimeoutExpired('agy', 1)), \
                mock.patch.object(dispatch, 'run_claude') as rc:
            code, _ = self.run_d(brief, ['agy:m', 'claude-api:haiku'], timeout_min=7)
        self.assertEqual(code, 1)
        rc.assert_not_called()
        self.assertIn('timed out after 7 minutes', open(os.path.join(self.tmp.name, 'brief-x.report.md')).read())

    def test_default_chain_from_role(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_agy', return_value=self.ok()) as agy:
            code, _ = self.run_d(brief, None)
        self.assertEqual(code, 0)
        self.assertEqual(agy.call_args.args[0], 'gemini-3.8-flash-high')

    def test_args_forwarded(self):
        brief = self.brief()
        with mock.patch.object(dispatch, 'run_claude', return_value=self.ok()) as rc:
            self.run_d(brief, ['claude-plan:opus'], timeout_min=9, budget=3.5)
        self.assertEqual(rc.call_args.args[2:5], ('claude-sub-plan', 9, 3.5))
        self.assertFalse(rc.call_args.kwargs['api'])


class CliTests(Base):
    """Run dispatch.py as __main__ with its root moved into the temp directory."""

    def run_cli(self, argv, run_side_effect=None):
        buf = io.StringIO()
        path = os.path.join(TOOLS, 'dispatch.py')
        code = None
        with mock.patch.object(llm, 'ROOT', self.tmp.name), mock.patch('sys.argv', ['dispatch.py'] + argv), \
                mock.patch('sys.stdout', buf), mock.patch('subprocess.run', side_effect=run_side_effect):
            try:
                runpy.run_path(path, run_name='__main__')
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_status_lists_roles_and_key_state(self):
        code, out = self.run_cli(['--status'])
        self.assertEqual(code, 0)
        self.assertIn('retrieve: agy:gemini-3.8-flash-high, claude-api:haiku', out)
        self.assertIn('argue: claude-plan:opus', out)
        self.assertIn('ANTHROPIC_API_KEY: not set', out)

    def test_no_brief_means_status(self):
        code, out = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertIn('retrieve:', out)

    def test_status_shows_blocked_backend(self):
        future = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(timespec='minutes')
        os.makedirs(os.path.dirname(self.state))
        json.dump({'agy': future}, open(self.state, 'w'))
        code, out = self.run_cli(['--status'])
        self.assertIn(f'agy:gemini-3.8-flash-high (out of quota until {future})', out)

    def test_end_to_end_with_mocked_claude(self):
        brief = self.brief()
        result = json.dumps({'result': '## Report\n- Outcome: done', 'is_error': False, 'num_turns': 1, 'usage': {}})
        code, out = self.run_cli([brief, '--role', 'argue'], run_side_effect=lambda *a, **k: completed(result))
        self.assertEqual(code, 0)
        self.assertIn('claude-plan:opus: running brief-x', out)
        self.assertIn('## Report', open(os.path.join(self.tmp.name, 'brief-x.report.md')).read())
        self.assertEqual(self.rows()[0]['backend'], 'claude-plan')

    def test_end_to_end_quota_everywhere_exit_2(self):
        brief = self.brief()
        result = json.dumps({'result': 'usage limit reached', 'is_error': True})
        code, out = self.run_cli([brief, '--backend', 'claude-plan:opus'], run_side_effect=lambda *a, **k: completed(result))
        self.assertEqual(code, 2)
        self.assertIn('no backend available', out)
        self.assertTrue(os.path.exists(self.state))

    def test_bad_role_rejected(self):
        with mock.patch('sys.stderr', io.StringIO()):
            code, _ = self.run_cli(['b.md', '--role', 'bogus'])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
