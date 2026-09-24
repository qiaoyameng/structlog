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
from structlog.testing import CapturingLoggerFactory


@pytest.fixture
def reset_structlog():
    yield
    structlog.reset_defaults()


def render(processor, event_dict):
    processed = processor(None, "info", event_dict)
    rendered = JSONRenderer()(None, "info", processed)

    return json.loads(rendered)


class Evil:
    """An object that must never be stringified."""

    def __repr__(self):
        raise AssertionError("__repr__ must not be called")

    def __str__(self):
        raise AssertionError("__str__ must not be called")


class DictSubclass(dict):
    pass


class CustomIterable:
    def __iter__(self):
        raise AssertionError("must not be iterated")


class TestValidation:
    def test_empty_rule(self):
        with pytest.raises(ValueError, match="must not be empty"):
            RedactFields([()])

    def test_string_rule(self):
        with pytest.raises(ValueError, match="non-string sequence"):
            RedactFields(["password"])

    def test_empty_segment(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            RedactFields([("a", "")])

    def test_non_string_segment(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            RedactFields([("a", 1)])

    @pytest.mark.parametrize("limit", [0, -1, 1.5, "10", True])
    def test_invalid_max_depth(self, limit):
        with pytest.raises(ValueError, match="max_depth"):
            RedactFields(max_depth=limit)

    @pytest.mark.parametrize("limit", [0, -1, 1.5, "10", True])
    def test_invalid_max_nodes(self, limit):
        with pytest.raises(ValueError, match="max_nodes"):
            RedactFields(max_nodes=limit)


class TestRedaction:
    def test_exact_key(self):
        out = render(
            RedactFields([("password",)]),
            {"password": "hunter2", "user": "ada"},
        )

        assert out == {"password": "***", "user": "ada"}

    def test_default_config_keeps_normal_fields(self):
        event = {"user": "ada", "n": 42, "nested": {"ok": True}}
        out = RedactFields()(None, "info", event)

        assert out == event

    def test_custom_replacement(self):
        out = render(
            RedactFields([("password",)], replacement="<hidden>"),
            {"password": "hunter2"},
        )

        assert out == {"password": "<hidden>"}

    def test_dotted_key_is_single_segment(self):
        event = {"a.b": {"c": "secret", "d": "keep"}}

        out = render(RedactFields([("a.b", "c")]), event)
        assert out == {"a.b": {"c": "***", "d": "keep"}}

        # A split path must not match the dotted key.
        out = render(RedactFields([("a", "b", "c")]), event)
        assert out == {"a.b": {"c": "secret", "d": "keep"}}

    def test_alias_paths(self):
        out = render(
            RedactFields([("x", "secret"), ("y", "secret")]),
            {"x": {"secret": "s1"}, "y": {"secret": "s2"}},
        )

        assert out == {"x": {"secret": "***"}, "y": {"secret": "***"}}

    def test_wildcard_mapping_values(self):
        out = render(
            RedactFields([("users", "*", "token")]),
            {
                "users": {
                    "ada": {"token": "t1", "keep": 1},
                    "bob": {"token": "t2"},
                }
            },
        )

        assert out == {
            "users": {
                "ada": {"token": "***", "keep": 1},
                "bob": {"token": "***"},
            }
        }

    def test_wildcard_sequence_elements(self):
        out = render(
            RedactFields([("users", "*", "token")]),
            {"users": [{"token": "t1"}, {"token": "t2", "keep": 1}]},
        )

        assert out == {
            "users": [{"token": "***"}, {"token": "***", "keep": 1}]
        }

    def test_wildcard_matches_non_string_keys(self):
        out = RedactFields([("*", "password")])(
            None, "info", {1: {"password": "hunter2"}}
        )

        assert out == {1: {"password": "***"}}

    def test_exact_segment_does_not_match_sequence_index(self):
        # "0" is a string segment and must not match list index 0.
        out = render(
            RedactFields([("users", "0", "token")]),
            {"users": [{"token": "t1"}]},
        )

        assert out == {"users": [{"token": "t1"}]}

    def test_redacted_object_str_repr_not_called(self):
        out = render(
            RedactFields([("evil",), ("nested", "evil2")]),
            {"evil": Evil(), "nested": {"evil2": Evil()}},
        )

        assert out == {"evil": "***", "nested": {"evil2": "***"}}

    def test_no_mutation_of_input(self):
        event = {
            "password": "hunter2",
            "users": [{"token": "t1"}, ("x", {"token": "t2"})],
        }
        snapshot = copy.deepcopy(event)

        RedactFields([("password",), ("users", "*", "token")])(
            None, "info", event
        )

        assert event == snapshot


class TestContainers:
    def test_nested_lists_and_tuples(self):
        event = {
            "matrix": [[{"pw": "a"}], [{"pw": "b"}]],
            "pair": (({"pw": "c"},), [{"pw": "d"}]),
        }

        out = RedactFields(
            [("matrix", "*", "*", "pw"), ("pair", "*", "*", "pw")]
        )(None, "info", event)

        assert out == {
            "matrix": [[{"pw": "***"}], [{"pw": "***"}]],
            "pair": (({"pw": "***"},), [{"pw": "***"}]),
        }
        assert isinstance(out["pair"], tuple)
        assert isinstance(out["pair"][0], tuple)
        assert isinstance(out["matrix"][0], list)

    def test_containers_are_rebuilt(self):
        inner = {"keep": 1}
        event = {"a": inner, "b": [1, 2], "c": (1, 2)}

        out = RedactFields()(None, "info", event)

        assert out is not event
        assert out["a"] is not inner
        assert out["b"] is not event["b"]
        assert out["c"] is not event["c"]
        # Leaves are shared by reference.
        assert out["a"]["keep"] == 1

    def test_dict_subclass_is_opaque(self):
        sub = DictSubclass(password="hunter2")

        out = RedactFields([("sub", "password")])(None, "info", {"sub": sub})

        # Not traversed: passed through untouched by reference.
        assert out["sub"] is sub
        assert out["sub"]["password"] == "hunter2"

    def test_custom_iterable_is_opaque(self):
        obj = CustomIterable()

        out = RedactFields([("obj", "*")])(None, "info", {"obj": obj})

        assert out["obj"] is obj


class TestSharedAndCyclic:
    def test_shared_subobject_different_rules_per_path(self):
        shared = {"token": "abc", "keep": 1}
        event = {"a": shared, "b": shared}

        out = RedactFields([("a", "token")])(None, "info", event)

        # No caching: the same object is checked independently per path.
        assert out["a"] == {"token": "***", "keep": 1}
        assert out["b"] == {"token": "abc", "keep": 1}
        assert shared == {"token": "abc", "keep": 1}

    def test_shared_subobject_is_not_a_cycle(self):
        shared = {"token": "abc"}
        event = {"a": shared, "b": shared}

        out = RedactFields([("a", "token"), ("b", "token")])(
            None, "info", event
        )

        assert out["a"] == {"token": "***"}
        assert out["b"] == {"token": "***"}

    def test_dict_cycle(self):
        event = {"x": 1}
        event["self"] = event

        out = RedactFields()(None, "info", event)

        assert out == {"x": 1, "self": "<cycle>"}

    def test_list_cycle(self):
        inner = []
        inner.append(inner)
        event = {"list": inner}

        out = RedactFields()(None, "info", event)

        assert out == {"list": ["<cycle>"]}

    def test_indirect_cycle(self):
        first = {}
        second = {"up": first}
        first["down"] = second

        out = RedactFields()(None, "info", {"root": first})

        assert out == {"root": {"down": {"up": "<cycle>"}}}

    def test_cycle_marker_is_customizable(self):
        event = {}
        event["self"] = event

        out = RedactFields(cycle_marker="<CYCLE>")(None, "info", event)

        assert out == {"self": "<CYCLE>"}


class TestLimits:
    def make_deep(self, depth):
        value = {"password": "hunter2"}
        for _ in range(depth):
            value = {"x": value}

        return {"deep": value}

    def test_max_depth_truncates_and_hides_secret(self):
        event = self.make_deep(20)
        rule = ("deep",) + ("x",) * 20 + ("password",)

        out = render(RedactFields([rule], max_depth=5), event)

        serialized = json.dumps(out)
        assert "hunter2" not in serialized
        assert "<truncated>" in serialized

    def test_max_depth_marker_is_customizable(self):
        event = self.make_deep(3)

        out = RedactFields(max_depth=2, truncated_marker="<T>")(
            None, "info", event
        )

        assert out == {"deep": {"x": "<T>"}}

    def test_max_nodes_truncates_and_hides_secret(self):
        event = {"items": [{"token": f"t{i}"} for i in range(50)]}
        snapshot = copy.deepcopy(event)

        out = render(
            RedactFields([("items", "*", "token")], max_nodes=10), event
        )

        serialized = json.dumps(out)
        assert "<truncated>" in serialized
        # Nodes beyond the budget are never passed through unchecked.
        assert "t49" not in serialized
        assert event == snapshot

    def test_limits_do_not_affect_normal_events(self):
        event = {"a": {"b": [1, 2, {"c": "x"}]}}

        out = RedactFields(max_depth=10, max_nodes=1000)(None, "info", event)

        assert out == event


class TestStructlogIntegration:
    def test_bound_context_is_reused_and_not_mutated(self, reset_structlog):
        factory = CapturingLoggerFactory()
        structlog.configure(
            processors=[
                RedactFields([("creds", "password")]),
                JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=factory,
        )

        log = structlog.get_logger().bind(
            creds={"password": "hunter2", "user": "ada"}
        )
        log.info("one")
        log.info("two")

        calls = factory.logger.calls
        assert len(calls) == 2
        for call in calls:
            rendered = call.args[0]
            assert "hunter2" not in rendered
            assert json.loads(rendered)["creds"] == {
                "password": "***",
                "user": "ada",
            }

        # The bound context survives repeated logging unmodified.
        assert log._context == {
            "creds": {"password": "hunter2", "user": "ada"}
        }

    def test_processor_formatter_multiple_handlers(self, reset_structlog):
        structlog.configure(
            processors=[ProcessorFormatter.wrap_for_formatter],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
        )

        streams = [StringIO(), StringIO()]
        stdlib_logger = logging.getLogger("redact-test")
        for stream in streams:
            handler = logging.StreamHandler(stream)
            handler.setFormatter(
                ProcessorFormatter(
                    processors=[
                        ProcessorFormatter.remove_processors_meta,
                        RedactFields([("password",), ("nested", "token")]),
                        JSONRenderer(),
                    ]
                )
            )
            stdlib_logger.addHandler(handler)
        stdlib_logger.setLevel(logging.INFO)
        stdlib_logger.propagate = False
        try:
            nested = {"token": "abc", "keep": 1}
            structlog.get_logger("redact-test").info(
                "hello", password="hunter2", nested=nested
            )
        finally:
            for handler in stdlib_logger.handlers:
                stdlib_logger.removeHandler(handler)

        # Both handlers see the original record and redact independently.
        for stream in streams:
            rendered = stream.getvalue()
            assert "hunter2" not in rendered
            assert "abc" not in rendered
            assert json.loads(rendered)["nested"] == {
                "token": "***",
                "keep": 1,
            }

        # The original event data shared with the record is unchanged.
        assert nested == {"token": "abc", "keep": 1}
