# SPDX-License-Identifier: MIT OR Apache-2.0
# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the MIT License.  See the LICENSE file in the root of this
# repository for complete details.

from __future__ import annotations

import asyncio
import inspect
import json
import sys

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

import structlog

from structlog import tracebacks


try:
    import rich
except ImportError:
    rich = None


class SecretStr(str):  # noqa: SLOT000
    """
    Secrets representation as used in Typed Settings or Pydantic.
    """

    def __repr__(self) -> str:
        return "*******"


@pytest.fixture(autouse=True)
def _unimport_rich() -> Iterator[None]:
    with patch.object(tracebacks, "rich", None):
        yield


def get_next_lineno() -> int:
    return inspect.currentframe().f_back.f_lineno + 1


@pytest.mark.parametrize(("data", "expected"), [(3, "3"), ("spam", "spam")])
def test_save_str(data: Any, expected: str):
    """
    "safe_str()" returns the str repr of an object.
    """
    assert expected == tracebacks.safe_str(data)


def test_safe_str_error():
    """
    "safe_str()" does not fail if __str__() raises an exception.
    """

    class Baam:
        def __str__(self) -> str:
            raise ValueError("BAAM!")

    with pytest.raises(ValueError, match="BAAM!"):
        str(Baam())

    assert "<str-error 'BAAM!'>" == tracebacks.safe_str(Baam())


@pytest.mark.parametrize(
    ("data", "max_len", "expected"),
    [
        (3, None, "3"),
        ("spam", None, "'spam'"),
        (b"spam", None, "b'spam'"),
        ("bacon", 3, "'bac'+2"),
        ("bacon", 4, "'baco'+1"),
        ("bacon", 5, "'bacon'"),
        (SecretStr("password"), None, "*******"),
        (["spam", "eggs", "bacon"], 10, "\"['spam', '\"+15"),
    ],
)
def test_to_repr(data: Any, max_len: int | None, expected: str) -> None:
    """
    "to_repr()" returns the repr of an object, trimmed to max_len.
    """
    assert expected == tracebacks.to_repr(data, max_string=max_len)


@pytest.mark.skipif(rich is None, reason="rich not installed")
@pytest.mark.parametrize(
    ("use_rich", "data", "max_len", "expected"),
    [
        (True, 3, None, "3"),
        (True, "spam", None, "'spam'"),
        (True, b"spam", None, "b'spam'"),
        (True, "bacon", 3, "'bac'+2"),
        (True, "bacon", 5, "'bacon'"),
        (True, SecretStr("password"), None, "*******"),
        (True, ["spam", "eggs", "bacon"], 4, "['spam', 'eggs', 'baco'+1]"),
        (False, "bacon", 3, "'bac'+2"),
        (False, ["spam", "eggs", "bacon"], 4, '"[\'sp"+21'),
    ],
)
def test_to_repr_rich(
    use_rich: bool,
    data: Any,
    max_len: int | None,
    expected: str,
) -> None:
    """
    "to_repr()" uses Rich to get a nice repr if it is installed and if
    "use_rich" is True.
    """
    with patch.object(tracebacks, "rich", rich):
        actual = tracebacks.to_repr(
            data, max_string=max_len, use_rich=use_rich
        )

    assert expected == actual


def test_to_repr_error() -> None:
    """
    "to_repr()" does not fail if __repr__() raises an exception.
    """

    class Baam:
        def __repr__(self) -> str:
            raise ValueError("BAAM!")

    with pytest.raises(ValueError, match="BAAM!"):
        repr(Baam())

    assert "<repr-error 'BAAM!'>" == tracebacks.to_repr(Baam())


