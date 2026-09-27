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

Exception groups can be wide or deeply nested, and cause chains can be long, so a single exception can result in a very large `exception` key.
In addition to *max_frames* (which limits each stack individually), {class}`~structlog.tracebacks.ExceptionDictTransformer` offers three optional budgets that apply to one whole conversion and are shared by all nested exception groups and cause chains:

- *max_exceptions*: the maximum total number of exception nodes that are kept.
- *max_total_frames*: the maximum total number of frames that are kept.
  Frames beyond the budget are not extracted at all -- their local variables are neither read nor rendered.
- *max_group_depth*: the maximum nesting depth of exception groups that is expanded.
  The root exception is at depth 0.

All three default to `None`, which means no limit.
Truncation is deterministic and follows the original exception order.
Wherever a budget cuts the output short, a marker is inserted instead:

- a stack whose `exc_type` is {data}`~structlog.tracebacks.TRUNCATED_EXCEPTIONS_MARKER` for skipped exception nodes,
- a frame whose `name` is {data}`~structlog.tracebacks.TRUNCATED_FRAMES_MARKER` for skipped frames,
- a stack whose `exc_type` is {data}`~structlog.tracebacks.TRUNCATED_GROUP_DEPTH_MARKER` for skipped exception group levels.

The nodes that are kept retain their full information (type, message, notes, `is_cause`, and their position in the tree), but the presence of a marker means the exception has *not* been recorded completely -- the `exception` key is a truncated view, not the full exception tree.

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
