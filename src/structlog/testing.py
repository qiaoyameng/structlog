# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

"""
Helpers to test your application's logging behavior.

.. versionadded:: 20.1.0

See :doc:`testing`.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import Any, NamedTuple, NoReturn

from ._capture import _ACTIVE_CAPTURE, CaptureState
from ._config import configure, get_config
from ._log_levels import map_method_name
from .exceptions import DropEvent
from .typing import EventDict, Processor, WrappedLogger


__all__ = [
    "CapturedCall",
    "CapturingLogger",
    "CapturingLoggerFactory",
    "LogCapture",
    "ReturnLogger",
    "ReturnLoggerFactory",
    "capture_logs",
]


class LogCapture:
    """
    Class for capturing log messages in its entries list.
    Generally you should use `structlog.testing.capture_logs`,
    but you can use this class if you want to capture logs with other patterns.

    :ivar List[structlog.typing.EventDict] entries: The captured log entries.

    .. versionadded:: 20.1.0

    .. versionchanged:: 24.3.0
       Added mapping from "exception" to "error"
       Added mapping from "warn" to "warning"
    """

    entries: list[EventDict]

    def __init__(self) -> None:
        self.entries = []

    def __call__(
        self, _: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> NoReturn:
        event_dict["log_level"] = map_method_name(method_name)
        self.entries.append(event_dict)

        raise DropEvent


@contextmanager
def capture_logs(
    processors: Iterable[Processor] = (),
    *,
    local: bool = False,
) -> Generator[list[EventDict], None, None]:
    """
    Context manager that appends all logging statements to its yielded list
    while it is active. Disables all configured processors for the duration
    of the context manager.

    Attention: by default, this is **not** thread-safe! Pass *local* as True
    for a context-local capture that doesn't touch the global configuration.

    Args:
        processors: Processors to apply before the logs are captured.

        local:
            If True, don't modify the global configuration. Instead, only
            capture logs that are emitted from the current execution context
            (as defined by `contextvars`): the current code, and `asyncio`
            tasks that are created while the context manager is active.

            Other threads, and asyncio tasks that already exist when the
            context manager is entered, keep logging through the configured
            processor chain and are not captured. Likewise, once the context
            manager exits, tasks that outlive it resume logging through the
            configured chain and no longer append to the yielded list.

            Captures can be nested: only the innermost active capture collects
            events, and the outer capture resumes once the inner one exits.

            Unlike the default mode, this also works with bound loggers that
            have been created (and cached) before entering the context
            manager, because the capture is checked when the event is
            processed, not when the logger is built.

    .. versionadded:: 20.1.0
    .. versionadded:: 25.5.0 *processors* parameter
    .. versionadded:: 26.2.0 *local* parameter
    """
    cap = LogCapture()
    if local:
        # Carry the capture in a context variable that is checked when an
        # event is processed. The global configuration is never touched.
        state = CaptureState(processors=(*processors, cap))
        token = _ACTIVE_CAPTURE.set(state)
        try:
            yield cap.entries
        finally:
            # Deactivate first, so execution contexts that still reference
            # this state (e.g. asyncio tasks created within the block that
            # finish after it) fall back to the regular processor chain
            # instead of appending to the yielded list.
            state.active = False
            _ACTIVE_CAPTURE.reset(token)
        return

    # Modify `_Configuration.default_processors` set via `configure` but always
    # keep the list instance intact to not break references held by bound
    # loggers.
    configured_processors = get_config()["processors"]
    old_processors = configured_processors.copy()
    try:
        # clear processors list and use LogCapture for testing
        configured_processors.clear()
        configured_processors.extend(processors)
        configured_processors.append(cap)
        configure(processors=configured_processors)
        yield cap.entries
    finally:
        # remove LogCapture and restore original processors
        configured_processors.clear()
        configured_processors.extend(old_processors)
        configure(processors=configured_processors)


class ReturnLogger:
    """
    Return the arguments that it's called with.

    >>> from structlog import ReturnLogger
    >>> ReturnLogger().info("hello")
    'hello'
    >>> ReturnLogger().info("hello", when="again")
    (('hello',), {'when': 'again'})

    .. versionchanged:: 0.3.0
        Allow for arbitrary arguments and keyword arguments to be passed in.
    """

    def msg(self, *args: Any, **kw: Any) -> Any:
        """
        Return tuple of ``args, kw`` or just ``args[0]`` if only one arg passed
        """
        # Slightly convoluted for backwards compatibility.
        if len(args) == 1 and not kw:
            return args[0]

        return args, kw

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


class ReturnLoggerFactory:
    r"""
    Produce and cache `ReturnLogger`\ s.

    To be used with `structlog.configure`\ 's *logger_factory*.

    Positional arguments are silently ignored.

    .. versionadded:: 0.4.0
    """

    def __init__(self) -> None:
        self._logger = ReturnLogger()

    def __call__(self, *args: Any) -> ReturnLogger:
        return self._logger


class CapturedCall(NamedTuple):
    """
    A call as captured by `CapturingLogger`.

    Can also be unpacked like a tuple.

    Args:
        method_name: The method name that got called.

        args: A tuple of the positional arguments.

        kwargs: A dict of the keyword arguments.

    .. versionadded:: 20.2.0
    """

    method_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class CapturingLogger:
    """
    Store the method calls that it's been called with.

    This is nicer than `ReturnLogger` for unit tests because the bound logger
    doesn't have to cooperate.

    **Any** method name is supported.

    .. versionadded:: 20.2.0
    """

    calls: list[CapturedCall]

    def __init__(self) -> None:
        self.calls = []

    def __repr__(self) -> str:
        return f"<CapturingLogger with {len(self.calls)} call(s)>"

    def __getattr__(self, name: str) -> Any:
        """
        Capture call to `calls`
        """

        def log(*args: Any, **kw: Any) -> None:
            self.calls.append(CapturedCall(name, args, kw))

        return log


class CapturingLoggerFactory:
    r"""
    Produce and cache `CapturingLogger`\ s.

    Each factory produces and reuses only **one** logger.

    You can access it via the ``logger`` attribute.

    To be used with `structlog.configure`\ 's *logger_factory*.

    Positional arguments are silently ignored.

    .. versionadded:: 20.2.0
    """

    logger: CapturingLogger

    def __init__(self) -> None:
        self.logger = CapturingLogger()

    def __call__(self, *args: Any) -> CapturingLogger:
        return self.logger
