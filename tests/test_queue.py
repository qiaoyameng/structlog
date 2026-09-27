# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import sys
import time

from logging.handlers import QueueListener

import pytest

import structlog

from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    merge_contextvars,
)
from structlog.processors import JSONRenderer
from structlog.stdlib import (
    BoundLogger,
    LoggerFactory,
    ProcessorFormatter,
    QueueHandler,
    add_log_level,
    add_logger_name,
)


class _RecordingQueueHandler(QueueHandler):
    """
    A QueueHandler that records errors passed to handleError() instead of
    printing them.
    """

    def __init__(self, q):
        super().__init__(q)
        self.errors = []

    def handleError(self, record):
        self.errors.append((record, sys.exc_info()[1]))


@pytest.fixture(autouse=True)
def _restore_logger_class():
    """
    LoggerFactory replaces the logger class globally; restore it.
    """
    cls = logging.getLoggerClass()

    yield

    logging.setLoggerClass(cls)


@pytest.fixture(name="producer")
def _producer():
    """
    Configure structlog to wrap event dicts for ProcessorFormatter and
    attach a recording QueueHandler to the root logger.

    Return (queue, handler).
    """
    structlog.configure(
        processors=[
            merge_contextvars,
            add_log_level,
            add_logger_name,
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=LoggerFactory(),
        wrapper_class=BoundLogger,
    )

    q = queue.Queue()
    handler = _RecordingQueueHandler(q)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    yield q, handler

    clear_contextvars()


def _render(record, **formatter_kw):
    formatter = ProcessorFormatter(
        processors=[
            ProcessorFormatter.remove_processors_meta,
            JSONRenderer(),
        ],
        **formatter_kw,
    )

    return formatter.format(record)


def _failing_helper():
    raise ValueError("boom")


class TestQueueHandler:
    def test_event_dict_survives_queue(self, producer):
        """
        The event dict crosses the queue as a dict, together with the
        producer's metadata (level, logger name, call site, timestamp).
        """
        q, handler = producer

        log = structlog.get_logger("queued").bind(bound="ctx")
        before = time.time()
        log.info("hello", answer=42)
        after = time.time()

        assert [] == handler.errors

        record = q.get_nowait()

        assert isinstance(record.msg, dict)
        assert "queued" == record.name
        assert logging.INFO == record.levelno
        assert before <= record.created <= after
        assert __file__ == record.pathname
        assert "test_event_dict_survives_queue" == record.funcName

        ed = json.loads(_render(record))

        assert {
            "event": "hello",
            "bound": "ctx",
            "answer": 42,
            "level": "info",
            "logger": "queued",
        } == ed

    def test_snapshot_isolated_from_later_mutation(self, producer):
        """
        Mutating the original event dict values -- including nested
        containers -- after the logging call returns does not change the
        enqueued event.
        """
        q, _ = producer

        payload = {"items": [1, 2], "nested": {"key": "value"}}
        structlog.get_logger().info("snapshot", payload=payload)

        payload["items"].append(3)
        payload["nested"]["key"] = "changed"
        payload["late"] = True

        record = q.get_nowait()
        ed = json.loads(_render(record))

        assert {"items": [1, 2], "nested": {"key": "value"}} == ed["payload"]

    def test_exc_info_rendered_on_producer(self, producer):
        """
        exc_info is resolved and rendered on the producer: the event dict
        that crosses the queue contains a plain string under "exception" and
        no exc_info that a consumer could re-resolve against its own stack.
        """
        q, handler = producer

        log = structlog.get_logger()
        try:
            _failing_helper()
        except ValueError:
            log.exception("went wrong")

        assert [] == handler.errors

        record = q.get_nowait()

        assert record.exc_info is None

        ed = json.loads(_render(record))

        assert "exc_info" not in ed
        exception = ed["exception"]
        assert "Traceback (most recent call last)" in exception
        assert "ValueError: boom" in exception
        # The frame only exists on the producer side.
        assert "_failing_helper" in exception

    def test_exc_info_exception_instance(self, producer):
        """
        An exception instance passed as exc_info is rendered too.
        """
        q, handler = producer

        structlog.get_logger().info("oops", exc_info=KeyError("nope"))

        assert [] == handler.errors

        ed = json.loads(_render(q.get_nowait()))

        assert "exc_info" not in ed
        assert "KeyError: 'nope'" in ed["exception"]

    def test_exc_info_true_outside_exception(self, producer):
        """
        exc_info=True without an active exception does not fabricate an
        exception on the consumer side.
        """
        q, handler = producer

        structlog.get_logger().info("no exc", exc_info=True)

        assert [] == handler.errors

        ed = json.loads(_render(q.get_nowait()))

        assert "exc_info" not in ed
        assert "exception" not in ed

    def test_foreign_record(self, producer):
        """
        Plain logging records cross the same queue: the message is
        interpolated and the exception is rendered into exc_text on the
        producer.
        """
        q, handler = producer

        try:
            _failing_helper()
        except ValueError:
            logging.getLogger("plain").warning("plain %s", 42, exc_info=True)

        assert [] == handler.errors

        record = q.get_nowait()

        assert "plain 42" == record.msg
        assert record.args is None
        assert record.exc_info is None
        assert "ValueError: boom" in record.exc_text
        assert "_failing_helper" in record.exc_text

        rendered = _render(record, keep_exc_info=True)

        assert "plain 42" in rendered
        assert "ValueError: boom" in rendered

    def test_unpicklable_object_goes_to_handle_error(self, producer):
        """
        Values that can't cross the queue fail synchronously in the calling
        thread and are routed to handleError() instead of being lost in
        multiprocessing's feeder thread.
        """
        q, handler = producer

        # Does not raise.
        structlog.get_logger().info("bad", callback=lambda: None)

        assert 1 == len(handler.errors)
        assert handler.errors[0][1] is not None
        assert q.empty()

    def test_full_queue_does_not_block(self, producer):
        """
        A full queue never blocks the calling thread: the overflow event is
        dropped into handleError() (with queue.Full) and earlier events are
        kept.
        """
        full_q = queue.Queue(maxsize=1)
        handler = _RecordingQueueHandler(full_q)
        logger = logging.getLogger("full")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        try:
            log = structlog.get_logger("full")
            log.info("first")
            log.info("second")
        finally:
            logger.removeHandler(handler)
            logger.propagate = True

        assert 1 == len(handler.errors)
        assert isinstance(handler.errors[0][1], queue.Full)

        record = full_q.get_nowait()
        assert "first" == record.msg["event"]
        assert full_q.empty()

    def test_prepare_leaves_original_record_untouched(self, producer):
        """
        prepare() works on a copy; the original record keeps its event dict
        and exc_info for other handlers.
        """
        _, handler = producer

        ed = {"event": "x", "exc_info": ValueError("e")}
        record = logging.getLogger().makeRecord(
            "n", logging.INFO, __file__, 1, ed, (), None
        )

        prepared = handler.prepare(record)

        assert record.msg is ed
        assert prepared.msg is not ed
        assert "x" == prepared.msg["event"]
        assert "exc_info" not in prepared.msg

    def test_queue_listener_roundtrip_and_clean_stop(self, producer):
        """
        A stock logging QueueListener renders queued events via
        ProcessorFormatter and stops cleanly, terminating its thread.
        """
        q, handler = producer
        rendered = []

        class _Handler(logging.Handler):
            def emit(self, record):
                rendered.append(_render(record))

        listener = QueueListener(q, _Handler())
        listener.start()

        structlog.get_logger("listener").info("one", n=1)
        try:
            _failing_helper()
        except ValueError:
            structlog.get_logger("listener").exception("two")
        logging.getLogger("plain").warning("three")

        listener.stop()

        assert listener._thread is None or not listener._thread.is_alive()
        assert [] == handler.errors

        assert 3 == len(rendered)
        one = json.loads(rendered[0])
        assert {"event": "one", "n": 1, "logger": "listener"}.items() <= one.items()
        two = json.loads(rendered[1])
        assert "ValueError: boom" in two["exception"]
        assert "three" in rendered[2]


def _spawn_consumer(log_queue, result_queue, control_queue):
    """
    Run in a spawned process: consume records from log_queue with a stock
    QueueListener, render them with ProcessorFormatter, and send rendered
    output plus record metadata back through result_queue.
    """
    formatter = ProcessorFormatter(
        processors=[
            ProcessorFormatter.remove_processors_meta,
            JSONRenderer(),
        ],
        foreign_pre_chain=[add_log_level],
        keep_exc_info=True,
    )

    class _Handler(logging.Handler):
        def emit(self, record):
            result_queue.put(
                {
                    "pid": os.getpid(),
                    "rendered": formatter.format(record),
                    "name": record.name,
                    "levelno": record.levelno,
                    "created": record.created,
                    "pathname": record.pathname,
                    "funcName": record.funcName,
                }
            )

    listener = QueueListener(log_queue, _Handler())
    listener.start()

    control_queue.get()  # wait until the producer is done
    listener.stop()

    result_queue.put("DONE")


def _produce_spawn_events():
    structlog.get_logger("spawned").bind(bound="ctx").info(
        "spawn event", answer=42
    )
    try:
        _failing_helper()
    except ValueError:
        structlog.get_logger("spawned").exception("spawn exc")
    logging.getLogger("plain").warning("plain %s", 42)


class TestQueueHandlerSpawn:
    @pytest.fixture(name="spawn_cluster")
    def _spawn_cluster(self, producer):
        """
        Start a spawned process that consumes from a multiprocessing.Queue
        and point the recording handler at it.
        """
        _, handler = producer
        ctx = multiprocessing.get_context("spawn")
        log_queue = ctx.Queue()
        result_queue = ctx.Queue()
        control_queue = ctx.Queue()
        handler.queue = log_queue

        proc = ctx.Process(
            target=_spawn_consumer,
            args=(log_queue, result_queue, control_queue),
        )
        proc.start()

        yield log_queue, result_queue, control_queue

        control_queue.put(None)
        assert "DONE" == result_queue.get(timeout=30)
        proc.join(timeout=30)
        assert 0 == proc.exitcode

    def test_spawn_process_consumes_events(self, producer, spawn_cluster):
        """
        Events produced in this process are consumed and rendered by a
        spawned process: structure, context, exception, call site, and
        production time all come from the producer.
        """
        _, handler = producer
        _, result_queue, _ = spawn_cluster

        bind_contextvars(request_id="req-1")

        before = time.time()
        _produce_spawn_events()
        after = time.time()

        results = [result_queue.get(timeout=30) for _ in range(3)]

        assert [] == handler.errors

        # The events were really consumed by a different process.
        assert {os.getpid()} != {result["pid"] for result in results}

        # Producer-side metadata travels with every record.
        for result in results:
            assert before <= result["created"] <= after
            assert __file__ == result["pathname"]
            assert "_produce_spawn_events" == result["funcName"]

        # Structured event with bound context and contextvars.
        event = json.loads(results[0]["rendered"])
        assert {
            "event": "spawn event",
            "bound": "ctx",
            "request_id": "req-1",
            "answer": 42,
            "level": "info",
            "logger": "spawned",
        } == event
        assert "spawned" == results[0]["name"]
        assert logging.INFO == results[0]["levelno"]

        # Exception rendered on the producer: the traceback mentions frames
        # that only exist in this process.
        exc_event = json.loads(results[1]["rendered"])
        assert "exc_info" not in exc_event
        assert "ValueError: boom" in exc_event["exception"]
        assert "_failing_helper" in exc_event["exception"]
        assert "_produce_spawn_events" in exc_event["exception"]

        # Plain logging record through the same queue.
        assert "plain 42" in results[2]["rendered"]
        assert "plain" == results[2]["name"]
        assert logging.WARNING == results[2]["levelno"]