def test_simple_exception():
    """
    Tracebacks are parsed for simple, single exceptions.
    """
    try:
        lineno = get_next_lineno()
        1 / 0
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_simple_exception",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_simple_exception_with_notes():
    """
    Notes are included in the traceback.
    """
    try:
        lineno = get_next_lineno()
        1 / 0
    except Exception as e:
        e.add_note("This is a note.")
        e.add_note("This is another note.")
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            exc_notes=["This is a note.", "This is another note."],
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_simple_exception_with_notes",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_raise_hide_cause():
    """
    If "raise ... from None" is used, the trace looks like from a simple
    exception.
    """
    try:
        try:
            1 / 0
        except ArithmeticError:
            lineno = get_next_lineno()
            raise ValueError("onoes") from None
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="onoes",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_raise_hide_cause",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_raise_with_cause():
    """
    If "raise ... from orig" is used, the orig trace is included and marked as
    cause.
    """
    try:
        try:
            lineno_1 = get_next_lineno()
            1 / 0
        except ArithmeticError as orig_exc:
            lineno_2 = get_next_lineno()
            raise ValueError("onoes") from orig_exc
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="onoes",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno_2,
                    name="test_raise_with_cause",
                    locals=None,
                ),
            ],
        ),
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            syntax_error=None,
            is_cause=True,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno_1,
                    name="test_raise_with_cause",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_raise_with_cause_no_tb():
    """
    If an exception's cause has no traceback, that cause is ignored.
    """
    try:
        lineno = get_next_lineno()
        raise ValueError("onoes") from RuntimeError("I am fake")
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="onoes",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_raise_with_cause_no_tb",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_raise_nested():
    """
    If an exc is raised during handling another one, the orig trace is
    included.
    """
    try:
        try:
            lineno_1 = get_next_lineno()
            1 / 0
        except ArithmeticError:
            lineno_2 = get_next_lineno()
            raise ValueError("onoes")  # noqa: B904
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="onoes",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno_2,
                    name="test_raise_nested",
                    locals=None,
                ),
            ],
        ),
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno_1,
                    name="test_raise_nested",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_raise_no_msg():
    """
    If exception classes (not instances) are raised, "exc_value" is an empty
    string.
    """
    try:
        lineno = get_next_lineno()
        raise RuntimeError
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="RuntimeError",
            exc_value="",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_raise_no_msg",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_syntax_error():
    """
    For SyntaxError, extra info about that error is added to the trace.
    """
    try:
        # raises SyntaxError: invalid syntax
        lineno = get_next_lineno()
        eval("2 +* 2")
    except SyntaxError as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="SyntaxError",
            exc_value="invalid syntax (<string>, line 1)",
            syntax_error=tracebacks.SyntaxError_(
                offset=4,
                filename="<string>",
                line="2 +* 2",
                lineno=1,
                msg="invalid syntax",
            ),
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_syntax_error",
                ),
            ],
        ),
    ] == trace.stacks


def test_filename_with_bracket():
    """
    Filenames with brackets (e.g., "<string>") are handled properly.
    """
    try:
        lineno = get_next_lineno()
        exec(compile("1/0", filename="<string>", mode="exec"))
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_filename_with_bracket",
                    locals=None,
                ),
                tracebacks.Frame(
                    filename="<string>",
                    lineno=1,
                    name="<module>",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_filename_not_a_file():
    """
    "Invalid" filenames are appended to CWD as if they were actual files.
    """
    try:
        lineno = get_next_lineno()
        exec(compile("1/0", filename="string", mode="exec"))
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert [
        tracebacks.Stack(
            exc_type="ZeroDivisionError",
            exc_value="division by zero",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_filename_not_a_file",
                    locals=None,
                ),
                tracebacks.Frame(
                    filename=str(Path.cwd().joinpath("string")),
                    lineno=1,
                    name="<module>",
                    locals=None,
                ),
            ],
        ),
    ] == trace.stacks


def test_show_locals():
    """
    Local variables in each frame can optionally be captured.
    """

    def bar(a):
        print(1 / a)

    def foo(a):
        bar(a)

    try:
        foo(0)
    except Exception as e:
        trace = tracebacks.extract(
            type(e), e, e.__traceback__, show_locals=True
        )

    stack_locals = [f.locals for f in trace.stacks[0].frames]
    # The first frames contain functions with "random" memory addresses,
    # so we only check the variable names:
    assert stack_locals[0].keys() == {"foo", "e", "bar"}
    assert stack_locals[1].keys() == {"a", "bar"}
    assert stack_locals[2] == {"a": "0"}


