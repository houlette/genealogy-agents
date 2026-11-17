import base64
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

KEYS = ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY')


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in KEYS}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class Resp:
    def __init__(self, status=200, body=None, text=''):
        self.status_code = status
        self._body = body
        self.text = text or json.dumps(body)

    def json(self):
        return self._body


class TempBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = os.path.join(self.tmp.name, 'ledger.jsonl')
        self.keyfile = os.path.join(self.tmp.name, 'keys.env')
        for p in (mock.patch.object(llm, 'LEDGER', self.ledger), mock.patch.object(llm, 'KEYFILE', self.keyfile),
                  mock.patch('time.sleep')):
            p.start()
            self.addCleanup(p.stop)
        env = clean_env()
        env.start()
        self.addCleanup(env.stop)

    def rows(self):
        if not os.path.exists(self.ledger):
            return []
        return [json.loads(l) for l in open(self.ledger) if l.strip()]


class KeyTests(TempBase):
    def test_missing_returns_none(self):
        self.assertIsNone(llm.key('ANTHROPIC_API_KEY'))

    def test_environment_beats_keyfile(self):
        open(self.keyfile, 'w').write('ANTHROPIC_API_KEY=fromfile\n')
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fromenv'}):
            self.assertEqual(llm.key('ANTHROPIC_API_KEY'), 'fromenv')

    def test_keyfile_used_and_quotes_stripped(self):
        open(self.keyfile, 'w').write('# comment\nANTHROPIC_API_KEY="abc"\nGEMINI_API_KEY=\'def\'\n'
                                      'OPENROUTER_API_KEY = ghi \nEMPTY=\n')
        self.assertEqual(llm.key('ANTHROPIC_API_KEY'), 'abc')
        self.assertEqual(llm.key('GEMINI_API_KEY'), 'def')
        self.assertEqual(llm.key('OPENROUTER_API_KEY'), 'ghi')
        self.assertIsNone(llm.key('EMPTY'))

    def test_value_containing_equals(self):
        open(self.keyfile, 'w').write('ANTHROPIC_API_KEY=ab=cd==\n')
        self.assertEqual(llm.key('ANTHROPIC_API_KEY'), 'ab=cd==')

    def test_empty_env_value_falls_to_keyfile(self):
        open(self.keyfile, 'w').write('ANTHROPIC_API_KEY=fromfile\n')
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            self.assertEqual(llm.key('ANTHROPIC_API_KEY'), 'fromfile')


class CostTests(unittest.TestCase):
    def test_cost_with_cached_tokens(self):
        spec = llm.MODELS['opus']  # in 4, out 20, cached 0.2
        usage = {'in': 1000000, 'out': 100000, 'cached': 400000}
        # fresh 600k*4 + cached 400k*0.2 + out 100k*20 = 2.4 + 0.08 + 2.0 = 4.48
        self.assertEqual(llm.cost_of(spec, usage), 4.48)

    def test_cost_no_cache(self):
        spec = llm.MODELS['haiku']
        self.assertEqual(llm.cost_of(spec, {'in': 1000, 'out': 2000, 'cached': 0}), round((1000 * 0.1 + 2000 * 0.5) / 1e6, 6))

    def test_cached_greater_than_in_does_not_go_negative(self):
        spec = {'in': 1.0, 'out': 1.0, 'cached': 0.5}
        self.assertEqual(llm.cost_of(spec, {'in': 10, 'out': 0, 'cached': 20}), round(20 * 0.5 / 1e6, 6))

    def test_models_roster_has_prices(self):
        for name, spec in llm.MODELS.items():
            self.assertFalse(name.startswith('_'))
            for f in ('provider', 'id', 'in', 'out', 'cached'):
                self.assertIn(f, spec, name)


