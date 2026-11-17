#!/usr/bin/env python3
"""Run every unit test: tests/ (the tree tools, model tools, hooks, sync) and
tools/browser_bridge/ (the bridge). No network, browser or paid API is used.

    python3 tools/run_tests.py        # quiet
    python3 tools/run_tests.py -v     # one line per test

Exit status 0 when every test passes. The live bridge test
(tools/browser_bridge/live_two_agents.py) needs a browser and is not run here.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    verbosity = 2 if "-v" in sys.argv[1:] else 1
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for start in ("tests", os.path.join("tools", "browser_bridge")):
        path = os.path.join(ROOT, start)
        if os.path.isdir(path):
            suite.addTests(loader.discover(path, pattern="test_*.py", top_level_dir=path))
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