def test_recursive():
    """
    Recursion errors give a lot of frames but don't break stuff.
    """

    def foo(n):
        return bar(n)

    def bar(n):
        return foo(n)

    try:
        lineno = get_next_lineno()
        foo(1)
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    frames = trace.stacks[0].frames
    trace.stacks[0].frames = []

    assert [
        tracebacks.Stack(
            exc_type="RecursionError",
            exc_value="maximum recursion depth exceeded",
            syntax_error=None,
            is_cause=False,
            frames=[],
        ),
    ] == trace.stacks
    assert (
        len(frames) > sys.getrecursionlimit() - 50
    )  # Buffer for frames from pytest
    assert (
        tracebacks.Frame(
            filename=__file__,
            lineno=lineno,
            name="test_recursive",
        )
        == frames[0]
    )

    # If we run the tests under Python 3.12 with sysmon enabled, it inserts
    # frames at the end.
    if sys.version_info >= (3, 12):
        frames = [f for f in frames if "coverage" not in f.filename]

    # Depending on whether we invoke pytest directly or run tox, either "foo()"
    # or "bar()" is at the end of the stack.
    assert frames[-1] in [
        tracebacks.Frame(
            filename=__file__,
            lineno=lineno - 7,
            name="foo",
        ),
        tracebacks.Frame(
            filename=__file__,
            lineno=lineno - 4,
            name="bar",
        ),
    ]


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_exception_groups() -> None:
    """
    Exception groups are detected and a list of Trace instances is added to
    the exception group's Trace.
    """
    lineno = get_next_lineno()

    async def t1() -> None:
        1 / 0

    async def t2() -> None:
        raise ValueError("Blam!")

    async def main():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(t1())
            tg.create_task(t2())

    try:
        asyncio.run(main())
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert "ExceptionGroup" == trace.stacks[0].exc_type
    assert (
        "unhandled errors in a TaskGroup (2 sub-exceptions)"
        == trace.stacks[0].exc_value
    )
    exceptions = trace.stacks[0].exceptions
    assert [
        tracebacks.Trace(
            stacks=[
                tracebacks.Stack(
                    exc_type="ZeroDivisionError",
                    exc_value="division by zero",
                    exc_notes=[],
                    syntax_error=None,
                    is_cause=False,
                    frames=[
                        tracebacks.Frame(
                            filename=__file__,
                            lineno=lineno + 2,
                            name="t1",
                            locals=None,
                        )
                    ],
                    is_group=False,
                    exceptions=[],
                )
            ]
        ),
        tracebacks.Trace(
            stacks=[
                tracebacks.Stack(
                    exc_type="ValueError",
                    exc_value="Blam!",
                    exc_notes=[],
                    syntax_error=None,
                    is_cause=False,
                    frames=[
                        tracebacks.Frame(
                            filename=__file__,
                            lineno=lineno + 5,
                            name="t2",
                            locals=None,
                        )
                    ],
                    is_group=False,
                    exceptions=[],
                )
            ]
        ),
    ] == exceptions


@pytest.mark.parametrize(
    ("kwargs", "local_variable"),
    [
        (
            {"locals_max_string": None},
            "x" * (tracebacks.LOCALS_MAX_STRING + 10),
        ),
        (
            {"locals_max_length": None},
            list(range(tracebacks.LOCALS_MAX_LENGTH + 10)),
        ),
    ],
)
def test_exception_dict_transformer_locals_max_accept_none_argument(
    kwargs, local_variable
):
    """
    ExceptionDictTransformer works when either locals_max_string or
    locals_max_length is set to None.
    """

    try:
        my_local = local_variable
        1 / 0
    except Exception as e:
        format_json = tracebacks.ExceptionDictTransformer(
            show_locals=True, **kwargs
        )
        result = format_json((type(e), e, e.__traceback__))

    _ = my_local

    assert len(result) == 1
    assert len(result[0]["frames"]) == 1

    frame = result[0]["frames"][0]

    assert frame["locals"]["my_local"].strip("'") == str(local_variable)


def test_json_traceback():
    """
    Tracebacks are formatted to JSON with all information.
    """
    try:
        lineno = get_next_lineno()
        1 / 0
    except Exception as e:
        format_json = tracebacks.ExceptionDictTransformer(show_locals=False)
        result = format_json((type(e), e, e.__traceback__))

    assert [
        {
            "exc_type": "ZeroDivisionError",
            "exc_value": "division by zero",
            "exc_notes": [],
            "exceptions": [],
            "frames": [
                {
                    "filename": __file__,
                    "lineno": lineno,
                    "name": "test_json_traceback",
                }
            ],
            "is_cause": False,
            "is_group": False,
            "syntax_error": None,
        },
    ] == result


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_with_notes():
    """
    Tracebacks are formatted to JSON with all information.
    """
    try:
        lineno = get_next_lineno()
        1 / 0
    except Exception as e:
        e.add_note("This is a note.")
        e.add_note("This is another note.")
        format_json = tracebacks.ExceptionDictTransformer(show_locals=False)
        result = format_json((type(e), e, e.__traceback__))

    assert [
        {
            "exc_type": "ZeroDivisionError",
            "exc_value": "division by zero",
            "exc_notes": ["This is a note.", "This is another note."],
            "exceptions": [],
            "frames": [
                {
                    "filename": __file__,
                    "lineno": lineno,
                    "name": "test_json_traceback_with_notes",
                }
            ],
            "is_cause": False,
            "is_group": False,
            "syntax_error": None,
        },
    ] == result


def test_json_traceback_locals_max_string():
    """
    Local variables in each frame are trimmed to locals_max_string.
    """
    try:
        _var = "spamspamspam"
        lineno = get_next_lineno()
        1 / 0
    except Exception as e:
        result = tracebacks.ExceptionDictTransformer(locals_max_string=4)(
            (type(e), e, e.__traceback__)
        )
    assert [
        {
            "exc_type": "ZeroDivisionError",
            "exc_value": "division by zero",
            "exc_notes": [],
            "exceptions": [],
            "frames": [
                {
                    "filename": __file__,
                    "lineno": lineno,
                    "locals": {
                        "_var": "'spam'+8",
                        "e": "'Zero'+33",
                        "lineno": str(lineno),
                    },
                    "name": "test_json_traceback_locals_max_string",
                }
            ],
            "is_cause": False,
            "is_group": False,
            "syntax_error": None,
        },
    ] == result


