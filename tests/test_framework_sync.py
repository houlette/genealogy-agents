"""Tests for tools/framework_sync.py, against a small fake framework so the real one is never changed."""
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout

import support

import framework_sync as fs  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fw = os.path.join(self.tmp.name, "fw")
        self.proj = os.path.join(self.tmp.name, "proj")
        write(os.path.join(self.fw, "framework.json"), json.dumps({
            "name": "test-framework",
            "owned": ["AGENTS.md", "tools/*.py", "tools/hooks/*"],
            "seeds": {"templates/PROJECT.md": "PROJECT.md", "templates/gitignore": ".gitignore"},
        }))
        write(os.path.join(self.fw, "AGENTS.md"), "rules v1\n")
        write(os.path.join(self.fw, "tools", "a.py"), "print('a v1')\n")
        write(os.path.join(self.fw, "tools", "sub", "deep.py"), "not owned: one level too deep\n")
        write(os.path.join(self.fw, "tools", "hooks", "pre-commit"), "#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(self.fw, "tools", "hooks", "pre-commit"), 0o755)
        write(os.path.join(self.fw, "templates", "PROJECT.md"), "Describe your project.\n")
        write(os.path.join(self.fw, "templates", "gitignore"), "scratch/\n")
        write(os.path.join(self.fw, "notes.txt"), "not owned\n")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fs.main([*args, "--framework", self.fw])
        return code, buf.getvalue()

    def lock(self):
        return json.loads(read(os.path.join(self.proj, ".framework-lock.json")))

    def init(self):
        code, out = self.cli("init", self.proj)
        self.assertEqual(code, 0, out)
        return out

    def test_owned_glob_matching(self):
        files = fs.owned_files(self.fw, json.loads(read(os.path.join(self.fw, "framework.json"))))
        self.assertEqual(files, ["AGENTS.md", "tools/a.py", "tools/hooks/pre-commit"])

    def test_init_copies_owned_files_and_seeds(self):
        out = self.init()
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "rules v1\n")
        self.assertEqual(read(os.path.join(self.proj, "PROJECT.md")), "Describe your project.\n")
        self.assertEqual(read(os.path.join(self.proj, ".gitignore")), "scratch/\n")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "notes.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "templates")))
        self.assertTrue(os.stat(os.path.join(self.proj, "tools", "hooks", "pre-commit")).st_mode & stat.S_IXUSR)
        lock = self.lock()
        self.assertEqual(lock["framework"], "test-framework")
        self.assertEqual(sorted(lock["files"]), ["AGENTS.md", "tools/a.py", "tools/hooks/pre-commit"])
        self.assertEqual(lock["files"]["AGENTS.md"], fs.sha(os.path.join(self.fw, "AGENTS.md")))
        self.assertEqual(lock["seeds"], [".gitignore", "PROJECT.md"])
        self.assertIn("Next: fill in PROJECT.md", out)

    def test_update_flows_when_project_unchanged(self):
        self.init()
        write(os.path.join(self.fw, "AGENTS.md"), "rules v2\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 0)
        self.assertIn("update", out)
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "rules v2\n")
        self.assertEqual(self.lock()["files"]["AGENTS.md"], fs.sha(os.path.join(self.fw, "AGENTS.md")))

    def test_local_change_is_kept_and_reported(self):
        self.init()
        write(os.path.join(self.proj, "tools", "a.py"), "print('local')\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 1)
        self.assertIn("changed here", out)
        self.assertEqual(read(os.path.join(self.proj, "tools", "a.py")), "print('local')\n")

    def test_conflict_when_both_changed_and_force_overwrites(self):
        self.init()
        write(os.path.join(self.proj, "AGENTS.md"), "local rules\n")
        write(os.path.join(self.fw, "AGENTS.md"), "rules v2\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"conflict\s+AGENTS.md")
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "local rules\n")
        code, out = self.cli("sync", "--project", self.proj, "--force", "AGENTS.md")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"overwrite\s+AGENTS.md")
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "rules v2\n")

    def test_existing_different_file_never_synced_is_a_conflict(self):
        write(os.path.join(self.proj, "AGENTS.md"), "a project's own older rules\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"conflict\s+AGENTS.md")
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "a project's own older rules\n")
        self.assertNotIn("AGENTS.md", self.lock()["files"])
        self.assertIn("tools/a.py", self.lock()["files"])

    def test_identical_existing_file_is_adopted(self):
        write(os.path.join(self.proj, "AGENTS.md"), "rules v1\n")
        self.cli("sync", "--project", self.proj)
        self.assertIn("AGENTS.md", self.lock()["files"])

    def test_seeds_are_not_overwritten_or_recreated(self):
        self.init()
        write(os.path.join(self.proj, "PROJECT.md"), "My project.\n")
        os.remove(os.path.join(self.proj, ".gitignore"))
        write(os.path.join(self.fw, "templates", "PROJECT.md"), "Template v2.\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 0)
        self.assertEqual(read(os.path.join(self.proj, "PROJECT.md")), "My project.\n")
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".gitignore")))

    def test_existing_seed_is_recorded_not_replaced(self):
        write(os.path.join(self.proj, "PROJECT.md"), "Mine.\n")
        self.cli("sync", "--project", self.proj)
        self.assertEqual(read(os.path.join(self.proj, "PROJECT.md")), "Mine.\n")
        self.assertIn("PROJECT.md", self.lock()["seeds"])

    def test_new_seed_added_later_is_seeded(self):
        self.init()
        manifest = json.loads(read(os.path.join(self.fw, "framework.json")))
        manifest["seeds"]["templates/tasks.md"] = "tasks.md"
        write(os.path.join(self.fw, "framework.json"), json.dumps(manifest))
        write(os.path.join(self.fw, "templates", "tasks.md"), "Queue.\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertIn("seeded", out)
        self.assertEqual(read(os.path.join(self.proj, "tasks.md")), "Queue.\n")

    def test_removed_upstream_needs_prune_and_respects_local_changes(self):
        self.init()
        write(os.path.join(self.fw, "tools", "b.py"), "b\n")
        self.cli("sync", "--project", self.proj)
        os.remove(os.path.join(self.fw, "tools", "a.py"))
        os.remove(os.path.join(self.fw, "tools", "b.py"))
        write(os.path.join(self.proj, "tools", "b.py"), "b changed here\n")
        code, out = self.cli("sync", "--project", self.proj)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"removed upstream\s+tools/a.py")
        self.assertRegex(out, r"removed upstream, changed here\s+tools/b.py")
        self.assertTrue(os.path.exists(os.path.join(self.proj, "tools", "a.py")))
        code, out = self.cli("sync", "--project", self.proj, "--prune")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "tools", "a.py")))
        self.assertTrue(os.path.exists(os.path.join(self.proj, "tools", "b.py")))
        self.assertNotIn("tools/a.py", self.lock()["files"])

    def test_deleted_here_is_not_restored_without_force(self):
        self.init()
        os.remove(os.path.join(self.proj, "tools", "a.py"))
        code, out = self.cli("sync", "--project", self.proj)
        self.assertRegex(out, r"deleted here\s+tools/a.py")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "tools", "a.py")))
        self.cli("sync", "--project", self.proj, "--force", "all")
        self.assertTrue(os.path.exists(os.path.join(self.proj, "tools", "a.py")))

    def test_dry_run_writes_nothing(self):
        code, out = self.cli("sync", "--project", self.proj, "--dry-run")
        self.assertIn("Would apply:", out)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "AGENTS.md")))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".framework-lock.json")))

    def test_status_counts(self):
        self.init()
        write(os.path.join(self.fw, "AGENTS.md"), "rules v2\n")
        write(os.path.join(self.proj, "tools", "a.py"), "local\n")
        code, out = self.cli("status", "--project", self.proj)
        self.assertEqual(code, 0)
        self.assertIn("1 changed here", out)
        self.assertIn("1 same", out)
        self.assertIn("1 update", out)
        self.assertEqual(read(os.path.join(self.proj, "AGENTS.md")), "rules v1\n")

    def test_diff(self):
        self.init()
        write(os.path.join(self.proj, "AGENTS.md"), "rules v1\nlocal rule\n")
        code, out = self.cli("diff", "AGENTS.md", "--project", self.proj)
        self.assertEqual(code, 0)
        self.assertIn("--- framework/AGENTS.md", out)
        self.assertIn("+local rule", out)

    def test_refuses_to_sync_into_itself_or_without_manifest(self):
        with self.assertRaises(SystemExit):
            self.cli("sync", "--project", self.fw)
        with self.assertRaises(SystemExit):
            fs.main(["status", "--framework", self.proj, "--project", self.fw])

    def test_lock_records_commit_when_framework_is_a_git_repo(self):
        support.git_init(self.fw)
        support.git(self.fw, "add", "-A")
        support.git(self.fw, "commit", "-q", "-m", "fw")
        self.init()
        head = support.git(self.fw, "rev-parse", "--short", "HEAD").stdout.strip()
        self.assertEqual(self.lock()["commit"], head)


