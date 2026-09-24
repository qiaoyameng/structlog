# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

import asyncio
import threading

import pytest

import structlog

from structlog import contextvars, get_config, get_logger, testing
from structlog.exceptions import DropEvent
from structlog.testing import (
    CapturedCall,
    CapturingLogger,
    CapturingLoggerFactory,
    LogCapture,
    ReturnLogger,
    ReturnLoggerFactory,
)


class TestCaptureLogs:
    def test_captures_logs(self):
        """
        Log entries are captured and retain their structure.
        """
        with testing.capture_logs() as logs:
            get_logger().bind(x="y").info("hello", answer=42)
            get_logger().bind(a="b").info("goodbye", foo={"bar": "baz"})
        assert [
            {"event": "hello", "log_level": "info", "x": "y", "answer": 42},
            {
                "a": "b",
                "event": "goodbye",
                "log_level": "info",
                "foo": {"bar": "baz"},
            },
        ] == logs

    def get_active_procs(self):
        return get_config()["processors"]

    def test_restores_processors_on_success(self):
        """
        Processors are patched within the contextmanager and restored on
        exit.
        """
        orig_procs = self.get_active_procs()
        assert len(orig_procs) > 1

        with testing.capture_logs():
            modified_procs = self.get_active_procs()
            assert len(modified_procs) == 1
            assert isinstance(modified_procs[0], LogCapture)

        restored_procs = self.get_active_procs()
        assert orig_procs is restored_procs
        assert len(restored_procs) > 1

    def test_uses_processors_arg_and_restores_on_success(self):
        """
        Processors passed with `processors` arg are active only until context
        exits.
        """
        orig_procs = self.get_active_procs()

        assert len(orig_procs) > 1

        with testing.capture_logs(processors=[contextvars.merge_contextvars]):
            modified_procs = self.get_active_procs()

            assert len(modified_procs) == 2
            assert contextvars.merge_contextvars == modified_procs[0]
            assert isinstance(modified_procs[1], LogCapture)
            assert len(modified_procs) == 2
            assert contextvars.merge_contextvars == modified_procs[0]
            assert isinstance(modified_procs[1], LogCapture)

        restored_procs = self.get_active_procs()

        assert orig_procs is restored_procs
        assert len(restored_procs) > 1

    def test_restores_processors_on_error(self):
        """
        Processors are restored even on errors.
        """
        orig_procs = self.get_active_procs()

        with pytest.raises(NotImplementedError), testing.capture_logs():
            raise NotImplementedError("from test")

        assert orig_procs is self.get_active_procs()

    def test_captures_bound_logers(self):
        """
        Even logs from already bound loggers are captured and their processors
        restored on exit.
        """
        logger = get_logger("bound").bind(foo="bar")
        logger.info("ensure logger is bound")

        with testing.capture_logs() as logs:
            logger.info("hello", answer=42)

        assert logs == [
            {
                "event": "hello",
                "answer": 42,
                "foo": "bar",
                "log_level": "info",
            }
        ]

    def test_captures_log_level_mapping(self):
        """
        exceptions and warn log levels are mapped like in regular loggers.
        """
        structlog.configure(
            processors=[
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
        )
        with testing.capture_logs() as logs:
            get_logger().exception("hello", answer=42)
            get_logger().warn("again", answer=23)

        assert [
            {
                "event": "hello",
                "answer": 42,
                "exc_info": True,
                "log_level": "error",
            },
            {
                "answer": 23,
                "event": "again",
                "log_level": "warning",
            },
        ] == logs


class _Sink:
    """
    A normal processor chain whose final processor records events instead of
    rendering them -- stands in for the application's regular log sink.
    """

    def __init__(self):
        self.entries = []

    def configure(self, **overrides):
        def mark_global(logger, method_name, event_dict):
            event_dict["global_processor"] = True
            return event_dict

        def sink_processor(logger, method_name, event_dict):
            self.entries.append(dict(event_dict))
            raise DropEvent

        structlog.configure(
            processors=[mark_global, sink_processor],
            logger_factory=CapturingLoggerFactory(),
            cache_logger_on_first_use=True,
            **overrides,
        )

    def events(self):
        return [entry["event"] for entry in self.entries]


class _AsyncBarrier:
    """
    A reusable-less asyncio barrier for a fixed number of parties.
    """

    def __init__(self, parties):
        self._parties = parties
        self._arrived = 0
        self._event = asyncio.Event()

    async def wait(self):
        self._arrived += 1
        if self._arrived >= self._parties:
            self._event.set()
        await self._event.wait()


class TestCaptureLogsLocal:
    @pytest.fixture
    def sink(self):
        sink = _Sink()
        sink.configure()

        return sink

    def test_captures_without_touching_global_config(self, sink):
        """
        Events inside the block are captured and never reach the normal sink;
        events outside go to the sink. The global processor list is identical
        before, during, and after the capture.
        """
        procs_before = get_config()["processors"]
        procs_snapshot = procs_before.copy()

        get_logger().info("before")

        with testing.capture_logs(local=True) as logs:
            assert procs_before is get_config()["processors"]
            assert procs_snapshot == get_config()["processors"]

            get_logger().info("inside")

        get_logger().info("after")

        assert procs_before is get_config()["processors"]
        assert procs_snapshot == get_config()["processors"]

        # The captured event did not pass through the global processors.
        assert [{"event": "inside", "log_level": "info"}] == logs
        # ...and never reached the sink, while outside events did.
        assert ["before", "after"] == sink.events()
        assert all(e["global_processor"] for e in sink.entries)

    def test_processors_argument_applies_to_captured_only(self, sink):
        """
        Processors passed to capture_logs run for captured events instead of
        the globally configured ones.
        """

        def add_local(logger, method_name, event_dict):
            event_dict["local_processor"] = True
            return event_dict

        with testing.capture_logs(processors=[add_local], local=True) as logs:
            get_logger().info("inside")

        get_logger().info("outside")

        assert [
            {
                "event": "inside",
                "local_processor": True,
                "log_level": "info",
            }
        ] == logs
        assert [{"event": "outside", "global_processor": True}] == sink.entries

    def test_cached_bound_logger(self, sink):
        """
        Bound loggers created and cached before the capture are captured too.
        """
        logger = get_logger("cached").bind(x="y")
        logger.info("prime-the-cache")

        with testing.capture_logs(local=True) as logs:
            logger.info("cached")

        logger.info("back-to-normal")

        assert [
            {"event": "cached", "log_level": "info", "x": "y"}
        ] == logs
        assert ["prime-the-cache", "back-to-normal"] == sink.events()

    def test_restores_on_error(self, sink):
        """
        After an exception inside the block, logging goes back to the sink.
        """
        with pytest.raises(ValueError), testing.capture_logs(local=True) as logs:
            get_logger().info("inside")
            raise ValueError("boom")

        get_logger().info("after")

        assert ["inside"] == [e["event"] for e in logs]
        assert ["after"] == sink.events()

    def test_nested_captures(self, sink):
        """
        Only the innermost capture collects; the outer resumes after the
        inner exits, also on errors.
        """
        with testing.capture_logs(local=True) as outer:
            get_logger().info("outer-1")

            with testing.capture_logs(local=True) as inner:
                get_logger().info("inner")

            get_logger().info("outer-2")

            with pytest.raises(ValueError), testing.capture_logs(local=True):
                get_logger().info("inner-error")
                raise ValueError("boom")

            get_logger().info("outer-3")

        get_logger().info("outside")

        assert ["inner"] == [e["event"] for e in inner]
        assert ["outer-1", "outer-2", "outer-3"] == [
            e["event"] for e in outer
        ]
        assert ["outside"] == sink.events()

    async def test_concurrent_tasks(self, sink):
        """
        A task that already exists when the capture starts is not captured;
        the capturing task and the other task run truly concurrently.
        """
        barrier = _AsyncBarrier(2)

        async def other_task():
            await barrier.wait()
            get_logger().info("other-task")
            await barrier.wait()

        task = asyncio.create_task(other_task())
        try:
            with testing.capture_logs(local=True) as logs:
                await barrier.wait()
                get_logger().info("captured")
                await barrier.wait()
        finally:
            await task

        assert ["captured"] == [e["event"] for e in logs]
        assert ["other-task"] == sink.events()

    async def test_child_task_inherits_but_stops_at_exit(self, sink):
        """
        A task created inside the block is captured, but once the block
        exits, a late-finishing child logs to the normal sink again instead
        of appending to the returned list.
        """
        barrier = _AsyncBarrier(2)
        release = asyncio.Event()

        async def child():
            get_logger().info("child-inside")
            # Wait until the parent has left the capture block.
            await barrier.wait()
            await release.wait()
            get_logger().info("child-late")

        with testing.capture_logs(local=True) as logs:
            task = asyncio.create_task(child())
            get_logger().info("parent")
            # The barrier is released after the child logged "child-inside";
            # the parent then exits the block before releasing the child.
            await barrier.wait()

        release.set()
        await task

        assert ["parent", "child-inside"] == [e["event"] for e in logs]
        assert ["child-late"] == sink.events()

    def test_other_thread_not_captured(self, sink):
        """
        Threads don't inherit the capture; their logs go to the normal sink
        even while the capture is active in the main thread.
        """
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            get_logger().info("from-thread")
            barrier.wait()

        thread = threading.Thread(target=worker)
        with testing.capture_logs(local=True) as logs:
            thread.start()
            barrier.wait()
            get_logger().info("from-main")
            barrier.wait()
            thread.join()

        assert ["from-main"] == [e["event"] for e in logs]
        assert ["from-thread"] == sink.events()

    @pytest.mark.parametrize("wrapper", ["native", "stdlib"])
    async def test_sync_and_async_methods(self, sink, wrapper):
        """
        Native (filtering) and stdlib bound loggers are captured by both
        their sync and async methods. The async hand-off to the executor
        carries the capture context along.
        """
        if wrapper == "native":
            sink.configure(
                wrapper_class=structlog.make_filtering_bound_logger("info")
            )
        else:
            sink.configure(wrapper_class=structlog.stdlib.BoundLogger)

        logger = get_logger(wrapper).bind(x="y")
        logger.info("prime-the-cache")

        with testing.capture_logs(local=True) as logs:
            logger.info("sync")
            await logger.ainfo("async")

        logger.info("after")

        assert ["sync", "async"] == [e["event"] for e in logs]
        assert all(e["x"] == "y" for e in logs)
        assert ["prime-the-cache", "after"] == sink.events()


class TestReturnLogger:
    # @pytest.mark.parametrize("method", stdlib_log_methods)
    def test_stdlib_methods_support(self, stdlib_log_method):
        """
        ReturnLogger implements methods of stdlib loggers.
        """
        v = getattr(ReturnLogger(), stdlib_log_method)("hello")

        assert "hello" == v

    def test_return_logger(self):
        """
        Return logger returns exactly what's sent in.
        """
        obj = ["hello"]

        assert obj is ReturnLogger().msg(obj)


class TestReturnLoggerFactory:
    def test_builds_returnloggers(self):
        """
        Factory returns ReturnLoggers.
        """
        f = ReturnLoggerFactory()

        assert isinstance(f(), ReturnLogger)

    def test_caches(self):
        """
        There's no need to have several loggers so we return the same one on
        each call.
        """
        f = ReturnLoggerFactory()

        assert f() is f()

    def test_ignores_args(self):
        """
        ReturnLogger doesn't take positional arguments.  If any are passed to
        the factory, they are not passed to the logger.
        """
        ReturnLoggerFactory()(1, 2, 3)


class TestCapturingLogger:
    def test_factory_caches(self):
        """
        CapturingLoggerFactory returns one CapturingLogger over and over again.
        """
        clf = CapturingLoggerFactory()
        cl1 = clf()
        cl2 = clf()

        assert cl1 is cl2

    def test_repr(self):
        """
        repr says how many calls there were.
        """
        cl = CapturingLogger()

        cl.info("hi")
        cl.error("yolo")

        assert "<CapturingLogger with 2 call(s)>" == repr(cl)

    def test_captures(self):
        """
        All calls to all names are captured.
        """
        cl = CapturingLogger()

        cl.info("hi", val=42)
        cl.trololo("yolo", foo={"bar": "baz"})

        assert [
            CapturedCall(method_name="info", args=("hi",), kwargs={"val": 42}),
            CapturedCall(
                method_name="trololo",
                args=("yolo",),
                kwargs={"foo": {"bar": "baz"}},
            ),
        ] == cl.calls
