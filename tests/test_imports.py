"""Smoke tests for the screenshot_annotation core modules.

These are import-time smoke tests: each core module must import cleanly without
side effects. Per spec, any import-time network access or `sips`/system calls
must be guarded behind `main()` so that simply importing the module is safe.
"""

import importlib
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CORE_MODULES = [
    "backend",
    "frontend",
    "config_loader",
    "tracker",
    "work_common",
    "work1",
    "work2",
    "work3",
    "work4",
    "work5",
    "work6",
    "work7",
    "ner",
]


class TestCoreImports(unittest.TestCase):
    def test_core_modules_import(self):
        for name in CORE_MODULES:
            importlib.import_module(name)


class TestConfigTemplate(unittest.TestCase):
    def test_config_template_has_required_keys(self):
        path = os.path.join(REPO_ROOT, "config.template.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("ollama_base", "vision_model", "embed_model", "TAG_LIST"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
