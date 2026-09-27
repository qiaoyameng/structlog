# Exceptions

While you should use a proper crash reporter like [Sentry](https://sentry.io) in production, *structlog* has helpers for formatting exceptions for humans and machines.

All *structlog*'s exception features center around passing an `exc_info` key-value pair in the event dict.
There are three possible behaviors depending on its value:

1. If the value is a tuple, render it as if it was returned by {func}`sys.exc_info`.
2. If the value is an Exception, render it.
3. If the value is true but no tuple, call {func}`sys.exc_info` and render that.

If there is no `exc_info` key or false, the event dict is not touched.
This behavior is analog to the one of the stdlib's logging.


## Transformations

*structlog* comes with {class}`structlog.processors.ExceptionRenderer` that deduces and removes the `exc_info` key as outlined above, calls a user-supplied function with the synthesized `exc_info`, and stores its return value in the `exception` key.
The most common use-cases are already covered by the following processors:

{func}`structlog.processors.format_exc_info`

: Formats it to a flat string like the standard library would on the console.

{obj}`structlog.processors.dict_tracebacks`

: Uses {class}`structlog.tracebacks.ExceptionDictTransformer` to give you a structured and JSON-serializable `exception` key.


### Customizing Dict Tracebacks

{class}`structlog.tracebacks.ExceptionDictTransformer` can be customized with a number of parameters.
If these prove insufficient, see {ref}`Custom Filtering of Dict Tracebacks <custom-dict-tracebacks>`.


### Limiting the Size of Dict Tracebacks

In addition to *max_frames* (which limits each stack individually), {class}`structlog.tracebacks.ExceptionDictTransformer` can bound a whole conversion – including all cause chains and nested exception groups – using three optional budgets that share one traversal:

- *max_exceptions*: the maximum number of exception nodes to extract.
- *max_total_frames*: the maximum total number of frames across all stacks.
- *max_group_depth*: the maximum nesting depth up to which exception groups are expanded (the root exception is at depth 0).

All three default to `None`, which means no limit and unchanged output.
The budgets are enforced while extracting, so frames beyond the budget are never inspected (their locals are not read and no `repr()` is taken of them).

When a budget cuts the output short, a marker is left at the truncation point so you can tell the output is incomplete:

- Omitted exceptions are replaced by a stack dict with a `"truncated"` key naming the exhausted budget, for example `{"exc_type": "", "exc_value": "", ..., "truncated": "max_exceptions"}`.
  The same structure with `"truncated": "max_group_depth"` is used for exception groups whose children were not expanded.
- Omitted frames are replaced by a marker frame like `{"filename": "", "lineno": -1, "name": "Skipped frames: 3 (max_total_frames)"}`.

Exceptions and frames that made it into the output are complete and correct – but a `"truncated"` marker anywhere in the output means the exception tree was *not* fully recorded and the output must not be treated as the full traceback.


## Console rendering

Our {doc}`console-output`'s {class}`structlog.dev.ConsoleRenderer` takes an *exception_formatter* argument that allows for customizing the output of exceptions.

{func}`structlog.dev.plain_traceback`

: Is the default if [Rich] is not installed.
  As the name suggests, it renders a plain traceback.

{class}`structlog.dev.RichTracebackFormatter`

: Uses [Rich] to render a colorful traceback.
  It's a class because it allows for customizing the output by passing arguments to Rich.
: It's the default if Rich is installed.

:::{seealso}
{doc}`console-output` for more information on *structlog*'s console features.
:::

:::{deprecated} 26.1.0
Support for [*better-exceptions*] is deprecated and will be removed in a future release.
Use [Rich] instead.
:::

[*better-exceptions*]: https://github.com/qix-/better-exceptions
[Rich]: https://github.com/Textualize/rich
