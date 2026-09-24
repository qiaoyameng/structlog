# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

"""
Context-local log capture state.

Used by `structlog.testing.capture_logs` in its *local* mode to capture log
events without touching the global configuration.

The state is carried by a `contextvars.ContextVar`, so it follows the
execution context: `asyncio` tasks created while a capture is active inherit
it, threads don't, and async log methods that hand off to an executor carry it
along via `contextvars.copy_context()`.
"""

from __future__ import annotations

import contextvars

from dataclasses import dataclass, field

from .typing import Processor


@dataclass
class CaptureState:
    """
    An active local capture context.

    ``processors`` is the full replacement processor chain (including the
    final capturing processor). ``active`` is flipped to ``False`` when the
    capturing context manager exits, so execution contexts that still carry a
    reference (e.g. late-finishing asyncio tasks) fall back to the regular
    processor chain instead of appending to a list that has already been
    handed back to the caller.
    """

    processors: tuple[Processor, ...]
    active: bool = field(default=True)


_ACTIVE_CAPTURE: contextvars.ContextVar[CaptureState | None] = (
    contextvars.ContextVar("structlog_local_capture", default=None)
)
