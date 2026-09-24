# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

from __future__ import annotations

import copy
import json
import logging

from io import StringIO

import pytest

import structlog

from structlog.processors import JSONRenderer, RedactFields
from structlog.stdlib import ProcessorFormatter
from structlog.testing import CapturingLogger


ANY = RedactFields.ANY


class ReprBomb:
    """
    An object that must never be stringified by the redactor.
    """

    def __repr__(self):
        raise AssertionError("repr() must not be called")

    def __str__(self):
        raise AssertionError("str() must not be called")


def redact(rules, event_dict, **kwargs):
    return RedactFields(rules, **kwargs)(None, "info", event_dict)


class TestRedactFields:
    def test_exact_key(self):
        """
        A plain string segment redacts the matching mapping key.
        """
        result = redact([("password",)], {"password": "hunter2", "user": "j"})

        assert {"password": "***", "user": "j"} == result

    def test_nested_path(self):
        """
        Multi-segment rules descend into nested mappings.
        """
        result = redact(
            [("a", "b", "secret")],
            {"a": {"b": {"secret": "x", "keep": 1}}, "secret": "untouched"},
        )

        assert {
            "a": {"b": {"secret": "***", "keep": 1}},
            "secret": "untouched",
        } == result

    def test_dotted_key_is_one_segment(self):
        """
        Keys containing dots are matched as a single path segment and are
        never split.
        """
        result = redact(
            [("a.b",)], {"a.b": "secret", "a": {"b": "different"}}
        )

        assert {"a.b": "***", "a": {"b": "different"}} == result

    def test_wildcard_mapping(self):
        """
        ANY matches all values of the current mapping.
        """
        result = redact(
            [("users", ANY, "token")],
            {
                "users": {
                    "u1": {"token": "t1", "name": "a"},
                    "u2": {"token": "t2", "name": "b"},
                }
            },
        )

        assert {
            "users": {
                "u1": {"token": "***", "name": "a"},
                "u2": {"token": "***", "name": "b"},
            }
        } == result

    def test_wildcard_sequence(self):
        """
        ANY matches all elements of lists and tuples.
        """
        result = redact(
            [("cmds", ANY, "argv", ANY)],
            {"cmds": [{"argv": ["run", "--pw", "s3"]}, {"argv": ("x", "y")}]},
        )

        assert {
            "cmds": [
                {"argv": ["***", "***", "***"]},
                {"argv": ("***", "***")},
            ]
        } == result

    def test_nested_arrays(self):
        """
        Arbitrarily nested lists/tuples are traversed and rebuilt with the
        same container types.
        """
        result = redact(
            [("matrix", ANY, ANY)],
            {"matrix": [[1, "secret"], ("secret", 3), []]},
        )

        assert {"matrix": [["***", "***"], ("***", "***"), []]} == result
        assert isinstance(result["matrix"][0], list)
        assert isinstance(result["matrix"][1], tuple)

    def test_alias_paths_same_object(self):
        """
        The same object reachable via multiple paths is examined per path:
        rules that only apply to one alias do not leak into the other, and
        redaction is not skipped because the object was visited before.
        """
        shared = {"token": "abc", "name": "n"}
        event_dict = {"first": shared, "second": shared}

        result = redact([("first", "token")], event_dict)

        assert {
            "first": {"token": "***", "name": "n"},
            "second": {"token": "abc", "name": "n"},
        } == result

    def test_alias_paths_both_redacted(self):
        """
        A shared object under two aliased rule paths is redacted on both.
        """
        shared = {"token": "abc"}
        event_dict = {"a": {"ptr": shared}, "b": {"ptr": shared}}

        result = redact(
            [("a", "ptr", "token"), ("b", "ptr", "token")], event_dict
        )

        assert "***" == result["a"]["ptr"]["token"]
        assert "***" == result["b"]["ptr"]["token"]

    def test_cycle_dict(self):
        """
        Cyclic mappings are replaced by the cycle marker and do not cause
        infinite recursion.
        """
        event_dict = {"a": {"b": None}}
        event_dict["a"]["b"] = event_dict["a"]

        result = redact([("x",)], event_dict, cycle_marker="<c>")

        assert {"a": {"b": "<c>"}} == result

    def test_cycle_list(self):
        """
        Cyclic sequences are replaced by the cycle marker.
        """
        lst = [1]
        lst.append(lst)

        result = redact([("x",)], {"lst": lst})

        assert {"lst": [1, "<cycle>"]} == result

    def test_cycle_under_redacted_path(self):
        """
        A cycle that is redacted wholesale does not recurse either.
        """
        event_dict = {"secret": {}}
        event_dict["secret"]["me"] = event_dict["secret"]

        result = redact([("secret",)], event_dict)

        assert {"secret": "***"} == result

    def test_malicious_repr_not_called(self):
        """
        Redacted values are dropped without calling str()/repr() on them.
        """
        result = redact(
            [("secret",), ("nested", ANY)],
            {"secret": ReprBomb(), "nested": [ReprBomb()], "keep": ReprBomb()},
        )

        assert isinstance(result["secret"], str)
        assert ["***"] == result["nested"]
        # Untouched custom objects are passed through by identity.
        assert isinstance(result["keep"], ReprBomb)

    def test_max_depth(self):
        """
        Containers deeper than max_depth are replaced by the truncation
        marker -- the unchecked original never reaches the renderer.
        """
        deep = {"l1": {"l2": {"l3": {"secret": "leak"}}}}

        result = redact([], deep)  # no rules: returned as-is
        assert result is deep

        result = redact(
            [("nothing",)], deep, max_depth=2, truncation_marker="<t>"
        )

        assert {"l1": {"l2": "<t>"}} == result
        assert "leak" not in repr(result)

    def test_max_nodes(self):
        """
        Once the node budget is exhausted, remaining containers are replaced
        by the truncation marker.
        """
        event_dict = {
            "a": {"secret": "lurk"},
            "b": {"secret": "lurk"},
            "c": 1,
        }

        result = redact(
            [("nothing",)], event_dict, max_nodes=2, truncation_marker="<t>"
        )

        # The root and "a" fit the budget and are checked normally; the
        # unchecked subtree "b" is replaced by the truncation marker.
        assert {"a": {"secret": "lurk"}, "b": "<t>", "c": 1} == result

        # A secret hidden in the unchecked subtree cannot leak.
        event_dict = {"a": 1, "b": {"secret": "lurk"}}

        result = redact(
            [("nothing",)], event_dict, max_nodes=1, truncation_marker="<t>"
        )

        assert {"a": 1, "b": "<t>"} == result
        assert "lurk" not in repr(result)

    def test_original_not_modified(self):
        """
        Neither the event dict nor any nested container is mutated.
        """
        inner = {"token": "abc", "n": [1, 2]}
        event_dict = {"a": inner, "list": [inner]}
        snapshot = copy.deepcopy(event_dict)

        result = redact([("a", "token"), ("list", ANY, "n", ANY)], event_dict)

        assert snapshot == event_dict
        assert result is not event_dict
        assert result["a"] is not inner
        assert "***" == result["a"]["token"]
        assert {"token": "abc", "n": ["***", "***"]} == result["list"][0]

    def test_default_rules_are_noop(self):
        """
        Without rules, the event dict is returned unchanged (same object).
        """
        event_dict = {"password": "hunter2"}

        assert redact([], event_dict) is event_dict

    def test_custom_objects_not_traversed(self):
        """
        Objects that are not exactly dict/list/tuple -- including subclasses
        and objects with secrets in attributes -- are passed through by
        identity and never inspected.
        """

        class MyDict(dict):
            pass

        class SecretHolder:
            def __init__(self):
                self.password = "hunter2"

        holder = SecretHolder()
        my_dict = MyDict(password="hunter2")

        result = redact(
            [("password",), (ANY, "password")],
            {"holder": holder, "my_dict": my_dict},
        )

        assert result["holder"] is holder
        assert result["my_dict"] is my_dict
        assert "hunter2" == result["my_dict"]["password"]

    def test_non_string_keys_kept(self):
        """
        Non-string mapping keys are preserved and never match string rules.
        """
        result = redact([("1",), (ANY,)], {1: "one", (2, 3): "two"})

        assert {1: "***", (2, 3): "***"} == result

    def test_json_renderer_integration(self):
        """
        The returned structure renders to JSON without sensitive values.
        """
        result = redact(
            [("auth", "token")],
            {"auth": {"token": "s3cr3t", "scheme": "bearer"}},
        )
        rendered = JSONRenderer()(None, "info", result)

        assert "s3cr3t" not in rendered
        assert {"auth": {"token": "***", "scheme": "bearer"}} == json.loads(
            rendered
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_depth": 0},
            {"max_depth": -1},
            {"max_depth": "3"},
            {"max_nodes": 0},
            {"max_nodes": 1.5},
            {"placeholder": 42},
            {"cycle_marker": None},
            {"truncation_marker": object()},
        ],
    )
    def test_invalid_options(self, kwargs):
        """
        Limits and markers are validated at initialization.
        """
        with pytest.raises((TypeError, ValueError)):
            RedactFields([("a",)], **kwargs)

    @pytest.mark.parametrize(
        "rule",
        [
            (),
            [],
            ("",),
            ("a", ""),
            (None,),
            ("a", 1),
            (("a",),),
        ],
    )
    def test_invalid_rules(self, rule):
        """
        Rules must be non-empty sequences of non-empty string segments.
        """
        with pytest.raises((TypeError, ValueError)):
            RedactFields([rule])