@pytest.mark.parametrize(
    ("max_frames", "expected_frames", "skipped_idx", "skipped_count"),
    [
        (2, 3, 1, 2),
        (3, 3, 1, 2),
        (4, 4, -1, 0),
        (5, 4, -1, 0),
    ],
)
def test_json_traceback_max_frames(
    max_frames: int, expected_frames: int, skipped_idx: int, skipped_count: int
):
    """
    Only max_frames frames are included in the traceback and the skipped frames
    are reported.
    """

    def spam():
        return 1 / 0

    def eggs():
        spam()

    def bacon():
        eggs()

    try:
        bacon()
    except Exception as e:
        format_json = tracebacks.ExceptionDictTransformer(
            show_locals=False, max_frames=max_frames
        )
        result = format_json((type(e), e, e.__traceback__))
        trace = result[0]
        assert len(trace["frames"]) == expected_frames, trace["frames"]
        if skipped_count:
            assert trace["frames"][skipped_idx] == {
                "filename": "",
                "lineno": -1,
                "name": f"Skipped frames: {skipped_count}",
            }
        else:
            assert not any(f["lineno"] == -1 for f in trace["frames"])


@pytest.mark.parametrize(
    ("suppress", "file_no_locals"),
    [
        pytest.param((__file__,), __file__, id="file"),
        pytest.param((json,), json.__file__, id="json"),
    ],
)
def test_json_tracebacks_suppress(
    suppress: tuple[str | ModuleType, ...],
    file_no_locals: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """
    Console and JSON output look as expected

    This means also warnings are logged correctly.
    """
    try:
        try:
            json.loads(42)  # type: ignore[arg-type]
        except Exception as e:
            raise ValueError("error shown to users") from e
    except ValueError as e:
        format_json = tracebacks.ExceptionDictTransformer(
            show_locals=True, suppress=suppress
        )
        result = format_json((type(e), e, e.__traceback__))
        for stack in result:
            for frame in stack["frames"]:
                no_locals = frame["filename"] == file_no_locals
                if no_locals:
                    assert "locals" not in frame
                else:
                    assert "locals" in frame


@pytest.mark.parametrize(
    ("hide_sunder", "hide_dunder", "expected"),
    [
        (False, False, {"_spam", "__eggs"}),
        (True, False, set()),  # Also hides "__eggs", b/c it starts with "_"!
        (False, True, {"_spam"}),
        (True, True, set()),
    ],
)
def test_json_tracebacks_skip_sunder_dunder(
    hide_sunder: bool, hide_dunder: bool, expected: set[str]
) -> None:
    """
    Local variables starting with "_" or "__" can be hidden from the locals
    dict.
    """

    def func() -> None:
        _spam = True
        __eggs = 3
        1 / 0

    try:
        func()
    except ZeroDivisionError as e:
        format_json = tracebacks.ExceptionDictTransformer(
            show_locals=True,
            locals_hide_sunder=hide_sunder,
            locals_hide_dunder=hide_dunder,
        )
        result = format_json((type(e), e, e.__traceback__))
        assert expected == set(result[0]["frames"][1]["locals"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"locals_max_length": -1},
        {"locals_max_string": -1},
        {"max_frames": -1},
        {"max_frames": 0},
        {"max_frames": 1},
    ],
)
def test_json_traceback_value_error(kwargs: dict[str, Any]) -> None:
    """
    Wrong arguments to ExceptionDictTransformer raise a ValueError that
    contains the name of the argument.
    """
    with pytest.raises(ValueError, match=next(iter(kwargs.keys()))):
        tracebacks.ExceptionDictTransformer(**kwargs)


def test_json_traceback_suppress_without_file() -> None:
    """
    Suppressing a module without a __file__ raises a ValueError that contains
    the name of the argument.
    """
    with (
        patch.object(json, "__file__", None),
        pytest.raises(ValueError, match="suppress"),
    ):
        tracebacks.ExceptionDictTransformer(suppress=(json,))


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_exception_groups() -> None:
    """
    When rendered as JSON, the "Trace.stacks" is stripped, so "exceptions" is a
    list of lists and not a list of objects (with a single "stacks" attribute.
    """

    lineno = get_next_lineno()

    async def t1() -> None:
        1 / 0

    async def t2() -> None:
        raise ValueError("Blam!")

    async def main():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(t1())
            tg.create_task(t2())

    try:
        asyncio.run(main())
    except Exception as e:
        format_json = tracebacks.ExceptionDictTransformer(show_locals=False)
        result = format_json((type(e), e, e.__traceback__))

    assert "ExceptionGroup" == result[0]["exc_type"]
    exceptions = result[0]["exceptions"]
    assert [
        [
            {
                "exc_type": "ZeroDivisionError",
                "exc_value": "division by zero",
                "exc_notes": [],
                "syntax_error": None,
                "is_cause": False,
                "frames": [
                    {
                        "filename": __file__,
                        "lineno": lineno + 2,
                        "name": "t1",
                    }
                ],
                "is_group": False,
                "exceptions": [],
            }
        ],
        [
            {
                "exc_type": "ValueError",
                "exc_value": "Blam!",
                "exc_notes": [],
                "syntax_error": None,
                "is_cause": False,
                "frames": [
                    {
                        "filename": __file__,
                        "lineno": lineno + 5,
                        "name": "t2",
                    }
                ],
                "is_group": False,
                "exceptions": [],
            }
        ],
    ] == exceptions


