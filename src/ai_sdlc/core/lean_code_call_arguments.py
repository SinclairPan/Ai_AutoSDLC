"""静态展开调用实参并按 Python 签名绑定到形参。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_static_values import _literal_dict_values


def _expanded_call_arguments(
    node: ast.Call,
) -> tuple[tuple[ast.expr, ...], dict[str, ast.expr], bool]:
    positional: list[ast.expr] = []
    keywords: dict[str, ast.expr] = {}
    complete = True
    for argument in node.args:
        if not isinstance(argument, ast.Starred):
            positional.append(argument)
            continue
        if isinstance(argument.value, (ast.List, ast.Tuple)) and not any(
            isinstance(item, ast.Starred) for item in argument.value.elts
        ):
            positional.extend(argument.value.elts)
        else:
            complete = False
    for keyword in node.keywords:
        if keyword.arg is not None:
            keywords[keyword.arg] = keyword.value
            continue
        if not isinstance(keyword.value, ast.Dict):
            complete = False
            continue
        values = _literal_dict_values(keyword.value)
        if values is None or not all(isinstance(key, str) for key in values):
            complete = False
            continue
        keywords.update((str(key), value) for key, value in values.items())
    return tuple(positional), keywords, complete


def _bound_call_arguments(
    arguments: ast.arguments,
    node: ast.Call,
) -> tuple[dict[str, ast.expr], bool]:
    positional, keywords, complete = _expanded_call_arguments(node)
    parameters = (*arguments.posonlyargs, *arguments.args)
    bound = {
        parameter.arg: value
        for parameter, value in zip(parameters, positional, strict=False)
    }
    positional_defaults = dict(
        zip(
            parameters[-len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
        if arguments.defaults
        else ()
    )
    positional_only = {parameter.arg for parameter in arguments.posonlyargs}
    for parameter in parameters:
        if parameter.arg in bound:
            continue
        if parameter.arg in keywords and parameter.arg not in positional_only:
            bound[parameter.arg] = keywords[parameter.arg]
        elif parameter in positional_defaults:
            bound[parameter.arg] = positional_defaults[parameter]
    for parameter, default in zip(
        arguments.kwonlyargs,
        arguments.kw_defaults,
        strict=True,
    ):
        if parameter.arg in keywords:
            bound[parameter.arg] = keywords[parameter.arg]
        elif default is not None:
            bound[parameter.arg] = default
    _bind_variadic_arguments(arguments, positional, keywords, parameters, bound)
    return bound, complete


def _bind_variadic_arguments(
    arguments: ast.arguments,
    positional: tuple[ast.expr, ...],
    keywords: dict[str, ast.expr],
    parameters: tuple[ast.arg, ...],
    bound: dict[str, ast.expr],
) -> None:
    if arguments.vararg is not None:
        bound[arguments.vararg.arg] = ast.Tuple(
            elts=list(positional[len(parameters) :]),
            ctx=ast.Load(),
        )
    if arguments.kwarg is not None:
        consumed = {
            parameter.arg
            for parameter in (*parameters, *arguments.kwonlyargs)
        }
        remaining = [
            (name, value)
            for name, value in keywords.items()
            if name not in consumed
        ]
        bound[arguments.kwarg.arg] = ast.Dict(
            keys=[ast.Constant(value=name) for name, _ in remaining],
            values=[value for _, value in remaining],
        )


__all__: list[str] = []
