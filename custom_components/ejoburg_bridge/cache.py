"""Helpers for safely caching downloaded documents."""

from __future__ import annotations

import os
import tempfile


def is_valid_pdf(path: str) -> bool:
    """Return whether a cached file starts with the PDF signature."""
    try:
        with open(path, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def write_pdf_atomically(path: str, contents: bytes) -> None:
    """Validate and atomically replace a cached PDF."""
    if not contents.startswith(b"%PDF-"):
        raise ValueError("Statement response was not a valid PDF")
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".statement-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
