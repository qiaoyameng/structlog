# Testing

*structlog* comes with tools for testing the logging behavior of your application.

If you need functionality similar to {meth}`unittest.TestCase.assertLogs`, or you want to capture all logs for some other reason, you can use the {func}`structlog.testing.capture_logs` context manager:

```{doctest}
>>> from structlog import get_logger
>>> from structlog.testing import capture_logs
>>> with capture_logs() as cap_logs:
...    get_logger().bind(x="y").info("hello")
>>> cap_logs
[{'x': 'y', 'event': 'hello', 'log_level': 'info'}]
```

Note that inside the context manager all configured processors are disabled.

If you want to capture logs after certain processors have been applied, they can be passed to the context manager.
For example, to capture {doc}`contextvars`:

```{doctest}
>>> from structlog import contextvars, get_logger
>>> from structlog.testing import capture_logs
>>> with capture_logs(processors=[contextvars.merge_contextvars]) as cap_logs:
...     _ = contextvars.bind_contextvars(x="y")
...     get_logger().info("hello")
>>> cap_logs
[{'event': 'hello', 'x': 'y', 'log_level': 'info'}]
```

:::{note}
`capture_logs()` relies on changing the configuration.
If you have *cache_logger_on_first_use* enabled for {doc}`performance <performance>`, any cached loggers will not be affected, so it’s recommended you do not enable it during tests.
:::

If you run tests concurrently (for example, using *pytest-xdist* with threaded event loops, or `asyncio` tests that run in parallel tasks), changing the global configuration is not an option.
In that case, use the *local* mode that captures only events from the current execution context and leaves the global configuration untouched:

```{doctest}
>>> from structlog import get_logger
>>> from structlog.testing import capture_logs
>>> with capture_logs(local=True) as cap_logs:
...    get_logger().bind(x="y").info("hello")
>>> cap_logs
[{'x': 'y', 'event': 'hello', 'log_level': 'info'}]
```

In local mode:

- The global processor chain is never modified, so loggers from other threads, or from asyncio tasks that existed before the block, keep using the regular configuration.
- Bound loggers that were created and cached before entering the block are captured too, because the capture is looked up when the event is processed, not when the logger is built.
- Asyncio tasks created within the block inherit the capture.
  Once the block exits, tasks that are still running stop capturing and resume regular logging; they never append to the already-returned list.
- Captures can be nested; only the innermost active capture collects an event.

You can build your own helpers using {class}`structlog.testing.LogCapture`.
For example a [*pytest*](https://docs.pytest.org/) fixture to capture log output could look like this:

```
@pytest.fixture(name="log_output")
def fixture_log_output():
    return LogCapture()

@pytest.fixture(autouse=True)
def fixture_configure_structlog(log_output):
    structlog.configure(
        processors=[log_output]
    )

def test_my_stuff(log_output):
    do_something()
    assert log_output.entries == [...]
```

---

You can also use {class}`structlog.testing.CapturingLogger` (directly, or via {class}`~structlog.testing.CapturingLoggerFactory` that always returns the same logger) that is more low-level and great for unit tests:

```{doctest}
>>> import structlog
>>> cf = structlog.testing.CapturingLoggerFactory()
>>> structlog.configure(logger_factory=cf, processors=[structlog.processors.JSONRenderer()])
>>> log = get_logger()
>>> log.info("test!")
>>> cf.logger.calls
[CapturedCall(method_name='info', args=('{"event": "test!"}',), kwargs={})]
```

```{testcleanup}
import structlog
structlog.reset_defaults()
```

---

Additionally *structlog* also ships with a logger that just returns whatever it gets passed into it: {class}`structlog.testing.ReturnLogger`.

```{doctest}
>>> from structlog import ReturnLogger
>>> ReturnLogger().info(42) == 42
True
>>> obj = ["hi"]
>>> ReturnLogger().info(obj) is obj
True
>>> ReturnLogger().info("hello", when="again")
(('hello',), {'when': 'again'})
```
