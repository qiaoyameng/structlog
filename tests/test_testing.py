# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

import asyncio
import threading

import pytest

import structlog

from structlog import contextvars, get_config, get_logger, testing
from structlog.testing import (
    CapturedCall,
    CapturingLogger,
    CapturingLoggerFactory,
    LogCapture,
    ReturnLogger,
    ReturnLoggerFactory,
)


class _AsyncBarrier:
    """
    A minimal asyncio barrier that also works on Python 3.10, where
    `asyncio.Barrier` is not available yet.
    """

    def __init__(self, parties):
        self._parties = parties
        self._arrived = 0
        self._event = asyncio.Event()

    async def wait(self):
        self._arrived += 1
        if self._arrived == self._parties:
            self._event.set()

        await self._event.wait()


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


def _configure_for_local_capture(wrapper_class):
    """
    Configure structlog with a CapturingLoggerFactory as the sink and return
    the CapturingLogger that receives all regularly logged events.
    """
    clf = CapturingLoggerFactory()
    structlog.configure(
        processors=[structlog.processors.add_log_level],
        logger_factory=clf,
        wrapper_class=wrapper_class,
        cache_logger_on_first_use=True,
    )

    return clf.logger


@pytest.fixture(name="sink")
def _sink():
    """
    A CapturingLogger that receives everything that is logged through the
    regular (non-captured) processor chain of a natively configured
    structlog.
    """
    return _configure_for_local_capture(
        structlog.make_filtering_bound_logger(0)
    )


def _sink_events(sink):
    return [call.kwargs["event"] for call in sink.calls]


class TestCaptureLogsLocal:
    def test_does_not_touch_global_configuration(self, sink):
        """
        In local mode, the global processor chain is identical before,
        during, and after the capture.
        """
        procs_before = get_config()["processors"]

        with testing.capture_logs(local=True) as logs:
            assert get_config()["processors"] is procs_before

            get_logger().info("hello", answer=42)

            assert get_config()["processors"] is procs_before

        assert get_config()["processors"] is procs_before
        assert [
            {"event": "hello", "answer": 42, "log_level": "info"}
        ] == logs
        assert [] == sink.calls

    def test_applies_given_processors(self, sink):
        """
        Captured events pass through the processors given to capture_logs,
        not through the globally configured ones.
        """

        def add_answer(logger, method_name, event_dict):
            return {**event_dict, "answer": 42}

        with testing.capture_logs(processors=[add_answer], local=True) as logs:
            get_logger().info("hello")

        assert [{"event": "hello", "answer": 42, "log_level": "info"}] == logs
        assert [] == sink.calls

    def test_captures_cached_bound_logger(self, sink):
        """
        Bound loggers that were created and cached before the capture are
        captured in local mode.
        """
        logger = get_logger().bind(foo="bar")
        logger.info("before")  # binds & caches the logger

        with testing.capture_logs(local=True) as logs:
            logger.info("during")

        logger.info("after")

        assert ["during"] == [e["event"] for e in logs]
        assert ["before", "after"] == _sink_events(sink)

    async def test_concurrent_tasks_are_isolated(self, sink):
        """
        A task that was created outside of the capture keeps logging to the
        regular sink while the capturing task's events are collected.
        """
        barrier = _AsyncBarrier(2)
        outsider_done = asyncio.Event()

        async def outsider():
            await barrier.wait()
            get_logger().info("outside")
            outsider_done.set()

        task = asyncio.create_task(outsider())

        with testing.capture_logs(local=True) as logs:
            await barrier.wait()
            get_logger().info("inside")
            await outsider_done.wait()

        await task

        assert ["inside"] == [e["event"] for e in logs]
        assert ["outside"] == _sink_events(sink)

    async def test_child_tasks_inherit_capture(self, sink):
        """
        Tasks created within the capture block are captured themselves.
        """
        child_logged = asyncio.Event()

        async def child():
            get_logger().info("from-child")
            child_logged.set()

        with testing.capture_logs(local=True) as logs:
            task = asyncio.create_task(child())
            await child_logged.wait()
            get_logger().info("from-parent")

        await task

        assert ["from-child", "from-parent"] == [e["event"] for e in logs]
        assert [] == sink.calls

    async def test_late_child_task_resumes_normal_logging(self, sink):
        """
        A child task that outlives the capture block stops appending to the
        returned list once the block exits and resumes regular logging.
        """
        logged_early = asyncio.Event()
        release = asyncio.Event()

        async def child():
            get_logger().info("early")
            logged_early.set()
            await release.wait()
            get_logger().info("late")

        with testing.capture_logs(local=True) as logs:
            task = asyncio.create_task(child())
            await logged_early.wait()

        assert ["early"] == [e["event"] for e in logs]

        release.set()
        await task

        assert ["early"] == [e["event"] for e in logs]
        assert ["late"] == _sink_events(sink)

    def test_nested_captures(self, sink):
        """
        Only the innermost capture collects; the outer one resumes once the
        inner one exits.
        """
        with testing.capture_logs(local=True) as outer:
            get_logger().info("outer-1")
            with testing.capture_logs(local=True) as inner:
                get_logger().info("inner")
            get_logger().info("outer-2")

        assert ["outer-1", "outer-2"] == [e["event"] for e in outer]
        assert ["inner"] == [e["event"] for e in inner]
        assert [] == sink.calls

    def test_nested_capture_restores_on_error(self, sink):
        """
        If the inner capture exits with an error, the outer capture keeps
        working.
        """
        with testing.capture_logs(local=True) as outer:
            with pytest.raises(RuntimeError), testing.capture_logs(local=True):
                raise RuntimeError("boom")

            get_logger().info("outer")

        assert ["outer"] == [e["event"] for e in outer]
        assert [] == sink.calls

    def test_restores_on_error(self, sink):
        """
        After an error, regular logging resumes and the captured events are
        kept.
        """
        with pytest.raises(NotImplementedError):
            with testing.capture_logs(local=True) as logs:
                get_logger().info("captured")
                raise NotImplementedError("from test")

        get_logger().info("after")

        assert ["captured"] == [e["event"] for e in logs]
        assert ["after"] == _sink_events(sink)

    def test_threads_are_not_captured(self, sink):
        """
        Threads don't inherit the capture; their events go to the regular
        sink even while the capture is active in the main thread.
        """
        barrier = threading.Barrier(2)

        def in_thread():
            barrier.wait()
            get_logger().info("from-thread")

        thread = threading.Thread(target=in_thread)

        with testing.capture_logs(local=True) as logs:
            thread.start()
            barrier.wait()
            get_logger().info("from-main")
            thread.join()

        assert ["from-main"] == [e["event"] for e in logs]
        assert ["from-thread"] == _sink_events(sink)

    async def test_native_sync_and_async_methods(self, sink):
        """
        Both sync and async methods of native bound loggers are captured;
        the executor hand-off of async methods carries the capture along.
        """
        logger = get_logger()

        with testing.capture_logs(local=True) as logs:
            logger.info("sync")
            await logger.ainfo("async")

        assert ["sync", "async"] == [e["event"] for e in logs]
        assert [] == sink.calls

    async def test_stdlib_sync_and_async_methods(self):
        """
        Both sync and async methods of stdlib bound loggers are captured.
        """
        sink = _configure_for_local_capture(structlog.stdlib.BoundLogger)
        logger = get_logger()

        with testing.capture_logs(local=True) as logs:
            logger.info("sync")
            await logger.ainfo("async")

        assert ["sync", "async"] == [e["event"] for e in logs]
        assert [] == sink.calls


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
