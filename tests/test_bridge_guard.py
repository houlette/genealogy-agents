"""Tests for .claude/hooks/bridge_guard.py, run as a subprocess the way Claude Code runs it."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / '.claude' / 'hooks' / 'bridge_guard.py'


def run_hook(stdin):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=30)


def bash(command):
    return json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}})


class WarnsForMainSessionTest(unittest.TestCase):
    def assertWarns(self, command):
        proc = run_hook(bash(command))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), f'no warning for: {command}')
        return json.loads(proc.stdout)

    def test_env_assignment(self):
        self.assertWarns('BRIDGE_AGENT=claude python3 tools/browser_bridge/client.py')

    def test_quoted_env_assignment(self):
        self.assertWarns('BRIDGE_AGENT="claude" curl http://127.0.0.1:9333/claims')
        self.assertWarns("BRIDGE_AGENT='claude' curl http://127.0.0.1:9333/claims")

    def test_env_assignment_at_end_of_command(self):
        self.assertWarns('export BRIDGE_AGENT=claude')

    def test_env_assignment_followed_by_semicolon(self):
        self.assertWarns('BRIDGE_AGENT=claude; python3 x.py')

    def test_set_agent_call(self):
        self.assertWarns('python3 -c "import client; client.set_agent(\'claude\')"')
        self.assertWarns("client.set_agent( 'claude' )")

    def test_script_under_scratch_claude(self):
        self.assertWarns('python3 scratch/claude/grab.py')
        self.assertWarns('python scratch/claude/grab.py --page 3')
        self.assertWarns('python3 /Users/x/proj/scratch/claude/grab.py')

    def test_output_shape(self):
        out = self.assertWarns('BRIDGE_AGENT=claude python3 x.py')
        self.assertEqual(set(out), {'hookSpecificOutput'})
        inner = out['hookSpecificOutput']
        self.assertEqual(inner['hookEventName'], 'PreToolUse')
        self.assertIn('Bridge guard', inner['additionalContext'])
        self.assertIn('retriever', inner['additionalContext'])
        self.assertNotIn('decision', inner)
        self.assertNotIn('permissionDecision', inner)

    def test_output_is_a_single_json_document(self):
        proc = run_hook(bash('BRIDGE_AGENT=claude python3 x.py'))
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)

    def test_multiline_command(self):
        self.assertWarns('cd /tmp\nBRIDGE_AGENT=claude python3 x.py\necho done')


class StaysSilentTest(unittest.TestCase):
    def assertSilent(self, stdin):
        proc = run_hook(stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, '')

    def test_worker_names(self):
        for name in ('claude-haiku-fs8', 'claude-sonnet-archion7', 'claude-sub-1', 'gemini', 'claude_x', 'claudette'):
            with self.subTest(name=name):
                self.assertSilent(bash(f'BRIDGE_AGENT={name} python3 x.py'))
                self.assertSilent(bash(f'python3 -c "client.set_agent(\'{name}\')"'))

    def test_other_scratch_folders(self):
        self.assertSilent(bash('python3 scratch/claude-haiku/grab.py'))
        self.assertSilent(bash('python3 scratch/gemini/grab.py'))
        self.assertSilent(bash('ls scratch/claude/'))  # not running a script

    def test_ordinary_commands(self):
        for cmd in ('ls -la', 'git status', 'python3 tools/check_tree.py', ''):
            with self.subTest(cmd=cmd):
                self.assertSilent(bash(cmd))

    def test_missing_or_odd_tool_input(self):
        self.assertSilent(json.dumps({}))
        self.assertSilent(json.dumps({'tool_input': None}))
        self.assertSilent(json.dumps({'tool_input': {}}))
        self.assertSilent(json.dumps({'tool_input': {'command': None}}))
        self.assertSilent(json.dumps({'tool_input': 'BRIDGE_AGENT=claude'}))
        self.assertSilent(json.dumps(['BRIDGE_AGENT=claude']))
        self.assertSilent(json.dumps('BRIDGE_AGENT=claude'))
        self.assertSilent(json.dumps(None))

    def test_invalid_json_and_empty_input(self):
        self.assertSilent('')
        self.assertSilent('not json')
        self.assertSilent('{"tool_input": ')


if __name__ == '__main__':
    unittest.main()
