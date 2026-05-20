"""Params dataclass auto-population from os.environ.

Action scripts declare `Params: ClassVar[type] = SomeDataclass` on their
BaseAction subclass. The framework reads each field of that dataclass from
the env and constructs the instance.

Supported field types:
  - str                          (required unless field has a default)
  - Optional[str] / str | None   (defaults to None / declared default)
  - int
  - bool                          (truthy parse: "1", "true", "yes", "on")
  - float

If you need a list of values, declare the field as `str` and split it
yourself (e.g. `params.SYMBOLS.split(",")`). The framework does not parse
multi-value env vars.
"""

from __future__ import annotations

import os
import types
import typing
from dataclasses import MISSING, fields
from typing import Any


def _parse_bool(s: str) -> bool:
    return s.strip().lower() in {"1", "true", "yes", "on"}


def _is_union(origin: Any) -> bool:
    return origin is typing.Union or origin is types.UnionType


def _has_default(field: Any) -> bool:
    return field.default is not MISSING or field.default_factory is not MISSING


def _field_default(field: Any) -> Any:
    if field.default is not MISSING:
        return field.default
    return field.default_factory()


def _coerce(
    cls_name: str,
    field_name: str,
    raw: str,
    target: Any,
) -> Any:
    """Convert a raw env-var string to the field's target type."""
    if target is str:
        return raw
    if target is int:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{cls_name}.{field_name}: cannot parse {raw!r} as int"
            ) from exc
    if target is bool:
        return _parse_bool(raw)
    if target is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(
                f"{cls_name}.{field_name}: cannot parse {raw!r} as float"
            ) from exc
    raise TypeError(
        f"{cls_name}.{field_name}: unsupported field type {target!r}; "
        f"supported: str, int, bool, float, Optional[str]"
    )


def build_params_from_env(cls: type) -> Any:
    """Construct a dataclass instance by reading os.environ for each field.

    Raises:
      - KeyError if a required field (no default) is missing from env.
      - ValueError if a value cannot be coerced to the declared type.
      - TypeError if `cls` is not a dataclass, or a field has an unsupported type.
    """
    if not hasattr(cls, "__dataclass_fields__"):
        raise TypeError(f"{cls.__name__} is not a dataclass")

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}

    for f in fields(cls):
        name = f.name
        type_hint = hints.get(name, str)
        origin = typing.get_origin(type_hint)
        args = typing.get_args(type_hint)

        raw = os.environ.get(name)
        if raw is None:
            if _has_default(f):
                kwargs[name] = _field_default(f)
                continue
            raise KeyError(
                f"Required env var {name!r} not set "
                f"(declared by {cls.__name__})"
            )

        # Optional[T] / T | None — pick the non-None member.
        if _is_union(origin):
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) != 1:
                raise TypeError(
                    f"{cls.__name__}.{name}: unsupported union type "
                    f"{type_hint!r}; only Optional[T] is supported"
                )
            target = non_none[0]
        else:
            target = type_hint

        kwargs[name] = _coerce(cls.__name__, name, raw, target)

    return cls(**kwargs)
