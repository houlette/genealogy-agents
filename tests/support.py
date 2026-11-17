"""Shared helpers for the tests of the tree tools, hooks and sync.

Each test builds a throwaway project in a temporary folder (a copy of the
fictional tree in tests/fixtures/sample-tree.ged, a project.json, and any
images it needs) and runs the framework's tools against it as subprocesses,
with GENEALOGY_PROJECT pointing at the project, exactly as an agent would.
"""
import json
import os
import shutil
import subprocess
import sys

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(FRAMEWORK, "tools")
FIXTURE = os.path.join(FRAMEWORK, "tests", "fixtures", "sample-tree.ged")
ERNST_IMAGE = "sources/1930-02-02-birth-Musterdorf-Ernst-Adler.jpg"

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def fixture_text():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def save_image(path, size=(1200, 800)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, "white").save(path)


def make_project(tmp, tree=None, config=None, images=(ERNST_IMAGE,), image_size=(1200, 800)):
    """Create a project in tmp and return its path. tree defaults to the fixture."""
    os.makedirs(os.path.join(tmp, "sources"), exist_ok=True)
    with open(os.path.join(tmp, "tree.ged"), "w", encoding="utf-8") as f:
        f.write(fixture_text() if tree is None else tree)
    if config is not None:
        with open(os.path.join(tmp, "project.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)
    for rel in images:
        save_image(os.path.join(tmp, rel), image_size)
    return tmp


def env_for(project, **extra):
    env = dict(os.environ)
    env["GENEALOGY_PROJECT"] = project
    env.update(extra)
    return env


def run(project, script, *args, cwd=None, **extra_env):
    """Run a framework script (path relative to the framework) against project."""
    return subprocess.run([sys.executable, os.path.join(FRAMEWORK, script), *args],
                          cwd=cwd or project, env=env_for(project, **extra_env),
                          capture_output=True, text=True, timeout=120)


def git(path, *args, check=True):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=check)


def git_init(path):
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")


def install_framework(project):
    """Copy the tools, hooks, viewer template and builder into project (as a synced project has them)."""
    shutil.copytree(TOOLS, os.path.join(project, "tools"),
                    ignore=shutil.ignore_patterns("__pycache__", "claims*.json", "bakeoff"), dirs_exist_ok=True)
    for name in ("make-tree-view.py", "tree-view.template.html"):
        shutil.copy2(os.path.join(FRAMEWORK, name), project)