class LogTests(TempBase):
    def test_log_line_format(self):
        llm.log({'kind': 'call', 'task': 'é', 'cost': 1.5})
        llm.log({'kind': 'call', 'task': 'b'})
        raw = open(self.ledger, encoding='utf-8').read().splitlines()
        self.assertEqual(len(raw), 2)
        row = json.loads(raw[0])
        self.assertEqual(row['task'], 'é')
        self.assertIn('é', raw[0])  # ensure_ascii False
        self.assertRegex(row['ts'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$')
        self.assertEqual(list(row)[0], 'ts')


@unittest.skipIf(Image is None, 'Pillow missing')
class ImageTests(TempBase):
    def make(self, size, mode='RGB', fmt='PNG', name='x.png'):
        p = os.path.join(self.tmp.name, name)
        Image.new(mode, size, 'white').save(p, fmt)
        return p

    def decode(self, b64):
        return Image.open(io.BytesIO(base64.b64decode(b64)))

    def test_small_image_unchanged_size_and_jpeg(self):
        media, data = llm.encode_image(self.make((100, 50)))
        self.assertEqual(media, 'image/jpeg')
        im = self.decode(data)
        self.assertEqual(im.format, 'JPEG')
        self.assertEqual(im.size, (100, 50))

    def test_large_image_downscaled_keeps_aspect(self):
        _, data = llm.encode_image(self.make((4000, 1000)), max_side=2000)
        self.assertEqual(self.decode(data).size, (2000, 500))

    def test_custom_max_side(self):
        _, data = llm.encode_image(self.make((300, 600)), max_side=100)
        self.assertEqual(self.decode(data).size, (50, 100))

    def test_rgba_and_palette_converted(self):
        for mode in ('RGBA', 'P', 'L'):
            _, data = llm.encode_image(self.make((10, 10), mode=mode))
            self.assertEqual(self.decode(data).mode, 'RGB')

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            llm.encode_image(os.path.join(self.tmp.name, 'nope.png'))


class ParseJsonTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(llm.parse_json('{"a": 1}'), {'a': 1})

    def test_fenced_and_trailing_text(self):
        self.assertEqual(llm.parse_json('Here:\n```json\n{"a": {"b": [1, 2]}}\n```\nDone {not json'), {'a': {'b': [1, 2]}})

    def test_no_json(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json('no braces here')

    def test_bad_json(self):
        with self.assertRaises(llm.LLMError) as cm:
            llm.parse_json('{"a": ')
        self.assertIn('bad JSON', str(cm.exception))


class RouteTests(TempBase):
    def test_gemini_with_key_stays_gemini(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'g', 'OPENROUTER_API_KEY': 'o'}):
            self.assertEqual(llm.route('argon')[0], 'gemini')

    def test_gemini_falls_back_to_openrouter(self):
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'o'}):
            provider, spec = llm.route('argon')
        self.assertEqual(provider, 'openrouter')
        self.assertEqual(spec['id'], llm.MODELS['argon']['openrouter'])

    def test_gemini_no_keys_stays_gemini(self):
        self.assertEqual(llm.route('argon')[0], 'gemini')

    def test_other_providers(self):
        self.assertEqual(llm.route('opus')[0], 'anthropic')
        self.assertEqual(llm.route('sol')[0], 'openrouter')


