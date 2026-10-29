"""JSON-constrained decoding for the MLX server: automaton, token table, processor, wrapper.

Needs ``mlx`` and the Qwen tokenizer directory (no model weights are loaded).
Run with the MLX environment's python; skipped cleanly elsewhere.
"""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_lm")

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

MODEL_DIR = Path(
    os.environ.get(
        "SQUIPI_MLX_MODEL_DIR",
        os.path.expanduser("~/Library/Application Support/Squipi/mlx-model"),
    )
)
if not (MODEL_DIR / "tokenizer.json").exists():
    pytest.skip(f"tokenizer directory missing: {MODEL_DIR}", allow_module_level=True)

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np  # noqa: E402

import squipi_json_grammar as g  # noqa: E402

VOCAB = 248320  # the model's logits width (larger than the tokenizer: padding ids)


@pytest.fixture(scope="module")
def tokenizer():
    from mlx_lm.utils import load_tokenizer

    return load_tokenizer(MODEL_DIR)


@pytest.fixture(scope="module")
def table(tokenizer, tmp_path_factory):
    hf_home = tmp_path_factory.mktemp("hf-home")
    old = os.environ.get("HF_HOME")
    os.environ["HF_HOME"] = str(hf_home)
    try:
        g._TABLES.clear()
        t = g.get_table(tokenizer)
        files = list((hf_home / "squipi-json-grammar").glob("*.npz"))
        assert len(files) == 1, "table should be cached to disk under HF_HOME"
        # a second request must come from disk and be identical
        g._TABLES.clear()
        t2 = g.get_table(tokenizer)
        assert np.array_equal(t.allowed, t2.allowed)
        assert t2.eos_ids == t.eos_ids and t2.max_pattern == t.max_pattern
        yield t2
    finally:
        if old is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = old


def _hf(tokenizer):
    return tokenizer._tokenizer


def _ids(tokenizer, text):
    return tokenizer.encode(text, add_special_tokens=False)


def _processor(table):
    return g.JSONGrammarProcessor(table, thinking_hint=False)


def _run_ids(table, ids, tokenizer):
    """Feed ids through the processor; return index of first masked token or -1."""
    p = _processor(table)
    for k, i in enumerate(ids):
        if not p.allowed_now()[i]:
            return k, p
        p.advance(i)
    return -1, p


# ---------------------------------------------------------------------------
# 1. the automaton


VALID = [
    "{}",
    '{"a":1}',
    ' {"a": [1, 2.5e3, true, null], "b": {"c": "x\\"y"}}\n',
    '{"k": "\\u00e9\\n\\t\\\\ \\/"}',
    '{"a":-0.5E+2, "b": 0, "c": -0, "d": 10.25, "e": 1e-7}',
    '{"a":[]}',
    '{"a":{}}',
    '{"a":[[[]], {}, [{}]]}',
    '{"a": "日本語 🙂 ünïcödé"}',
    '{\n\t"x" :\r\n[ 1 , 2 ]\n}',
    '{"a":' + "[" * 31 + "]" * 31 + "}",
]
INVALID = [
    "",
    "{",
    "}",
    '{"a":1,}',
    '{"a":01}',
    '{"a":tru}',
    '{"a":"\\x"}',
    "[1]",
    "1",
    '"s"',
    "true",
    "{a:1}",
    '{"a":1}}',
    '{"a":[1,]}',
    '{"a":"\n"}',
    '{"a":1} {}',
    '{"a":.5}',
    '{"a":1.}',
    '{"a":1e}',
    '{"a"1}',
    '{"a":"\\u12"}',
    '{"a":[1}',
    '{"a":{]}',
    "{,}",
    '{"a":1 "b":2}',
    '{"a":nul}',
    '{"a":True}',
    '{"a":' + "[" * 32 + "]" * 32 + "}",
    "{'a':1}",
]