class TestLogException:
    """
    Higher level integration tests for `Logger.exception()`.
    """

    @pytest.fixture
    def cap_logs(self) -> structlog.testing.LogCapture:
        """
        Create a LogCapture to be used as processor and fixture for retrieving
        logs in tests.
        """
        return structlog.testing.LogCapture()

    @pytest.fixture
    def logger(
        self, cap_logs: structlog.testing.LogCapture
    ) -> structlog.Logger:
        """
        Create a logger with the dict_tracebacks and a LogCapture processor.
        """
        old_processors = structlog.get_config()["processors"]
        structlog.configure([structlog.processors.dict_tracebacks, cap_logs])
        logger = structlog.get_logger("dict_tracebacks")
        try:
            yield logger
        finally:
            structlog.configure(processors=old_processors)

    def test_log_explicit_exception(
        self, logger: structlog.Logger, cap_logs: structlog.testing.LogCapture
    ) -> None:
        """
        The log row contains a traceback when `Logger.exception()` is
        explicitly called with an exception instance.
        """
        try:
            1 / 0
        except ZeroDivisionError as e:
            logger.exception("onoes", exception=e)

        log_row = cap_logs.entries[0]

        assert log_row["exception"][0]["exc_type"] == "ZeroDivisionError"

    def test_log_implicit_exception(
        self, logger: structlog.Logger, cap_logs: structlog.testing.LogCapture
    ) -> None:
        """
        The log row contains a traceback when `Logger.exception()` is called
        in an `except` block but without explicitly passing the exception.
        """
        try:
            1 / 0
        except ZeroDivisionError:
            logger.exception("onoes")

        log_row = cap_logs.entries[0]

        assert log_row["exception"][0]["exc_type"] == "ZeroDivisionError"

    def test_no_exception(
        self, logger: structlog.Logger, cap_logs: structlog.testing.LogCapture
    ) -> None:
        """
        `Logger.exception()` should not be called outside an `except` block
        but this cases is gracefully handled and does not lead to an exception.

        See: https://github.com/hynek/structlog/issues/634
        """
        logger.error("onoes")

        assert [{"event": "onoes", "log_level": "error"}] == cap_logs.entries


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_reraise_error_from_exception_group():
    """
    There is no RecursionError when building the traceback for an exception
    that has been re-raised from an ExceptionGroup.
    """
    inner_lineno = None
    lineno = None

    try:
        try:
            inner_lineno = get_next_lineno()
            raise ExceptionGroup(  # noqa: F821
                "Some error occurred",
                [ValueError("value error")],
            )
        except ExceptionGroup as e:  # noqa: F821
            lineno = get_next_lineno()
            raise e.exceptions[0]  # noqa: B904
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert lineno is not None
    assert inner_lineno is not None
    assert 2 == len(trace.stacks)
    assert lineno == trace.stacks[0].frames[0].lineno
    assert (
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="value error",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_reraise_error_from_exception_group",
                    locals=None,
                )
            ],
            is_group=False,
            exceptions=[],
        )
        == trace.stacks[0]
    )
    assert (
        tracebacks.Stack(
            exc_type="ExceptionGroup",
            exc_value="Some error occurred (1 sub-exception)",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=inner_lineno,
                    name="test_reraise_error_from_exception_group",
                    locals=None,
                ),
            ],
            is_group=True,
            exceptions=[tracebacks.Trace(stacks=[])],
        )
        == trace.stacks[1]
    )