class PostTests(TempBase):
    def test_success(self):
        with mock.patch('requests.post', return_value=Resp(200, {'ok': 1})) as p:
            self.assertEqual(llm._post('http://x', {'h': 1}, {'b': 2}), {'ok': 1})
        self.assertEqual(p.call_args.kwargs['json'], {'b': 2})
        self.assertEqual(p.call_args.kwargs['headers'], {'h': 1})

    def test_retries_on_429_then_succeeds(self):
        seq = [Resp(429, text='slow'), Resp(503, text='x'), Resp(200, {'ok': 1})]
        with mock.patch('requests.post', side_effect=seq) as p:
            self.assertEqual(llm._post('u', {}, {}), {'ok': 1})
        self.assertEqual(p.call_count, 3)

    def test_gives_up_after_three_attempts_on_429(self):
        seq = [Resp(429, text='a'), Resp(429, text='b'), Resp(429, text='c')]
        with mock.patch('requests.post', side_effect=seq):
            with self.assertRaises(llm.LLMError) as cm:
                llm._post('u', {}, {})
        self.assertIn('HTTP 429', str(cm.exception))

    def test_non_retryable_status_raises_at_once(self):
        with mock.patch('requests.post', return_value=Resp(400, text='bad' * 300)) as p:
            with self.assertRaises(llm.LLMError) as cm:
                llm._post('u', {}, {})
        self.assertEqual(p.call_count, 1)
        self.assertIn('HTTP 400', str(cm.exception))
        self.assertLessEqual(len(str(cm.exception)), 420)  # body truncated to 400

    def test_connection_error_retried_then_raised(self):
        import requests
        with mock.patch('requests.post', side_effect=requests.ConnectionError('boom')) as p:
            with self.assertRaises(llm.LLMError) as cm:
                llm._post('u', {}, {})
        self.assertEqual(p.call_count, 3)
        self.assertIn('connection error', str(cm.exception))

    def test_connection_error_then_success(self):
        import requests
        with mock.patch('requests.post', side_effect=[requests.Timeout('t'), Resp(200, {'ok': 2})]):
            self.assertEqual(llm._post('u', {}, {}), {'ok': 2})


