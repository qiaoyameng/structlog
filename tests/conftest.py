# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

import asyncio
import logging
import os

from asyncio.selector_events import BaseSelectorEventLoop
from io import StringIO

import pytest

import structlog

from structlog._log_levels import NAME_TO_LEVEL
from structlog.testing import CapturingLogger


try:
    import twisted
except ImportError:
    twisted = None

LOGGER = logging.getLogger()


@pytest.fixture(autouse=True)
def _ensure_logging_framework_not_altered():
    """
    Prevents 'ValueError: I/O operation on closed file.' errors.
    """
    before_handlers = list(LOGGER.handlers)
    before_level = LOGGER.level

    yield

    LOGGER.handlers = before_handlers
    LOGGER.setLevel(before_level)


@pytest.fixture(name="sio")
def _sio():
    """
    A new StringIO instance.
    """
    return StringIO()


@pytest.fixture(name="event_dict")
def _event_dict():
    """
    An example event dictionary with multiple value types w/o the event itself.
    """

    class A:
        def __repr__(self):
            return r"<A(\o/)>"

    return {"a": A(), "b": [3, 4], "x": 7, "y": "test", "z": (1, 2)}


@pytest.fixture(
    name="stdlib_log_method",
    params=[m for m in NAME_TO_LEVEL if m != "notset"],
)
def _stdlib_log_methods(request):
    return request.param


@pytest.fixture(name="cl")
def _cl():
    return CapturingLogger()


@pytest.fixture(autouse=True)
def _reset_config():
    structlog.reset_defaults()


@pytest.fixture(autouse=True, scope="session")
def _workaround_broken_self_pipe():
    """
    Some sandboxed environments block the send() syscall on socket pairs
    with EPERM, which silently breaks asyncio's cross-thread loop wakeup
    that run_in_executor() and shutdown_default_executor() rely on.
    os.write() on the same socket fd is not blocked and behaves the same
    for the non-blocking self-pipe.
    """
    if os.name == "nt":
        yield

        return

    orig_write_to_self = BaseSelectorEventLoop._write_to_self

    def _write_to_self(self):
        csock = self._csock
        if csock is None:
            return

        try:
            os.write(csock.fileno(), b"\0")
        except OSError:
            pass

    BaseSelectorEventLoop._write_to_self = _write_to_self
    try:
        yield
    finally:
        BaseSelectorEventLoop._write_to_self = orig_write_to_self