def test_exception_cycle():
    """
    There is no RecursionError when building the traceback for an exception
    that has itself in its cause chain.
    """
    inner_lineno = None
    lineno = None

    try:
        try:
            exc = ValueError("onoes")
            inner_lineno = get_next_lineno()
            raise exc
        except Exception as exc:
            lineno = get_next_lineno()
            raise exc from exc
    except Exception as e:
        trace = tracebacks.extract(type(e), e, e.__traceback__)

    assert lineno is not None
    assert inner_lineno is not None
    assert 1 == len(trace.stacks)
    assert lineno == trace.stacks[0].frames[0].lineno
    assert (
        tracebacks.Stack(
            exc_type="ValueError",
            exc_value="onoes",
            syntax_error=None,
            is_cause=False,
            frames=[
                tracebacks.Frame(
                    filename=__file__,
                    lineno=lineno,
                    name="test_exception_cycle",
                    locals=None,
                ),
                tracebacks.Frame(
                    filename=__file__,
                    lineno=inner_lineno,
                    name="test_exception_cycle",
                    locals=None,
                ),
            ],
            is_group=False,
            exceptions=[],
        )
        == trace.stacks[0]
    )


def _raised(exc: BaseException) -> BaseException:
    """Raise *exc* and return it, so it has a traceback attached."""
    try:
        raise exc
    except BaseException as e:
        return e