class RealManifest(unittest.TestCase):
    """The framework's own framework.json names files that exist and holds no project data."""

    def setUp(self):
        self.manifest = json.loads(read(os.path.join(support.FRAMEWORK, "framework.json")))

    def test_seeds_exist(self):
        for src in self.manifest["seeds"]:
            self.assertTrue(os.path.exists(os.path.join(support.FRAMEWORK, src)), src)

    def test_core_files_are_owned(self):
        owned = fs.owned_files(support.FRAMEWORK, self.manifest)
        for f in ("AGENTS.md", "tools/check_tree.py", "tools/framework_sync.py", "tools/hooks/pre-commit",
                  "tools/browser_bridge/daemon.py", ".claude/agents/retriever.md", "tests/support.py",
                  "tests/fixtures/sample-tree.ged", "make-tree-view.py"):
            self.assertIn(f, owned)
        self.assertFalse(any(f.startswith("templates/") for f in owned))

    def test_init_of_real_framework_gives_a_working_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "p")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fs.main(["init", proj, "--framework", support.FRAMEWORK])
            self.assertEqual(code, 0, buf.getvalue())
            r = support.run(proj, "tools/check_tree.py", cwd=proj)
            r2 = support.run(proj, "make-tree-view.py", cwd=proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 people, 0 families", r.stdout)
        self.assertEqual(r2.returncode, 0, r2.stderr)


if __name__ == "__main__":
    unittest.main()