@pytest.mark.parametrize("text", VALID)
def test_automaton_accepts_valid_json(text):
    assert g.JSONAutomaton.accepts(text)


@pytest.mark.parametrize("text", INVALID)
def test_automaton_rejects_invalid_json(text):
    assert not g.JSONAutomaton.accepts(text)


def test_automaton_prefix_of_valid_is_live():
    text = '{"a": [1, {"b": "c\\u00e9"}], "d": -1.5e2, "e": false}'
    for k in range(len(text)):
        st, _ = g.JSONAutomaton.run_text(text[:k])
        assert st >= 0 and st != g.S_DONE, text[:k]
    assert g.JSONAutomaton.accepts(text)


def test_byte_decomposition():
    assert g.decompose_bytes("abc".encode()) == (0, "abc", 0)
    e = "é".encode("utf-8")  # c3 a9
    assert g.decompose_bytes(e[:1]) == (0, "", 1)
    assert g.decompose_bytes(e[1:]) == (1, "", 0)
    smile = "🙂".encode("utf-8")  # 4 bytes
    assert g.decompose_bytes(smile[:2]) == (0, "", 2)
    assert g.decompose_bytes(smile[2:]) == (2, "", 0)
    assert g.decompose_bytes(b"ab" + smile[:1]) == (0, "ab", 3)
    assert g.decompose_bytes(smile[3:] + b"xy") == (1, "xy", 0)
    assert g.decompose_bytes(b"\xff") is None
    assert g.decompose_bytes(b"") is None


# ---------------------------------------------------------------------------
# 2. the real tokenizer and the precomputed table


JSON_SAMPLES = [
    '{"tool": "aggregate", "arguments": {"by": ["region", "year"], "n": -12.5e3, "ok": true, "none": null}}',
    '{"answer": "The 2024 figures: 1,234 (12.5%) — see \\"Table 3\\".", "blocked": null}',
    '{\n  "a": [1, 2, 3],\n  "b": {"c": [{"d": []}, {}], "e": 0.0, "f": 1E-7}\n}\n',
    '{"emoji": "👩‍👩‍👧‍👦 🇸🇦", "ar": "الرياض", "zh": "你好，世界", "esc": "q\\"x\\u00e9\\n\\t"}',
    '{"nested": {"deep": {"deeper": {"deepest": [true, false, null, 0, -1, 2.5]}}}, "s": "end"}',
]


@pytest.mark.parametrize("text", JSON_SAMPLES)
def test_known_json_tokenization_is_admissible(table, tokenizer, text):
    ids = _ids(tokenizer, text)
    assert len(ids) > 5
    bad, p = _run_ids(table, ids, tokenizer)
    if bad >= 0:
        pytest.fail(f"token {ids[bad]} {_hf(tokenizer).decode([ids[bad]])!r} masked in state {g._STATE_NAMES[p.state]}")
    assert p.state == g.S_DONE
    assert p.allowed_now()[table.eos_ids[0]]