class GeminiTests(TempBase):
    def reply(self, **kw):
        base = {'candidates': [{'content': {'parts': [{'text': 'hello '}, {'text': 'secret', 'thought': True}, {'text': 'world'}]},
                                'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 30,
                                  'cachedContentTokenCount': 10}}
        base.update(kw)
        return base

    def test_request_and_parse(self):
        spec = llm.MODELS['argon']
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'gk'}), \
                mock.patch.object(llm, '_post', return_value=self.reply()) as post:
            out, usage, stop = llm._gemini(spec, 'SYS', 'TXT', [('image/jpeg', 'AAA')], 'high', 123)
        url, headers, body = post.call_args.args
        self.assertTrue(url.endswith(f'/{spec["id"]}:generateContent'))
        self.assertEqual(headers, {'x-goog-api-key': 'gk'})
        self.assertEqual(body['systemInstruction']['parts'][0]['text'], 'SYS')
        self.assertEqual(body['generationConfig']['maxOutputTokens'], 123)
        parts = body['contents'][0]['parts']
        self.assertEqual(parts[0], {'inline_data': {'mime_type': 'image/jpeg', 'data': 'AAA'}})
        self.assertEqual(parts[-1], {'text': 'TXT'})
        self.assertEqual(out, 'hello world')  # thought parts removed
        self.assertEqual(usage, {'in': 100, 'out': 50, 'cached': 10})
        self.assertEqual(stop, 'STOP')

    def test_no_candidates_raises(self):
        with mock.patch.object(llm, '_post', return_value={'promptFeedback': {'blockReason': 'SAFETY'}}):
            with self.assertRaises(llm.LLMError) as cm:
                llm._gemini(llm.MODELS['argon'], 's', 't', [], 'high', 10)
        self.assertIn('SAFETY', str(cm.exception))

    def test_candidate_without_parts_raises(self):
        with mock.patch.object(llm, '_post', return_value={'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {}}]}):
            with self.assertRaises(llm.LLMError):
                llm._gemini(llm.MODELS['argon'], 's', 't', [], 'high', 10)


class OpenRouterTests(TempBase):
    def reply(self, **kw):
        base = {'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 50, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 7},
                          'cost': 0.0123},
                'provider': 'Acme'}
        base.update(kw)
        return base

    def test_request_and_parse(self):
        spec = dict(llm.MODELS['sol'])
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'ok'}), \
                mock.patch.object(llm, '_post', return_value=self.reply()) as post:
            out, usage, stop = llm._openrouter(spec, 'SYS', 'TXT', [('image/jpeg', 'AAA')], 'max', 99)
        url, headers, body = post.call_args.args
        self.assertEqual(url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(headers, {'Authorization': 'Bearer ok'})
        self.assertEqual(body['model'], spec['id'])
        self.assertEqual(body['reasoning'], {'effort': 'high'})  # xhigh/max mapped to high
        self.assertEqual(body['messages'][0], {'role': 'system', 'content': 'SYS'})
        user = body['messages'][1]['content']
        self.assertEqual(user[0]['image_url']['url'], 'data:image/jpeg;base64,AAA')
        self.assertEqual(user[-1], {'type': 'text', 'text': 'TXT'})
        self.assertNotIn('provider', body)
        self.assertEqual(out, 'answer')
        self.assertEqual(usage, {'in': 50, 'out': 5, 'cached': 7, 'billed': 0.0123})
        self.assertEqual(stop, 'stop via Acme')

    def test_effort_passthrough_and_provider_pin_and_model_id(self):
        spec = dict(llm.MODELS['sol'], provider_pin='Fireworks')
        with mock.patch.object(llm, '_post', return_value=self.reply()) as post:
            llm._openrouter(spec, 's', 't', [], 'low', 9, model_id='other/model')
        body = post.call_args.args[2]
        self.assertEqual(body['reasoning'], {'effort': 'low'})
        self.assertEqual(body['model'], 'other/model')
        self.assertEqual(body['provider'], {'order': ['Fireworks'], 'allow_fallbacks': False})

    def test_error_or_no_choices(self):
        for r in ({'error': {'message': 'nope'}}, {'choices': []}):
            with mock.patch.object(llm, '_post', return_value=r):
                with self.assertRaises(llm.LLMError):
                    llm._openrouter(llm.MODELS['sol'], 's', 't', [], 'high', 9)

    def test_null_content_gives_empty_text_and_no_billed(self):
        r = self.reply(choices=[{'message': {'content': None}, 'finish_reason': 'length'}], usage={'prompt_tokens': 1})
        with mock.patch.object(llm, '_post', return_value=r):
            out, usage, _ = llm._openrouter(llm.MODELS['sol'], 's', 't', [], 'high', 9)
        self.assertEqual(out, '')
        self.assertNotIn('billed', usage)


class FakeAnthropic:
    """Stands in for the anthropic SDK module."""

    class RateLimitError(Exception):
        def __init__(self, message='slow'):
            self.message = message

    class APIStatusError(Exception):
        def __init__(self, status_code=500, message='server'):
            self.status_code, self.message = status_code, message

    class APIConnectionError(Exception):
        pass

    def __init__(self, final=None, exc=None):
        outer = self
        self.final, self.exc, self.kwargs, self.api_key = final, exc, None, None

        class Stream:
            def __enter__(s):
                if outer.exc:
                    raise outer.exc
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return outer.final

        class Messages:
            def stream(s, **kw):
                outer.kwargs = kw
                return Stream()

        class Client:
            def __init__(c, api_key=None):
                outer.api_key = api_key
                c.messages = Messages()

        self.module = types.ModuleType('anthropic')
        self.module.Anthropic = Client
        self.module.RateLimitError = self.RateLimitError
        self.module.APIStatusError = self.APIStatusError
        self.module.APIConnectionError = self.APIConnectionError


def message(text='hi', stop='end_turn', inp=10, out=5, cache_read=0, cache_create=0, category=None):
    blocks = [types.SimpleNamespace(type='thinking', text='ignore'), types.SimpleNamespace(type='text', text=text)]
    return types.SimpleNamespace(
        content=blocks, stop_reason=stop, stop_details=types.SimpleNamespace(category=category),
        usage=types.SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
                                    cache_creation_input_tokens=cache_create))