class TestBoundContextReuse:
    def test_bound_context_not_mutated(self):
        """
        Context bound to a logger survives logging untouched and stays
        reusable for subsequent log calls.
        """
        capturing = CapturingLogger()
        logger = structlog.wrap_logger(
            capturing,
            processors=[RedactFields([("password",)])],
            cache_logger_on_first_use=False,
        )
        bound = logger.bind(password="hunter2", user="j")
        context_before = dict(bound._context)

        bound.info("first")
        bound.info("second")

        assert context_before == bound._context
        assert "hunter2" == bound._context["password"]

        for call in capturing.calls:
            assert "***" == call.kwargs["password"]
            assert "j" == call.kwargs["user"]


class TestProcessorFormatterIsolation:
    def test_multiple_handlers_isolated(self):
        """
        Two handlers with different ProcessorFormatter chains on the same
        logger each get their own view: the redacting handler masks secrets
        while the plain handler still sees the original values.
        """
        redacting_stream = StringIO()
        plain_stream = StringIO()

        redacting_handler = logging.StreamHandler(redacting_stream)
        redacting_handler.setFormatter(
            ProcessorFormatter(
                processors=[
                    ProcessorFormatter.remove_processors_meta,
                    RedactFields([("password",)]),
                    JSONRenderer(),
                ],
            )
        )
        plain_handler = logging.StreamHandler(plain_stream)
        plain_handler.setFormatter(
            ProcessorFormatter(
                processors=[
                    ProcessorFormatter.remove_processors_meta,
                    JSONRenderer(),
                ]
            )
        )

        stdlib_logger = logging.getLogger("redact-isolation-test")
        stdlib_logger.addHandler(redacting_handler)
        stdlib_logger.addHandler(plain_handler)
        stdlib_logger.setLevel(logging.INFO)
        stdlib_logger.propagate = False
        try:
            structlog.configure(
                processors=[ProcessorFormatter.wrap_for_formatter],
                logger_factory=structlog.stdlib.LoggerFactory(),
                cache_logger_on_first_use=False,
            )
            try:
                structlog.get_logger("redact-isolation-test").info(
                    "login", password="hunter2"
                )
            finally:
                structlog.reset_defaults()
        finally:
            stdlib_logger.removeHandler(redacting_handler)
            stdlib_logger.removeHandler(plain_handler)

        redacted_output = redacting_stream.getvalue()
        plain_output = plain_stream.getvalue()

        assert "hunter2" not in redacted_output
        assert "***" in redacted_output
        assert "hunter2" in plain_output
