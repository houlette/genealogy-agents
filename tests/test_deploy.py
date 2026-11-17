"""Tests of family-app/deploy.sh. rsync, ssh and curl are replaced by small
scripts on PATH that record their arguments, so nothing leaves the machine."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import support
from test_build_site import HAVE_DEPS, site_project

FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
case "$(basename "$0")" in
  rsync) case "$*" in *itemize*) [ -n "$FAKE_CHANGED" ] && echo ">f+++++++++ app.py" ;; esac ;;
  curl) printf "%s" "${FAKE_STATUS:-200}" ;;
esac
exit 0
"""


@unittest.skipUnless(HAVE_DEPS and shutil.which("sh"), "needs sh and the markdown and Pillow packages")
class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = site_project(os.path.join(self.tmp.name, "p"))
        support.install_framework(self.p)
        shutil.copytree(os.path.join(support.FRAMEWORK, "family-app"), os.path.join(self.p, "family-app"),
                        ignore=shutil.ignore_patterns("__pycache__", "data"))
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        for name in ("rsync", "ssh", "curl"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(FAKE)
            os.chmod(path, 0o755)
        self.log = os.path.join(self.tmp.name, "log")

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, deploy):
        with open(os.path.join(self.p, "site-src", "site.json"), "w", encoding="utf-8") as f:
            json.dump({"deploy": deploy}, f)

    def deploy(self, *args, **env):
        e = support.env_for(self.p, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_LOG=self.log, DEPLOY_WAIT="0", **env)
        r = subprocess.run(["sh", os.path.join(self.p, "family-app", "deploy.sh"), *args], cwd=self.tmp.name,
                           env=e, capture_output=True, text=True, timeout=120)
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = f.read().splitlines()
        return r, calls

    def test_no_host_is_an_error_and_nothing_runs(self):
        r, calls = self.deploy()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no server", r.stderr)
        self.assertEqual(calls, [])

    def test_site_only_deploy(self):
        self.settings({"ssh_host": "user@host", "remote_dir": "/srv/fam", "url": "https://family.example.com/"})
        r, calls = self.deploy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(calls[0], "rsync -az --delete site/ user@host:/srv/fam/site/")
        self.assertEqual(calls[1], "rsync -az --delete private/ user@host:/srv/fam/private/")
        self.assertIn("--exclude family.env", calls[2])
        self.assertIn("--exclude data/", calls[2])
        self.assertTrue(calls[2].endswith("family-app/ user@host:/srv/fam/app/"))
        self.assertFalse(any(c.startswith("ssh") for c in calls))
        self.assertIn("App code unchanged", r.stdout)
        self.assertIn("https://family.example.com/signin", calls[-1])
        self.assertIn("Sign-in page: HTTP 200", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.p, "site", "data", "public.json")))

    def test_changed_app_is_rebuilt_with_the_env_file(self):
        self.settings({"ssh_host": "user@host", "env_file": "/etc/other.env"})
        r, calls = self.deploy(FAKE_CHANGED="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ssh = [c for c in calls if c.startswith("ssh")]
        self.assertEqual(ssh[0], "ssh user@host cd family/app && docker compose --env-file /etc/other.env up -d --build")
        self.assertIn("docker ps", ssh[1])
        self.assertIn("No deploy.url", r.stdout)
        self.assertFalse(any(c.startswith("curl") for c in calls))

    def test_argument_overrides_the_host(self):
        self.settings({"ssh_host": "user@host"})
        r, calls = self.deploy("other@elsewhere")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("other@elsewhere:family/site/", calls[0])

    def test_failed_sign_in_check_fails_the_deploy(self):
        self.settings({"ssh_host": "user@host", "url": "https://family.example.com"})
        r, _ = self.deploy(FAKE_STATUS="502")
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not answer 200", r.stdout)

    def test_build_failure_stops_before_copying(self):
        self.settings({"ssh_host": "user@host"})
        with open(os.path.join(self.p, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"root": "@I999@"}, f)
        r, calls = self.deploy()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
