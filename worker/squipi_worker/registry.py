"""Typed tool registry. Each tool declares a JSON-Schema for its arguments; arguments are
validated here before a handler runs, so a model can only ask for operations that exist, with
parameters of the declared shape. The schema is also what the app advertises to models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Tuple, Any, Callable, Dict, List, Optional

from .security import AccessDenied


# Network permission comes from the host, never from model-supplied tool arguments.
NETWORK_TOOLS = frozenset({"search_papers", "search_web", "fetch_web", "resolve_citation", "brightdata_search", "brightdata_fetch"})


class InvalidArguments(ValueError):
    pass


class Cancelled(Exception):
    pass


class JobContext:
    """Passed to every tool: progress reporting and cooperative cancellation."""

    def __init__(self, project, report: Callable[[int, str], None], cancelled: Callable[[], bool],
                 *, allow_network: bool = False):
        self.project = project
        self._report = report
        self._cancelled = cancelled
        self.allow_network = allow_network is True

    def progress(self, pct: int, note: str = "") -> None:
        self.check()
        self._report(int(max(0, min(100, pct))), note)

    def check(self) -> None:
        if self._cancelled():
            raise Cancelled()


@dataclass
class Tool:
    name: str
    description: str
    params: Dict[str, Any]                      # JSON Schema (object)
    handler: Callable[[JobContext, Dict[str, Any]], Dict[str, Any]]
    needs_model: bool = False                   # deterministic tools never need a model
    tested: bool = True                         # shown in the feature-status table
    examples: List[Dict[str, Any]] = field(default_factory=list)
    # Optional deterministic argument normalizer, run before schema validation. It takes the raw
    # arguments and returns (arguments, notes). A small model writes "avg", "count_distinct" or the
    # same column in two lists; spelling variants of a legal request are repaired here, never guessed
    # at by another model call. Every change is reported back in the result as `argument_notes`.
    normalize: Optional[Callable[[Dict[str, Any]], "tuple[Dict[str, Any], List[str]]"]] = None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "params": self.params,
                "needs_model": self.needs_model, "tested": self.tested}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate(schema: Dict[str, Any], value: Any, path: str = "args") -> None:
    """A small JSON-Schema subset: type, required, properties, additionalProperties, enum,
    items, minimum/maximum, minLength/maxLength, minItems/maxItems. Enough for tool arguments."""
    if isinstance(value,float) and not math.isfinite(value):
        raise InvalidArguments(f"{path}: numbers must be finite")
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for tt in types:
            if tt == "null" and value is None:
                ok = True
            elif tt in _TYPES and isinstance(value, _TYPES[tt]) and not (tt in ("integer", "number") and isinstance(value, bool)):
                ok = True
        if not ok:
            raise InvalidArguments(f"{path}: expected {t}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidArguments(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise InvalidArguments(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise InvalidArguments(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise InvalidArguments(f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise InvalidArguments(f"{path}: too long")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise InvalidArguments(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise InvalidArguments(f"{path}: at most {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                validate(schema["items"], v, f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value:
                raise InvalidArguments(f"{path}: missing required '{r}'")
        if schema.get("additionalProperties", True) is False:
            extra = set(value) - set(props)
            if extra:
                raise InvalidArguments(f"{path}: unexpected {sorted(extra)}")
        for k, v in value.items():
            if k in props:
                validate(props[k], v, f"{path}.{k}")


class Registry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def describe(self) -> List[Dict[str, Any]]:
        return [self._tools[n].describe() for n in self.names()]

    def call(self, name: str, args: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            raise InvalidArguments(f"unknown tool '{name}'. Known tools: {', '.join(self.names())}")
        if not isinstance(args, dict):
            raise InvalidArguments("args must be an object")
        notes: List[str] = []
        if tool.normalize is not None:
            args, notes = tool.normalize(dict(args))
        args, loose = tolerate_unknown_keys(tool.params, args)
        args, shaped = coerce_shapes(tool.params, args)
        from .tools.derive import derive_arguments     # local import: tools import this module
        args, derived = derive_arguments(name, args, ctx)
        notes = notes + loose + shaped + derived
        validate(tool.params, args)
        if name in NETWORK_TOOLS and not ctx.allow_network:
            raise AccessDenied("Network access is disabled. The user must authorize it in Squipi or start the MCP server with --allow-network.")
        result = _finite(tool.handler(ctx, args))
        if notes and isinstance(result, dict):
            result = dict(result)
            result["argument_notes"] = list(notes) + list(result.get("argument_notes") or [])
            if isinstance(result.get("summary_text"), str):
                result["summary_text"] = result["summary_text"].rstrip() + " Argument adjustments: " + " ".join(notes)
        return result


# Paths and files are not gates (the allow-list checks every path value); permissions and credentials are.
GUARDED_KEY = re.compile(r"(allow|permit|enable|network|online|internet|url|token|secret|password|credential|sudo|admin|unsafe|force|override|bypass|skip)", re.I)


def _finite(v):
    """Replace non-finite floats (inf, nan) with None anywhere in a result: JSON has no inf, and a single
    division by zero in a statistic must not void a whole tool call (28 Oct, compare_groups)."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_finite(x) for x in v]
    if isinstance(v, tuple):
        return [_finite(x) for x in v]
    try:
        import numpy as _np
        if isinstance(v, _np.floating):
            f = float(v); return f if math.isfinite(f) else None
        if isinstance(v, _np.integer):
            return int(v)
    except Exception:
        pass
    return v


