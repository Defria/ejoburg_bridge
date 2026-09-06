"""Tests for private statement cache helpers."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

CACHE_PATH = Path(__file__).parents[1] / "custom_components/ejoburg_bridge/cache.py"
SPEC = importlib.util.spec_from_file_location("ejoburg_bridge_cache", CACHE_PATH)
assert SPEC and SPEC.loader
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)


class StatementCacheTests(unittest.TestCase):
    def test_atomic_writer_accepts_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "statement.pdf")
            CACHE.write_pdf_atomically(path, b"%PDF-1.7\nbody")

            self.assertTrue(CACHE.is_valid_pdf(path))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"%PDF-1.7\nbody")

    def test_atomic_writer_rejects_non_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "statement.pdf")
            with self.assertRaises(ValueError):
                CACHE.write_pdf_atomically(path, b"<html>login</html>")
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
