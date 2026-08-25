#!/usr/bin/env python3
"""Smoke-test the Syte import graph after the AI/provider removal."""

import importlib

module = importlib.import_module("syte.main")
assert getattr(module, "app", None) is not None
print("Syte application import succeeded")