def _close_match(key: str, known: List[str]) -> Optional[str]:
    """A known property name within a small edit distance (column → columns, group_by → groupby)."""
    k = key.lower().replace("-", "_").replace(" ", "_")
    for cand in known:
        c = cand.lower()
        if k == c or k.replace("_", "") == c.replace("_", ""):
            return cand
    # An affix spelling of a known name: file_path → path, dataset → dataset_id, group → group_by.
    affix = [cand for cand in known if k.endswith("_" + cand.lower()) or k.startswith(cand.lower() + "_") or cand.lower().startswith(k + "_") or cand.lower().endswith("_" + k)]
    if len(affix) == 1:
        return affix[0]
    best, best_d = None, 3
    for cand in known:
        a, b = k, cand.lower()
        if abs(len(a) - len(b)) > 2:
            continue
        # Levenshtein, small strings only
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        if prev[-1] < best_d:
            best, best_d = cand, prev[-1]
    return best


def coerce_shapes(schema: Dict[str, Any], args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """A single name where a list is declared is a list of one (28 Oct: three identical refusals of
    `columns: "monthly_consumption_inr"`); a comma-separated string is split. Reported, never guessed."""
    if not isinstance(args, dict):
        return args, []
    out = dict(args); notes: List[str] = []
    for k, spec in (schema.get("properties") or {}).items():
        t = spec.get("type"); types = t if isinstance(t, list) else [t]
        v = out.get(k)
        if "array" in types and "string" not in types and isinstance(v, str) and v.strip():
            items = [x.strip() for x in v.split(",")] if "," in v else [v.strip()]
            out[k] = items
            notes.append(f"'{k}' was given as text and read as a list of {len(items)}.")
    return out, notes


def tolerate_unknown_keys(schema: Dict[str, Any], args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """An unknown top-level argument is a loose spelling of a request, not a wrong request (28 Oct 2026:
    randomized_trial was refused twice for `design`). A near-miss is renamed to the schema's name when
    that name is still free; anything else is dropped. Every change is reported, nothing is guessed about
    values, and the schema still validates the result."""
    if not isinstance(args, dict) or schema.get("additionalProperties", True) is not False:
        return args, []
    props = list(schema.get("properties", {}))
    extra = [k for k in args if k not in props]
    if not extra:
        return args, []
    # Permission-like keys are never tolerated: a model may not spell its way past a gate.
    guarded = [k for k in extra if GUARDED_KEY.search(k)]
    if guarded:
        raise InvalidArguments(f"args: unexpected {sorted(guarded)}")
    out = dict(args); notes: List[str] = []
    for k in extra:
        target = _close_match(k, props)
        if target and target not in out:
            out[target] = out.pop(k)
            notes.append(f"'{k}' was read as '{target}'.")
        else:
            out.pop(k)
            notes.append(f"Unknown argument '{k}' was ignored" + (f" (the tool has no such option; known: {', '.join(props[:8])})." if props else "."))
    return out, notes


registry = Registry()


def tool(name: str, description: str, params: Dict[str, Any], **kw):
    """Decorator: register a handler as a tool."""
    def deco(fn):
        registry.add(Tool(name=name, description=description, params=params, handler=fn, **kw))
        return fn
    return deco