def test_greedy_stub_follows_candidates_and_masks_first_corruption(table, tokenizer):
    """Greedy stub: among a fixed candidate sequence pick the highest-scored *allowed* token."""
    text = '{"tool": "profile_dataset", "arguments": {"path": "data/sales.csv", "pii": "drop"}}'
    ids = _ids(tokenizer, text)
    hf = _hf(tokenizer)
    p = _processor(table)
    hist = mx.array([11, 22, 33], dtype=mx.int32)
    base = mx.zeros((1, VOCAB), dtype=mx.float32)
    out = p(hist, base)  # first call: prompt only
    assert out.shape == (1, VOCAB)
    chosen = []
    for i in ids:
        logits = mx.zeros((1, VOCAB), dtype=mx.float32).at[:, i].add(5.0)  # the model "wants" the right token
        masked = p(hist, logits)
        pick = int(mx.argmax(masked).item())
        assert pick == i, f"expected {hf.decode([i])!r}, got {hf.decode([pick])!r}"
        chosen.append(pick)
        hist = mx.concatenate([hist, mx.array([pick], dtype=mx.int32)])
    assert hf.decode(chosen) == text
    assert not p.disabled

    # corrupted: a trailing comma before the closing brace
    bad_text = '{"a": 1, }'
    bad_ids = _ids(tokenizer, bad_text)
    k, p2 = _run_ids(table, bad_ids, tokenizer)
    assert k >= 0, "corruption must be detected"
    assert "}" in hf.decode([bad_ids[k]])
    assert all(p2.allowed_now()[i] is not None for i in bad_ids[:k])
    # the mask really is -inf for the bad token
    hist = mx.array([1, 2, 3], dtype=mx.int32)
    p3 = _processor(table)
    logits = mx.zeros((1, VOCAB), dtype=mx.float32)
    p3(hist, logits)
    for i in bad_ids[:k]:
        hist = mx.concatenate([hist, mx.array([i], dtype=mx.int32)])
    masked = p3(hist, logits)
    assert masked[0, bad_ids[k]].item() == float("-inf")
    # and whatever was allowed decodes to a legal continuation
    pick = int(mx.argmax(masked).item())
    assert masked[0, pick].item() == 0.0


def test_special_and_padding_tokens_are_never_allowed(table, tokenizer):
    hf = _hf(tokenizer)
    specials = [int(i) for i in hf.added_tokens_decoder]
    p = _processor(table)
    sample = _ids(tokenizer, '{"a": "text ')
    for i in sample:
        p.advance(i)
    allowed = p.allowed_now()
    assert not allowed[specials].any()
    # out-of-tokenizer padding ids in the model's logits are masked
    hist = mx.array([1, 2, 3], dtype=mx.int32)
    logits = mx.zeros((1, VOCAB), dtype=mx.float32)
    p2 = _processor(table)
    out = p2(hist, logits)
    assert out[0, VOCAB - 1].item() == float("-inf")
    assert out[0, hf.convert_tokens_to_ids("{")].item() == 0.0
    assert out[0, hf.convert_tokens_to_ids("[")].item() == float("-inf")


def test_closing_brackets_must_match_stack(table, tokenizer):
    hf = _hf(tokenizer)
    close_obj = hf.convert_tokens_to_ids("}")
    close_arr = hf.convert_tokens_to_ids("]")
    p = _processor(table)
    for i in _ids(tokenizer, '{"a": [1'):
        p.advance(i)
    allowed = p.allowed_now()
    assert allowed[close_arr] and not allowed[close_obj]
    p.advance(close_arr)
    allowed = p.allowed_now()
    assert allowed[close_obj] and not allowed[close_arr]
    # multi-bracket tokens respect the stack too
    p = _processor(table)
    for i in _ids(tokenizer, '{"a": {"b": [1'):
        p.advance(i)
    allowed = p.allowed_now()
    two = _ids(tokenizer, "]}")
    if len(two) == 1:
        assert allowed[two[0]]
    wrong = _ids(tokenizer, "}}")
    if len(wrong) == 1:
        assert not allowed[wrong[0]]
    three = _ids(tokenizer, "]}}")
    if len(three) == 1:
        assert allowed[three[0]]
    # string-closing tokens that also close containers
    quote_close_obj = _ids(tokenizer, '"}}')
    quote_close_arr = _ids(tokenizer, '"]}')
    assert len(quote_close_obj) == 1 and len(quote_close_arr) == 1
    p = _processor(table)
    for i in _ids(tokenizer, '{"a": {"b": "x'):
        p.advance(i)
    allowed = p.allowed_now()
    assert allowed[quote_close_obj[0]] and not allowed[quote_close_arr[0]]
    p = _processor(table)
    for i in _ids(tokenizer, '{"a": ["x'):
        p.advance(i)
    allowed = p.allowed_now()
    assert allowed[quote_close_arr[0]] and not allowed[quote_close_obj[0]]
    p = _processor(table)
    for i in _ids(tokenizer, '{"a": "x'):
        p.advance(i)
    allowed = p.allowed_now()  # depth 1: '"}}' would close one object too many
    assert not allowed[quote_close_obj[0]] and not allowed[quote_close_arr[0]]
    assert allowed[_ids(tokenizer, '"}')[0]]