class AnthropicTests(TempBase):
    def run_it(self, fake, **kw):
        with mock.patch.dict(sys.modules, {'anthropic': fake.module}), \
                mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'ak'}):
            return llm._anthropic(llm.MODELS['opus'], 'SYS', 'TXT', kw.get('images', []), 'medium', 777)

    def test_request_and_usage(self):
        fake = FakeAnthropic(final=message('answer', inp=10, out=5, cache_read=100, cache_create=20))
        out, usage, stop = self.run_it(fake, images=[('image/jpeg', 'AAA')])
        self.assertEqual(out, 'answer')
        self.assertEqual(usage, {'in': 130, 'out': 5, 'cached': 100})
        self.assertEqual(stop, 'end_turn')
        self.assertEqual(fake.api_key, 'ak')
        kw = fake.kwargs
        self.assertEqual(kw['model'], 'claude-opus-5-5')
        self.assertEqual(kw['max_tokens'], 777)
        self.assertEqual(kw['output_config'], {'effort': 'medium'})
        content = kw['messages'][0]['content']
        self.assertEqual(content[0]['type'], 'image')
        self.assertEqual(content[0]['source']['data'], 'AAA')
        self.assertEqual(content[-1], {'type': 'text', 'text': 'TXT'})

    def test_none_cache_fields(self):
        m = message()
        m.usage.cache_read_input_tokens = None
        m.usage.cache_creation_input_tokens = None
        _, usage, _ = self.run_it(FakeAnthropic(final=m))
        self.assertEqual(usage, {'in': 10, 'out': 5, 'cached': 0})

    def test_refusal(self):
        with self.assertRaises(llm.LLMError) as cm:
            self.run_it(FakeAnthropic(final=message(stop='refusal', category='cyber')))
        self.assertIn('refused: cyber', str(cm.exception))

    def test_error_translation(self):
        cases = [(FakeAnthropic.RateLimitError('slow'), 'rate limited: slow'),
                 (FakeAnthropic.APIStatusError(529, 'overloaded'), 'HTTP 529: overloaded'),
                 (FakeAnthropic.APIConnectionError('down'), 'connection error: down')]
        for exc, expect in cases:
            with self.assertRaises(llm.LLMError) as cm:
                self.run_it(FakeAnthropic(exc=exc))
            self.assertIn(expect, str(cm.exception))


