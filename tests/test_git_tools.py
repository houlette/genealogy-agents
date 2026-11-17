"""Tests for lint_prose.py, verify.py and the git hooks in tools/hooks/, each in a throwaway git repository."""
import os
import subprocess
import sys
import tempfile
import unittest

import support

HAS_PIL = True
try:
    import PIL  # noqa: F401
except ImportError:
    HAS_PIL = False


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Repo(unittest.TestCase):
    """A git repository holding a synced project (fixture tree, tools, hooks), with one commit."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.p = self.tmpdir.name
        support.make_project(self.p, images=(support.ERNST_IMAGE,) if HAS_PIL else ())
        support.install_framework(self.p)
        support.git_init(self.p)
        write(os.path.join(self.p, "notes.md"), "First line.\n")
        support.git(self.p, "add", "-A")
        support.git(self.p, "commit", "-q", "-m", "start", "--no-verify")

    def tearDown(self):
        self.tmpdir.cleanup()

    def lint(self, *args):
        return subprocess.run([sys.executable, "tools/lint_prose.py", *args], cwd=self.p,
                              env=support.env_for(self.p), capture_output=True, text=True)

    def commit(self, message="change"):
        support.git(self.p, "config", "core.hooksPath", "tools/hooks")
        r = subprocess.run(["git", "commit", "-m", message], cwd=self.p, env=support.env_for(self.p),
                           capture_output=True, text=True)
        # Git passes hook output through on stderr; read both streams as one.
        r.stdout = r.stdout + r.stderr
        return r


class LintProse(Repo):
    def test_clean_change(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nThe register probably names him.\n")
        r = self.lint()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("lint_prose: 0 flagged", r.stdout)

    def test_certainty_words_and_em_dash_in_added_lines_only(self):
        write(os.path.join(self.p, "notes.md"),
              "First line.\nThis proves the link — verified and RESOLVED.\nThe will was proved in 1801.\n")
        r = self.lint()
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes.md:2: certainty word 'proves'", r.stdout)
        self.assertIn("notes.md:2: certainty word 'verified'", r.stdout)
        self.assertIn("notes.md:2: certainty word 'RESOLVED'", r.stdout)
        self.assertIn("notes.md:2: em dash", r.stdout)
        self.assertNotIn("notes.md:3", r.stdout)  # "proved" (of a will) is allowed
        self.assertNotIn("notes.md:1", r.stdout)  # unchanged lines are not read

    def test_untracked_markdown_is_read_whole(self):
        write(os.path.join(self.p, "sources", "new.md"), "Line one.\nAn exhaustive search.\n")
        r = self.lint()
        self.assertIn("sources/new.md:2: certainty word 'exhaustive'", r.stdout)

    def test_other_file_types_are_ignored(self):
        write(os.path.join(self.p, "tools", "x.py"), "# this proves nothing\n")
        support.git(self.p, "add", "tools/x.py")
        self.assertEqual(self.lint().returncode, 0)

    def test_negative_result_marked_done_in_tasks(self):
        write(os.path.join(self.p, "tasks.md"), "| Task | Where | Suits | Status |\n")
        support.git(self.p, "add", "tasks.md")
        support.git(self.p, "commit", "-q", "-m", "tasks", "--no-verify")
        write(os.path.join(self.p, "tasks.md"), "| Task | Where | Suits | Status |\n"
              "| Search 1850 | FS | any | done (abc123), not found in the register |\n"
              "| Search 1860 | FS | any | for review (Gemini), not found |\n")
        r = self.lint()
        self.assertIn("tasks.md:2: negative result marked done", r.stdout)
        self.assertNotIn("tasks.md:3", r.stdout)

    def test_rev_range(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nDefinitively his.\n")
        support.git(self.p, "commit", "-q", "-am", "x", "--no-verify")
        r = self.lint("--rev", "HEAD~1..HEAD")
        self.assertIn("notes.md:2: certainty word 'Definitively'", r.stdout)

    def test_message_options(self):
        msg = os.path.join(self.p, "msg.txt")
        write(msg, "Add the link\n\nThis is conclusive.\n")
        r = self.lint("--only-message", msg)
        self.assertIn("commit message:3: certainty word 'conclusive'", r.stdout)
        write(os.path.join(self.p, "notes.md"), "First line.\nProof here.\n")
        r = self.lint("--message", msg)
        self.assertIn("notes.md:2", r.stdout)
        self.assertIn("commit message:3", r.stdout)


class Verify(Repo):
    def run_verify(self, *args):
        return subprocess.run([sys.executable, "tools/verify.py", *args], cwd=self.p,
                              env=support.env_for(self.p), capture_output=True, text=True)

    @unittest.skipUnless(HAS_PIL, "Pillow needed for the fixture image")
    def test_all_steps_pass(self):
        r = self.run_verify()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("0 errors", r.stdout)
        self.assertIn("tree-view.html: 11 people", r.stdout)
        self.assertIn("verify: all checks passed cleanly.", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.p, "tree-view.html")))

    def test_failures_are_reported(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nThis is verified.\n")
        with open(os.path.join(self.p, "tree.ged"), "a", encoding="utf-8") as f:
            f.write("0 @I60@ INDI\n1 NAME Stray /One/\n")
        r = self.run_verify()
        self.assertEqual(r.returncode, 1)
        self.assertIn("[FAIL] check_tree.py", r.stdout)
        self.assertIn("[FAIL] lint_prose.py", r.stdout)

    def test_arguments_are_forwarded_with_their_values(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nProven.\n")
        support.git(self.p, "commit", "-q", "-am", "x", "--no-verify")
        r = self.run_verify("--sources", "--rev", "HEAD~1..HEAD")
        self.assertIn("Dated facts without a citation:", r.stdout)
        self.assertIn("notes.md:2: certainty word 'Proven'", r.stdout)


class Hooks(Repo):
    def test_clean_commit_passes(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nSecond line.\n")
        support.git(self.p, "add", "notes.md")
        r = self.commit()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_tree_errors_block_the_commit_and_keep_the_work(self):
        tree = os.path.join(self.p, "tree.ged")
        with open(tree, encoding="utf-8") as f:
            text = f.read()
        write(tree, text.replace("0 TRLR", "0 @I70@ INDI\n1 NAME Stray /One/\n0 TRLR"))
        support.git(self.p, "add", "tree.ged")
        r = self.commit()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ERROR @I70@ is not connected", r.stdout)
        self.assertIn("Commit blocked", r.stdout)
        self.assertIn("tree.ged", support.git(self.p, "diff", "--cached", "--name-only").stdout)
        self.assertFalse(os.path.exists(os.path.join(self.p, ".tree-staged.ged")))

    def test_hook_checks_the_staged_tree_not_the_working_copy(self):
        tree = os.path.join(self.p, "tree.ged")
        with open(tree, encoding="utf-8") as f:
            good = f.read()
        write(tree, good.replace("0 TRLR", "0 @I71@ INDI\n1 NAME Stray /One/\n0 TRLR"))  # unstaged error
        write(os.path.join(self.p, "notes.md"), "First line.\nok\n")
        support.git(self.p, "add", "notes.md")
        self.assertEqual(self.commit().returncode, 0)

    @unittest.skipUnless(HAS_PIL, "Pillow needed")
    def test_unstaged_cited_file_is_noted(self):
        support.save_image(os.path.join(self.p, "sources", "1900-new.jpg"))
        tree = os.path.join(self.p, "tree.ged")
        with open(tree, encoding="utf-8") as f:
            text = f.read()
        write(tree, text.replace("1 NAME Katharina /Ebert/", "1 NAME Katharina /Ebert/\n1 NOTE See sources/1900-new.jpg"))
        support.git(self.p, "add", "tree.ged")
        r = self.commit()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("(note) tree.ged cites sources/1900-new.jpg, which is not committed or staged", r.stdout)

    def test_evidence_and_lint_notes_do_not_block(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nThis proves it.\n")
        support.git(self.p, "add", "notes.md")
        r = self.commit("Prove the link — conclusively")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(note) lint_prose:", r.stdout)
        out = r.stdout + r.stderr
        self.assertIn("(note) commit message:", out)
        self.assertIn("em dash", out)

    def test_co_authored_by_line_is_not_linted(self):
        write(os.path.join(self.p, "notes.md"), "First line.\nfine\n")
        support.git(self.p, "add", "notes.md")
        r = self.commit("Add a note\n\nCo-Authored-By: Verified Bot <bot@example.invalid>")
        self.assertNotIn("(note) commit message", r.stdout + r.stderr)


class LintProseNewProject(unittest.TestCase):
    """A project just made by framework_sync init: no commits yet, framework text untouched."""

    def setUp(self):
        import framework_sync
        self.tmpdir = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmpdir.name, "proj")
        with open(os.devnull, "w") as devnull:
            import contextlib
            with contextlib.redirect_stdout(devnull):
                framework_sync.main(["init", self.p, "--framework", support.FRAMEWORK])
        support.git_init(self.p)

    def tearDown(self):
        self.tmpdir.cleanup()

    def lint(self, *args):
        return subprocess.run([sys.executable, "tools/lint_prose.py", *args], cwd=self.p,
                              env=support.env_for(self.p), capture_output=True, text=True)

    def test_no_commits_and_untouched_framework_text_pass(self):
        r = self.lint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lint_prose: 0 flagged", r.stdout)

    def test_project_prose_is_still_checked_before_the_first_commit(self):
        write(os.path.join(self.p, "PROJECT.md"), "This proves it.\n")
        r = self.lint()
        self.assertIn("PROJECT.md:1: certainty word 'proves'", r.stdout)

    def test_edited_framework_file_is_checked(self):
        with open(os.path.join(self.p, "rules-browser.md"), "a", encoding="utf-8") as f:
            f.write("A verified rule.\n")
        r = self.lint()
        self.assertIn("rules-browser.md", r.stdout)
        self.assertIn("certainty word 'verified'", r.stdout)

    def test_staged_check_in_a_repository_without_commits(self):
        support.git(self.p, "add", "-A")
        r = self.lint("--rev", "--cached")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_verify_passes_on_a_fresh_project(self):
        r = subprocess.run([sys.executable, "tools/verify.py"], cwd=self.p, env=support.env_for(self.p),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("verify: all checks passed cleanly.", r.stdout)


if __name__ == "__main__":
    unittest.main()
