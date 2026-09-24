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

from ._base import _LOCAL_LOG_CAPTURE
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


class _LocalLogCapture:
    """
    State of one local `capture_logs` context.

    Carried by a context variable, so it is visible to the current execution
    context and any asyncio tasks created within it, but not to other
    threads or to tasks that were created outside of it.

    Once the owning context manager exits, ``active`` is set to `False` and
    execution contexts that still reference this object (for example,
    asyncio tasks that outlive the context manager) fall back to their
    regular processor chain.

    .. versionadded:: 26.2.0
    """

    __slots__ = ("active", "processors")

    processors: list[Processor]
    active: bool

    def __init__(self, processors: list[Processor]) -> None:
        self.processors = processors
        self.active = True


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

    By default, this works by replacing the global processor chain, which
    affects the whole process.  Pass ``local=True`` to capture only log
    events from the current execution context instead, without touching the
    global configuration.

    Args:
        processors: Processors to apply before the logs are captured.

        local:
            If `True`, capture only events that are logged in the current
            execution context (and asyncio tasks created within it) and
            leave the global configuration untouched.

            In local mode:

            - The global processor chain is never modified, so it is safe
              to use in concurrently running tests.  Loggers from other
              threads, or from asyncio tasks that existed before the block,
              keep using the regular configuration.
            - Bound loggers that were created and cached before entering
              the block are captured too, because the capture is looked up
              when the event is processed, not when the logger is built.
            - Asyncio tasks created within the block inherit the capture.
              Once the block exits, tasks that are still running stop
              capturing and resume regular logging; they never append to
              the already-returned list.
            - Captures can be nested; only the innermost active capture
              collects an event.

    .. versionadded:: 20.1.0
    .. versionadded:: 25.5.0 *processors* parameter
    .. versionadded:: 26.2.0 *local* parameter

    .. attention::
       The default (``local=False``) mode is **not** thread-safe!
    """
    cap = LogCapture()

    if local:
        capture = _LocalLogCapture([*processors, cap])
        token = _LOCAL_LOG_CAPTURE.set(capture)
        try:
            yield cap.entries
        finally:
            # Deactivate before resetting, so execution contexts that still
            # carry this capture (e.g. asyncio tasks that outlive the block)
            # resume regular logging instead of appending to the returned
            # list.
            capture.active = False
            _LOCAL_LOG_CAPTURE.reset(token)

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