def _walk_stack_dicts(
    stack_dicts: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Yield all stack dicts of a rendered exception tree, depth first."""
    for stack_dict in stack_dicts:
        yield stack_dict
        for trace in stack_dict["exceptions"]:
            yield from _walk_stack_dicts(trace)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_exceptions": -1}, ValueError),
        ({"max_exceptions": True}, TypeError),
        ({"max_exceptions": 1.5}, TypeError),
        ({"max_exceptions": "3"}, TypeError),
        ({"max_total_frames": -1}, ValueError),
        ({"max_total_frames": False}, TypeError),
        ({"max_total_frames": 2.0}, TypeError),
        ({"max_group_depth": -2}, ValueError),
        ({"max_group_depth": True}, TypeError),
        ({"max_group_depth": "0"}, TypeError),
    ],
)
def test_json_traceback_budget_validation(
    kwargs: dict[str, Any], error: type[Exception]
) -> None:
    """
    Negative, boolean and non-integer budgets are rejected with a clear
    error naming the argument.
    """
    with pytest.raises(error, match=next(iter(kwargs.keys()))):
        tracebacks.ExceptionDictTransformer(**kwargs)


def test_extract_budget_validation() -> None:
    """
    extract() validates its budget arguments the same way as
    ExceptionDictTransformer.
    """
    exc = ValueError("onoes")

    with pytest.raises(TypeError, match="max_exceptions"):
        tracebacks.extract(type(exc), exc, None, max_exceptions=True)

    with pytest.raises(ValueError, match="max_total_frames"):
        tracebacks.extract(type(exc), exc, None, max_total_frames=-1)


def test_json_traceback_budgets_not_exceeded() -> None:
    """
    Configuring budgets that are not exceeded does not change the output,
    and no "truncated" keys appear.
    """
    try:
        1 / 0
    except ZeroDivisionError as e:
        exc_info = (type(e), e, e.__traceback__)

    plain = tracebacks.ExceptionDictTransformer(show_locals=False)(exc_info)
    budgeted = tracebacks.ExceptionDictTransformer(
        show_locals=False,
        max_exceptions=10,
        max_total_frames=100,
        max_group_depth=10,
    )(exc_info)

    assert plain == budgeted
    assert all(
        "truncated" not in stack_dict
        for stack_dict in _walk_stack_dicts(budgeted)
    )


def test_json_traceback_zero_max_exceptions() -> None:
    """
    A zero exception budget is legal: every exception is replaced by a
    single truncation marker and the result stays JSON-serializable.
    """
    exc = _raised(ValueError("onoes"))
    result = tracebacks.ExceptionDictTransformer(max_exceptions=0)(
        (type(exc), exc, exc.__traceback__)
    )

    assert 1 == len(result)
    assert "max_exceptions" == result[0]["truncated"]
    json.dumps(result)


def test_json_traceback_zero_max_total_frames() -> None:
    """
    A zero total frame budget keeps the exceptions but replaces all frames
    by a marker frame.
    """
    exc = _raised(ValueError("onoes"))
    result = tracebacks.ExceptionDictTransformer(max_total_frames=0)(
        (type(exc), exc, exc.__traceback__)
    )

    assert "ValueError" == result[0]["exc_type"]
    assert "onoes" == result[0]["exc_value"]
    assert 1 == len(result[0]["frames"])
    marker = result[0]["frames"][0]
    assert -1 == marker["lineno"]
    assert marker["name"].startswith("Skipped frames: ")
    assert "max_total_frames" in marker["name"]
    json.dumps(result)


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_zero_max_group_depth() -> None:
    """
    A zero group depth budget keeps the group itself but does not expand
    its children.
    """
    group = _raised(
        ExceptionGroup("group", [_raised(ValueError("leaf"))])  # noqa: F821
    )
    result = tracebacks.ExceptionDictTransformer(max_group_depth=0)(
        (type(group), group, group.__traceback__)
    )

    assert "ExceptionGroup" == result[0]["exc_type"]  # noqa: F821
    assert result[0]["is_group"]
    assert 1 == len(result[0]["exceptions"])
    assert "max_group_depth" == result[0]["exceptions"][0][0]["truncated"]
    json.dumps(result)


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_max_exceptions_wide_group() -> None:
    """
    The exception budget is shared by all children of a group: children
    are kept in order until the budget is exhausted and the rest is
    replaced by one marker.
    """
    children = [_raised(ValueError(f"error {i}")) for i in range(5)]
    group = _raised(ExceptionGroup("wide", children))  # noqa: F821

    result = tracebacks.ExceptionDictTransformer(max_exceptions=3)(
        (type(group), group, group.__traceback__)
    )

    assert "ExceptionGroup" == result[0]["exc_type"]  # noqa: F821
    traces = result[0]["exceptions"]
    assert 3 == len(traces)
    # The first two children are kept, in order, with their data intact.
    assert "error 0" == traces[0][0]["exc_value"]
    assert "error 1" == traces[1][0]["exc_value"]
    assert "truncated" not in traces[0][0]
    assert "truncated" not in traces[1][0]
    # The remaining three children are replaced by one marker.
    assert "max_exceptions" == traces[2][0]["truncated"]
    json.dumps(result)


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_max_exceptions_shared_cause() -> None:
    """
    A cause that is shared between several group children is extracted
    only once (existing _seen handling) and therefore consumes the
    exception budget only once.
    """
    shared = _raised(ValueError("shared cause"))
    children = []
    for i in range(3):
        try:
            raise RuntimeError(f"child {i}") from shared
        except RuntimeError as child:
            children.append(child)
    group = _raised(ExceptionGroup("group", children))  # noqa: F821

    # 1 group + 3 children + 1 shared cause = 5 nodes: no truncation.
    result = tracebacks.ExceptionDictTransformer(max_exceptions=5)(
        (type(group), group, group.__traceback__)
    )

    assert all(
        "truncated" not in stack_dict
        for stack_dict in _walk_stack_dicts(result)
    )
    traces = result[0]["exceptions"]
    assert 3 == len(traces)
    # The shared cause is only rendered below the first child.
    assert ["RuntimeError", "ValueError"] == [
        stack["exc_type"] for stack in traces[0]
    ]
    assert traces[0][1]["is_cause"]
    assert "shared cause" == traces[0][1]["exc_value"]
    assert 1 == len(traces[1])
    assert 1 == len(traces[2])

    # One node less and the last child is replaced by a marker.
    result = tracebacks.ExceptionDictTransformer(max_exceptions=4)(
        (type(group), group, group.__traceback__)
    )
    traces = result[0]["exceptions"]
    assert "max_exceptions" == traces[2][0]["truncated"]
    json.dumps(result)


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_max_group_depth() -> None:
    """
    Groups nested deeper than max_group_depth are kept as nodes (with
    type, message and notes) but their children are not extracted; a
    marker takes their place.
    """
    leaf = _raised(ValueError("leaf"))
    inner = _raised(ExceptionGroup("inner", [leaf]))  # noqa: F821
    inner.add_note("inner note")
    outer = _raised(ExceptionGroup("outer", [inner]))  # noqa: F821

    result = tracebacks.ExceptionDictTransformer(max_group_depth=1)(
        (type(outer), outer, outer.__traceback__)
    )

    inner_dict = result[0]["exceptions"][0][0]
    assert "ExceptionGroup" == inner_dict["exc_type"]  # noqa: F821
    assert "inner (1 sub-exception)" == inner_dict["exc_value"]
    assert ["inner note"] == inner_dict["exc_notes"]
    assert inner_dict["is_group"]
    assert not inner_dict["is_cause"]
    # The inner group's children are not extracted.
    assert 1 == len(inner_dict["exceptions"])
    marker = inner_dict["exceptions"][0][0]
    assert "max_group_depth" == marker["truncated"]
    json.dumps(result)

    # Without a depth budget, everything is expanded.
    result = tracebacks.ExceptionDictTransformer()(
        (type(outer), outer, outer.__traceback__)
    )
    leaf_dict = result[0]["exceptions"][0][0]["exceptions"][0][0]
    assert "ValueError" == leaf_dict["exc_type"]
    assert "leaf" == leaf_dict["exc_value"]


class _CountingRepr:
    """Counts how often its repr is taken."""

    calls = 0

    def __repr__(self) -> str:
        type(self).calls += 1
        return "CountingRepr()"


def _raise_with_counting_locals() -> ValueError:
    def level1() -> None:
        local1 = _CountingRepr()
        level2()
        _ = local1

    def level2() -> None:
        local2 = _CountingRepr()
        level3()
        _ = local2

    def level3() -> None:
        local3 = _CountingRepr()
        raise ValueError("deep")
        _ = local3

    try:
        level1()
    except ValueError as e:
        return e

    raise AssertionError("unreachable")  # pragma: no cover


@pytest.mark.parametrize("use_rich", [False, True])
def test_json_traceback_max_total_frames(use_rich: bool) -> None:
    """
    The total frame budget is shared by all stacks.  Frames beyond the
    budget are not extracted: their locals are never repr'd.  A marker
    frame with the number of skipped frames takes their place.
    """
    _CountingRepr.calls = 0
    exc = _raise_with_counting_locals()

    result = tracebacks.ExceptionDictTransformer(
        show_locals=True,
        max_total_frames=3,
        use_rich=use_rich,
    )((type(exc), exc, exc.__traceback__))

    frames = result[0]["frames"]
    # 3 real frames (test helper, level1, level2) + 1 marker for level3.
    assert 4 == len(frames)
    assert "level1" == frames[1]["name"]
    assert "level2" == frames[2]["name"]
    assert "CountingRepr()" == frames[1]["locals"]["local1"]
    assert "CountingRepr()" == frames[2]["locals"]["local2"]
    marker = frames[3]
    assert -1 == marker["lineno"]
    assert "Skipped frames: 1 (max_total_frames)" == marker["name"]
    assert "locals" not in marker
    # local3's repr was never taken because its frame was over budget.
    assert 2 == _CountingRepr.calls
    json.dumps(result)


@pytest.mark.skipif(rich is None, reason="rich not installed")
def test_json_traceback_max_total_frames_rich() -> None:
    """
    The frame budget behaves the same when rich renders the locals.
    """
    _CountingRepr.calls = 0
    exc = _raise_with_counting_locals()

    with patch.object(tracebacks, "rich", rich):
        result = tracebacks.ExceptionDictTransformer(
            show_locals=True,
            max_total_frames=3,
            use_rich=True,
        )((type(exc), exc, exc.__traceback__))

    marker = result[0]["frames"][3]
    assert "Skipped frames: 1 (max_total_frames)" == marker["name"]
    assert 2 == _CountingRepr.calls


def test_json_traceback_max_total_frames_with_suppress() -> None:
    """
    suppress and the total frame budget compose: kept frames honor
    suppress, the marker frame is unaffected.
    """
    exc = _raise_with_counting_locals()

    result = tracebacks.ExceptionDictTransformer(
        show_locals=True,
        max_total_frames=3,
        suppress=(str(Path(__file__).parent),),
    )((type(exc), exc, exc.__traceback__))

    frames = result[0]["frames"]
    assert 4 == len(frames)
    assert all("locals" not in frame for frame in frames)
    assert "Skipped frames: 1 (max_total_frames)" == frames[3]["name"]


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Requires Python 3.11 or higher"
)
def test_json_traceback_combined_budgets() -> None:
    """
    All three budgets apply together, share one traversal and leave a
    JSON-serializable result with markers at the truncation points.
    """

    def make_leaf(msg: str) -> BaseException:
        return _raised(ValueError(msg))

    inner_a = _raised(
        ExceptionGroup("a", [make_leaf("a1"), make_leaf("a2")])  # noqa: F821
    )
    inner_b = _raised(
        ExceptionGroup("b", [make_leaf("b1"), make_leaf("b2")])  # noqa: F821
    )
    outer = _raised(ExceptionGroup("outer", [inner_a, inner_b]))  # noqa: F821

    result = tracebacks.ExceptionDictTransformer(
        show_locals=True,
        max_exceptions=5,
        max_total_frames=4,
        max_group_depth=2,
    )((type(outer), outer, outer.__traceback__))

    # JSON-serializable as promised.
    json.dumps(result)

    stacks = list(_walk_stack_dicts(result))
    kept = [s for s in stacks if "truncated" not in s]
    markers = [s for s in stacks if "truncated" in s]

    # The node budget is exact and shared: outer, a, a1, a2, b are kept
    # (depth-first, in order); b's children are replaced by a marker.
    assert 5 == len(kept)
    assert [
        "ExceptionGroup",
        "ExceptionGroup",
        "ValueError",
        "ValueError",
        "ExceptionGroup",
    ] == [
        s["exc_type"] for s in kept
    ]
    assert ["max_exceptions"] == [m["truncated"] for m in markers]

    # The frame budget is shared across stacks: at most 4 real frames.
    real_frames = [
        frame
        for stack in kept
        for frame in stack["frames"]
        if frame["lineno"] != -1
    ]
    assert 4 == len(real_frames)
    # Truncated stacks carry a marker frame instead.
    skipped = [
        frame
        for stack in kept
        for frame in stack["frames"]
        if frame["lineno"] == -1
    ]
    assert skipped
    assert all("max_total_frames" in frame["name"] for frame in skipped)
