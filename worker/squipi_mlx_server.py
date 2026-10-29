"""Drop-in wrapper around ``python -m mlx_lm server`` with JSON-constrained decoding.

Run it exactly like the stock server::

    python squipi_mlx_server.py --model <dir> --host 127.0.0.1 --port 8766 ...

Same CLI arguments, endpoints and behaviour.  The one addition: a
``/v1/chat/completions`` request carrying ``response_format`` of type
``json_object`` (or ``json_schema``, treated the same way) is decoded under a
JSON grammar (see :mod:`squipi_json_grammar`) so the completion is always one
syntactically valid JSON object.  Requests without ``response_format`` are
served with no processor attached, byte for byte as before.

How the flag travels to the generation thread (three monkeypatch points in
``mlx_lm.server``):

1. ``APIHandler.handle_completion`` (HTTP handler thread) is wrapped: it reads
   ``self.body["response_format"]`` and ``chat_template_kwargs`` and stores
   them in a thread-local for the duration of the original call.
2. ``LogitsProcessorArguments`` is replaced by a dataclass subclass
   (``JSONLogitsProcessorArguments``) whose ``__post_init__`` copies the
   thread-local flag into ``json_mode`` / ``json_thinking``.  ``handle_completion``
   constructs it by module-global name, so the instance that lands in
   ``GenerationArguments.logits`` and travels through the request queue carries
   the flag.
3. ``_make_logits_processors(args)`` (generation thread; called from both the
   batched ``_generate`` path and ``_serve_single``) is wrapped: when
   ``args.logits.json_mode`` is set, a fresh per-request
   :class:`JSONGrammarProcessor` is appended after the stock processors.

The tokenizer is taken from the ``ModelProvider`` captured by wrapping
``mlx_lm.server.run``; the grammar table is built once per tokenizer and cached
under ``$HF_HOME/squipi-json-grammar/``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Optional

# the grammar module lives next to this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mlx_lm.server as mlx_server  # noqa: E402

from squipi_json_grammar import get_table, make_processor, wants_json  # noqa: E402

log = logging.getLogger("squipi.mlx_server")

CHAT_PATHS = ("/v1/chat/completions", "/chat/completions")

_tls = threading.local()


class _State:
    provider: Any = None  # the ModelProvider (set by the patched ``run``)
    warned_no_tokenizer = False


_state = _State()


# ---------------------------------------------------------------------------
# 1. request body -> thread-local (handler thread)

_orig_handle_completion = mlx_server.APIHandler.handle_completion


def _thinking_from_body(body: Any) -> Optional[bool]:
    if not isinstance(body, dict):
        return None
    kwargs = body.get("chat_template_kwargs")
    if isinstance(kwargs, dict) and "enable_thinking" in kwargs:
        return bool(kwargs["enable_thinking"])
    return None


def _handle_completion(self, request, stop_words):
    body = getattr(self, "body", None)
    path = getattr(self, "path", "")
    json_mode = path in CHAT_PATHS and wants_json(body)
    _tls.json_mode = json_mode
    _tls.json_thinking = _thinking_from_body(body) if json_mode else None
    try:
        return _orig_handle_completion(self, request, stop_words)
    finally:
        _tls.json_mode = False
        _tls.json_thinking = None


mlx_server.APIHandler.handle_completion = _handle_completion


# ---------------------------------------------------------------------------
# 2. flag rides along in GenerationArguments.logits

_OrigLogitsProcessorArguments = mlx_server.LogitsProcessorArguments


@dataclass
class JSONLogitsProcessorArguments(_OrigLogitsProcessorArguments):
    json_mode: bool = False
    # None: not specified by the client (template default); True/False: explicit
    json_thinking: Optional[bool] = None

    def __post_init__(self):
        if getattr(_tls, "json_mode", False):
            self.json_mode = True
            self.json_thinking = getattr(_tls, "json_thinking", None)


mlx_server.LogitsProcessorArguments = JSONLogitsProcessorArguments


# ---------------------------------------------------------------------------
# 3. processor factory (generation thread)

_orig_make_logits_processors = mlx_server._make_logits_processors


def _current_tokenizer():
    provider = _state.provider
    return getattr(provider, "tokenizer", None) if provider is not None else None


def _make_logits_processors(args):
    processors = _orig_make_logits_processors(args)
    logits_args = getattr(args, "logits", None)
    if not getattr(logits_args, "json_mode", False):
        return processors
    try:
        tokenizer = _current_tokenizer()
        if tokenizer is None:
            if not _state.warned_no_tokenizer:
                log.warning("json_mode requested but no tokenizer is loaded; serving unconstrained")
                _state.warned_no_tokenizer = True
            return processors
        hint = getattr(logits_args, "json_thinking", None)
        if hint is None:
            # mlx_lm passes enable_thinking=has_thinking when the client does not
            cli_args = getattr(_state.provider, "cli_args", None)
            template_args = getattr(cli_args, "chat_template_args", None) or {}
            if isinstance(template_args, dict) and "enable_thinking" in template_args:
                hint = bool(template_args["enable_thinking"])
            else:
                hint = bool(getattr(tokenizer, "has_thinking", False))
        processor = make_processor(tokenizer, get_table(tokenizer), thinking_hint=hint)
        log.info("JSON-constrained decoding enabled for this request (thinking=%s)", hint)
        return list(processors) + [processor]
    except Exception as e:  # never fail the request because of the grammar
        log.warning("JSON grammar unavailable (%s); serving unconstrained", e)
        return processors


mlx_server._make_logits_processors = _make_logits_processors


# ---------------------------------------------------------------------------
# capture the model provider so the generation thread can reach the tokenizer

_orig_run = mlx_server.run


def _prewarm_table(model_path):
    """Build/load the grammar table from the tokenizer alone so the first JSON
    request does not pay for it on the generation thread (weights untouched)."""
    try:
        from pathlib import Path

        from mlx_lm.utils import load_tokenizer

        tokenizer = load_tokenizer(Path(model_path))
        get_table(tokenizer)
    except Exception as e:  # purely an optimisation
        log.info("JSON grammar pre-warm skipped: %s", e)


def _run(host, port, model_provider, *a, **kw):
    _state.provider = model_provider
    model_path = getattr(getattr(model_provider, "cli_args", None), "model", None)
    if model_path and os.environ.get("SQUIPI_JSON_GRAMMAR_PREWARM", "1") != "0":
        threading.Thread(target=_prewarm_table, args=(model_path,), name="squipi-json-grammar", daemon=True).start()
    return _orig_run(host, port, model_provider, *a, **kw)


mlx_server.run = _run


def main():
    # same CLI as ``python -m mlx_lm server``; a leading ``server`` token (as in
    # the stock ``-m mlx_lm server ...`` invocation) is accepted and dropped
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        del sys.argv[1]
    mlx_server.main()


if __name__ == "__main__":
    main()
