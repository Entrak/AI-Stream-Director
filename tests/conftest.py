"""Pytest configuration for async test support."""

import pytest

# Explicitly enable pytest-asyncio
pytest_plugins = ('pytest_asyncio',)