def test_depth_cap(table, tokenizer):
    hf = _hf(tokenizer)
    open_arr = hf.convert_tokens_to_ids("[")
    p = _processor(table)
    for i in _ids(tokenizer, '{"a":'):
        p.advance(i)
    for _ in range(31):  # depth 1 (object) + 31 arrays = 32
        assert p.allowed_now()[open_arr]
        p.advance(open_arr)
    assert len(p.stack) == g.MAX_DEPTH
    allowed = p.allowed_now()
    assert not allowed[open_arr]
    assert not allowed[hf.convert_tokens_to_ids("{")]
    assert allowed[hf.convert_tokens_to_ids("]")]
    assert allowed[hf.convert_tokens_to_ids("1")]


# ---------------------------------------------------------------------------
# 3. EOS handling


def test_eos_masked_until_object_closed_then_allowed(table, tokenizer):
    eos = table.eos_ids[0]
    text = '{"answer": "ok", "n": [1, 2]}'
    ids = _ids(tokenizer, text)
    p = _processor(table)
    hist = mx.array([5, 6, 7], dtype=mx.int32)
    logits = mx.zeros((1, VOCAB), dtype=mx.float32)
    out = p(hist, logits)
    assert out[0, eos].item() == float("-inf")
    for i in ids:
        hist = mx.concatenate([hist, mx.array([i], dtype=mx.int32)])
        out = p(hist, logits)
        if i != ids[-1]:
            assert out[0, eos].item() == float("-inf"), _hf(tokenizer).decode(ids[: ids.index(i) + 1])
    assert p.state == g.S_DONE
    assert out[0, eos].item() == 0.0
    # in DONE only EOS and whitespace survive
    allowed = p.allowed_now()
    hf = _hf(tokenizer)
    for i in np.flatnonzero(allowed):
        if i == eos:
            continue
        assert hf.decode([int(i)]).strip(" \t\r\n") == ""
    # after a couple of whitespace tokens only EOS is left
    nl = hf.convert_tokens_to_ids("Ċ")  # "\n"
    assert allowed[nl]
    for _ in range(g.DONE_WS_LIMIT):
        p.advance(nl)
    allowed = p.allowed_now()
    assert allowed[eos] and allowed.sum() == 1
    # after EOS the processor is inert
    hist = mx.concatenate([hist, mx.array([eos], dtype=mx.int32)])
    same = p(hist, logits)
    assert same is logits


def test_thinking_block_passes_through_until_think_end(table, tokenizer):
    hf = _hf(tokenizer)
    think_start = tokenizer.think_start_tokens
    think_end = tokenizer.think_end_tokens
    p = g.make_processor(tokenizer, table, thinking_hint=True)
    prompt = [100, 200] + list(think_start) + [hf.convert_tokens_to_ids("Ċ")]
    hist = mx.array(prompt, dtype=mx.int32)
    logits = mx.zeros((1, VOCAB), dtype=mx.float32)
    assert p(hist, logits) is logits  # thinking: untouched
    for i in _ids(tokenizer, "Let me reason about this. The answer is 42."):
        hist = mx.concatenate([hist, mx.array([i], dtype=mx.int32)])
        assert p(hist, logits) is logits
    for i in think_end:
        hist = mx.concatenate([hist, mx.array([i], dtype=mx.int32)])
    out = p(hist, logits)
    assert out is not logits
    assert out[0, hf.convert_tokens_to_ids("{")].item() == 0.0
    assert out[0, hf.convert_tokens_to_ids("ĊĊ")].item() == 0.0  # "\n\n" after </think>
    assert out[0, hf.convert_tokens_to_ids("The")].item() == float("-inf")
    assert not p.disabled
    # a prompt whose thinking is already closed (enable_thinking=false template) is constrained at once
    p2 = g.make_processor(tokenizer, table, thinking_hint=True)
    closed = [100] + list(think_start) + [hf.convert_tokens_to_ids("ĊĊ")] + list(think_end) + [hf.convert_tokens_to_ids("ĊĊ")]
    out2 = p2(mx.array(closed, dtype=mx.int32), logits)
    assert out2 is not logits and out2[0, hf.convert_tokens_to_ids("{")].item() == 0.0