class CallTests(TempBase):
    def test_missing_key_raises_before_any_request_and_no_ledger(self):
        with mock.patch.object(llm, '_anthropic') as fn:
            with self.assertRaises(llm.LLMError) as cm:
                llm.call('opus', 's', 't')
        self.assertIn('ANTHROPIC_API_KEY is not set', str(cm.exception))
        fn.assert_not_called()

    def test_success_logs_and_returns(self):
        usage = {'in': 1000, 'out': 500, 'cached': 200}
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), \
                mock.patch.object(llm, '_anthropic', return_value=('text', usage, 'end_turn')) as fn:
            r = llm.call('opus', 'SYS', 'TXT', task='unit:1')
        expected = llm.cost_of(llm.MODELS['opus'], usage)
        self.assertEqual(r, {'text': 'text', 'usage': usage, 'cost': expected, 'model': 'claude-opus-5-5', 'stop': 'end_turn'})
        self.assertEqual(fn.call_args.args[4:], ('high', 16000))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['kind'], row['task'], row['backend'], row['model'], row['outcome']),
                         ('call', 'unit:1', 'anthropic', 'claude-opus-5-5', 'ok'))
        self.assertEqual((row['tokens_in'], row['tokens_out'], row['tokens_cached']), (1000, 500, 200))
        self.assertEqual(row['cost'], round(expected, 6))
        self.assertEqual(row['list_cost'], expected)
        self.assertIn('seconds', row)

    def test_billed_cost_overrides_list_cost(self):
        usage = {'in': 1000, 'out': 500, 'cached': 0, 'billed': 0.5}
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k'}), \
                mock.patch.object(llm, '_openrouter', return_value=('t', usage, 'stop')):
            r = llm.call('sol', 's', 't')
        self.assertEqual(r['cost'], 0.5)
        row = self.rows()[0]
        self.assertEqual(row['cost'], 0.5)
        self.assertEqual(row['list_cost'], llm.cost_of(llm.MODELS['sol'], usage))
        self.assertEqual(row['backend'], 'openrouter')

    def test_failed_call_is_logged_and_reraised(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), \
                mock.patch.object(llm, '_anthropic', side_effect=llm.LLMError('HTTP 500: ' + 'x' * 500)):
            with self.assertRaises(llm.LLMError):
                llm.call('haiku', 's', 't', task='t')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['outcome'].startswith('error: HTTP 500'))
        self.assertLessEqual(len(rows[0]['outcome']), 300)
        self.assertNotIn('cost', rows[0])

    def test_min_max_tokens_floor_from_models_json(self):
        usage = {'in': 1, 'out': 1, 'cached': 0}
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'k'}), \
                mock.patch.object(llm, '_gemini', return_value=('t', usage, 'STOP')) as fn:
            llm.call('carbon', 's', 't', max_tokens=1000)
            self.assertEqual(fn.call_args.args[5], 32000)
            llm.call('carbon', 's', 't', max_tokens=50000)
            self.assertEqual(fn.call_args.args[5], 50000)

    def test_gemini_via_openrouter_fallback_logs_openrouter_model(self):
        usage = {'in': 1, 'out': 1, 'cached': 0}
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k'}), \
                mock.patch.object(llm, '_openrouter', return_value=('t', usage, 's')) as fn:
            llm.call('argon', 's', 't')
        self.assertEqual(fn.call_args.args[0]['id'], 'google/gemini-4-argon')
        self.assertEqual(self.rows()[0]['backend'], 'openrouter')

    @unittest.skipIf(Image is None, 'Pillow missing')
    def test_images_encoded_and_passed(self):
        p = os.path.join(self.tmp.name, 'i.png')
        Image.new('RGB', (30, 30), 'red').save(p)
        usage = {'in': 1, 'out': 1, 'cached': 0}
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), \
                mock.patch.object(llm, '_anthropic', return_value=('t', usage, 's')) as fn:
            llm.call('haiku', 's', 't', images=[p])
        imgs = fn.call_args.args[3]
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0][0], 'image/jpeg')

    def test_end_to_end_through_fake_http(self):
        body = {'choices': [{'message': {'content': '{"a": 1}'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k'}), \
                mock.patch('requests.post', return_value=Resp(200, body)):
            r = llm.call('terra', 's', 't')
        self.assertEqual(llm.parse_json(r['text']), {'a': 1})
        self.assertEqual(r['cost'], llm.cost_of(llm.MODELS['terra'], r['usage']))


class CheckAndMainTests(TempBase):
    def test_check_reports_keys_and_failures(self):
        def fake_call(name, *a, **k):
            if name == 'opus':
                raise llm.LLMError('bad key')
            return {'usage': {'in': 3, 'out': 1}, 'cost': 0.001}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}), mock.patch.object(llm, 'call', fake_call), \
                mock.patch('sys.stdout', buf):
            llm.check()
        out = buf.getvalue()
        self.assertIn('ANTHROPIC_API_KEY: set', out)
        self.assertIn('GEMINI_API_KEY: NOT SET', out)
        self.assertIn('FAILED: bad key', out)
        self.assertEqual(out.count(' ok '), len(llm.MODELS) - 1)

    def test_main_entry_point(self):
        import runpy
        buf = io.StringIO()
        with mock.patch('sys.argv', ['llm.py']), mock.patch('sys.stdout', buf):
            runpy.run_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'llm.py'),
                           run_name='__main__')
        self.assertIn('One-shot calls', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
