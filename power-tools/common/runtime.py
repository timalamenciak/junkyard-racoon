#!/usr/bin/env python3
"""Shared runtime helpers for scheduled scripts."""

from __future__ import annotations

import os
import sys


TEST_MODE_ENV = "POWER_TOOLS_TEST_MODE"
TRUE_VALUES = {"1", "true", "yes", "on"}


def is_test_mode(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv[1:]
    if "--test" in args:
        return True
    return os.environ.get(TEST_MODE_ENV, "").strip().lower() in TRUE_VALUES