def test_processor_never_raises(table, tokenizer, caplog):
    p = _processor(table)
    logits = mx.zeros((1, VOCAB), dtype=mx.float32)
    hist = mx.array([1, 2, 3], dtype=mx.int32)
    p(hist, logits)
    # a token the grammar forbids (as if the sampler ignored the mask): degrade to passthrough
    bad = _hf(tokenizer).convert_tokens_to_ids("hello")
    hist = mx.concatenate([hist, mx.array([bad], dtype=mx.int32)])
    with caplog.at_level("WARNING", logger="squipi.json_grammar"):
        out = p(hist, logits)
    assert out is logits and p.disabled
    assert any("disabled" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. performance


def test_performance_500_calls_under_two_seconds(table, tokenizer):
    body = ", ".join(f'"key_{k}": {{"text": "value number {k} with words", "n": [{k}, {k}.5, true, null]}}' for k in range(40))
    text = "{" + body + "}"
    ids = _ids(tokenizer, text)
    assert len(ids) >= 500
    p = _processor(table)
    hist = mx.array(list(range(10)), dtype=mx.int32)
    logits = mx.zeros((1, VOCAB), dtype=mx.bfloat16)
    mx.eval(hist, logits)
    out = p(hist, logits)
    mx.eval(out)
    # Time the processor calls themselves (state tracking, mask lookup, lazy
    # add).  The eval of the result stands in for the sampler's work and is
    # timed separately: it is a GPU round trip whose cost depends on what else
    # is running on the machine.
    t_call = 0.0
    t_eval = 0.0
    n = 0
    for i in ids[:500]:
        t0 = time.perf_counter()
        out = p(hist, logits)
        t1 = time.perf_counter()
        mx.eval(out)
        t_eval += time.perf_counter() - t1
        t_call += t1 - t0
        hist = mx.concatenate([hist, mx.array([i], dtype=mx.int32)])
        n += 1
    assert not p.disabled
    assert n == 500
    print(f"\n500 processor calls: {t_call:.3f}s ({t_call / n * 1000:.3f} ms/step; result eval {t_eval / n * 1000:.3f} ms/step)")
    assert t_call < 2.0


# ---------------------------------------------------------------------------
# 5. the server wrapper


@pytest.fixture(scope="module")
def wrapper():
    import squipi_mlx_server as w

    return w


def test_wrapper_factory_passthrough_without_json_flag(wrapper, table, tokenizer):
    S = wrapper.mlx_server
    assert S._make_logits_processors is wrapper._make_logits_processors
    assert S.LogitsProcessorArguments is wrapper.JSONLogitsProcessorArguments
    assert S.APIHandler.handle_completion is wrapper._handle_completion
    assert S.run is wrapper._run

    def make_args(logits_cls, **extra):
        return S.GenerationArguments(
            model=S.ModelDescription(model="m", draft="d", adapter=None),
            sampling=S.SamplingArguments(0.0, 1.0, 0, 0.0, 0.0, 0.1),
            logits=logits_cls(
                logit_bias=extra.pop("logit_bias", None),
                repetition_penalty=0.0,
                repetition_context_size=20,
                presence_penalty=0.0,
                presence_context_size=20,
                frequency_penalty=0.0,
                frequency_context_size=20,
                **extra,
            ),
            stop_words=[],
            max_tokens=16,
            num_draft_tokens=0,
            logprobs=False,
            top_logprobs=-1,
            seed=None,
            chat_template_kwargs=None,
        )

    # no flag (plain dataclass, as an un-patched handler would build): identical to upstream
    for cls in (wrapper._OrigLogitsProcessorArguments, wrapper.JSONLogitsProcessorArguments):
        args = make_args(cls)
        assert S._make_logits_processors(args) == wrapper._orig_make_logits_processors(args) == []
        args = make_args(cls, logit_bias={5: 1.0})
        ours = S._make_logits_processors(args)
        assert len(ours) == len(wrapper._orig_make_logits_processors(args)) == 1
        assert not isinstance(ours[-1], g.JSONGrammarProcessor)

    # the flag set but no tokenizer loaded: still passthrough (and no crash)
    wrapper._state.provider = None
    args = make_args(wrapper.JSONLogitsProcessorArguments, json_mode=True)
    assert S._make_logits_processors(args) == []

    # the flag set with a loaded tokenizer: grammar processor appended last
    wrapper._state.provider = SimpleNamespace(tokenizer=tokenizer, cli_args=SimpleNamespace(chat_template_args={}))
    try:
        args = make_args(wrapper.JSONLogitsProcessorArguments, logit_bias={5: 1.0}, json_mode=True, json_thinking=False)
        procs = S._make_logits_processors(args)
        assert len(procs) == 2 and isinstance(procs[-1], g.JSONGrammarProcessor)
        assert procs[-1].thinking_hint is False
        args = make_args(wrapper.JSONLogitsProcessorArguments, json_mode=True, json_thinking=None)
        procs = S._make_logits_processors(args)
        assert procs[-1].thinking_hint is True  # tokenizer has thinking -> template default
        assert S._make_logits_processors(args)[-1] is not procs[-1]  # fresh per request
    finally:
        wrapper._state.provider = None


def test_wrapper_threadlocal_carries_response_format(wrapper, monkeypatch):
    S = wrapper.mlx_server
    captured = {}

    def fake_original(self, request, stop_words):
        captured["args"] = S.LogitsProcessorArguments(
            logit_bias=None,
            repetition_penalty=0.0,
            repetition_context_size=20,
            presence_penalty=0.0,
            presence_context_size=20,
            frequency_penalty=0.0,
            frequency_context_size=20,
        )
        return "served"

    monkeypatch.setattr(wrapper, "_orig_handle_completion", fake_original)

    def call(body, path="/v1/chat/completions"):
        handler = SimpleNamespace(body=body, path=path)
        assert S.APIHandler.handle_completion(handler, None, []) == "served"
        return captured["args"]

    a = call({"messages": []})
    assert isinstance(a, wrapper.JSONLogitsProcessorArguments) and a.json_mode is False
    a = call({"messages": [], "response_format": {"type": "text"}})
    assert a.json_mode is False
    a = call({"messages": [], "response_format": {"type": "json_object"}})
    assert a.json_mode is True and a.json_thinking is None
    a = call({"messages": [], "response_format": {"type": "json_schema", "json_schema": {"name": "x"}},
              "chat_template_kwargs": {"enable_thinking": False}})
    assert a.json_mode is True and a.json_thinking is False
    a = call({"prompt": "x", "response_format": {"type": "json_object"}}, path="/v1/completions")
    assert a.json_mode is False  # only chat completions honour it
    # the thread-local is cleared after the call
    assert getattr(wrapper._tls, "json_mode", False) is False
    a = call({"messages": []})
    assert a.json_mode is False


def test_wants_json():
    assert g.wants_json({"response_format": {"type": "json_object"}})
    assert g.wants_json({"response_format": {"type": "json_schema", "json_schema": {}}})
    assert not g.wants_json({"response_format": {"type": "text"}})
    assert not g.wants_json({})
    assert not g.wants_json(None)
