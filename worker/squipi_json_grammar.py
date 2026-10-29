"""JSON-constrained decoding for the local mlx_lm server.

A character-level JSON automaton (explicit nesting stack, depth cap 32) is
simulated once per tokenizer over every vocabulary token to build a table
``allowed[state_class][token_id]``.  At generation time a
:class:`JSONGrammarProcessor` (an mlx_lm ``logits_processors`` callable)
tracks the automaton state from the generated token suffix and adds a
``0 / -inf`` mask to the logits so the completion is always exactly one
syntactically valid JSON object.

Only the tokenizer is needed to build the table; model weights are never
touched.  ``mlx.core`` is imported lazily so the automaton and the table can
be used (and tested) without MLX.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger("squipi.json_grammar")

GRAMMAR_VERSION = 4
MAX_DEPTH = 32
# After the top-level object closes, allow at most this many whitespace-only
# tokens before forcing EOS (prevents a model from padding with newlines).
DONE_WS_LIMIT = 2
# Tokens with more closing brackets than this are never allowed (bounded
# precompute); no sane model output needs 12 closers in one token.
MAX_CLOSERS_PER_TOKEN = 12

WS = " \t\n\r"
HEX = "0123456789abcdefABCDEF"
DIGITS = "0123456789"
ONE_NINE = "123456789"
ESCAPES = '"\\/bfnrt'

OBJ = "o"
ARR = "a"

_STATE_NAMES = [
    "TOP",  # expect ws or '{'
    "OBJ_OPEN",  # after '{': ws, '"', '}'
    "KEY", "KEY_ESC", "KEY_U0", "KEY_U1", "KEY_U2", "KEY_U3", "KEY_B1", "KEY_B2", "KEY_B3",
    "COLON",  # after key string: ws, ':'
    "OBJ_VAL",  # after ':': ws, value start
    "OBJ_AFTER",  # after a value in an object: ws, ',', '}'
    "OBJ_COMMA",  # after ',' in an object: ws, '"'
    "ARR_OPEN",  # after '[': ws, value start, ']'
    "ARR_AFTER",  # after a value in an array: ws, ',', ']'
    "ARR_COMMA",  # after ',' in an array: ws, value start
    "STR", "STR_ESC", "STR_U0", "STR_U1", "STR_U2", "STR_U3", "STR_B1", "STR_B2", "STR_B3",
    "NUM_MINUS", "NUM_ZERO", "NUM_INT", "NUM_FRAC0", "NUM_FRAC", "NUM_EXP0", "NUM_EXP_SIGN", "NUM_EXP",
    "LIT_rue", "LIT_ue", "LIT_e", "LIT_alse", "LIT_lse", "LIT_se", "LIT_ull", "LIT_ll", "LIT_l",
    "DONE",
]
STATE = {name: i for i, name in enumerate(_STATE_NAMES)}
globals().update({"S_" + k: v for k, v in STATE.items()})
N_STATES = len(_STATE_NAMES)

_LIT_NEXT = {
    "LIT_rue": "LIT_ue", "LIT_ue": "LIT_e", "LIT_e": None,
    "LIT_alse": "LIT_lse", "LIT_lse": "LIT_se", "LIT_se": "LIT_e",
    "LIT_ull": "LIT_ll", "LIT_ll": "LIT_l", "LIT_l": None,
}
_LIT_CHAR = {STATE[k]: k[4] for k in _LIT_NEXT}
_LIT_TO = {STATE[k]: (STATE[v] if v else -1) for k, v in _LIT_NEXT.items()}

# states that live inside an object / array / either
_OBJ_STATES = {STATE[n] for n in _STATE_NAMES if n.startswith(("OBJ_", "KEY", "COLON"))}
_ARR_STATES = {STATE[n] for n in _STATE_NAMES if n.startswith("ARR_")}
_VALUE_STATES = {STATE[n] for n in _STATE_NAMES if n.startswith(("STR", "NUM_", "LIT_"))}
_CONTENT_STATES = {S_KEY, S_STR}  # noqa: F821  plain string content
_BYTE_STATES = {  # pending UTF-8 continuation bytes -> (count, base state)
    S_KEY_B1: (1, S_KEY), S_KEY_B2: (2, S_KEY), S_KEY_B3: (3, S_KEY),  # noqa: F821
    S_STR_B1: (1, S_STR), S_STR_B2: (2, S_STR), S_STR_B3: (3, S_STR),  # noqa: F821
}
_TO_BYTE_STATE = {
    (S_KEY, 1): S_KEY_B1, (S_KEY, 2): S_KEY_B2, (S_KEY, 3): S_KEY_B3,  # noqa: F821
    (S_STR, 1): S_STR_B1, (S_STR, 2): S_STR_B2, (S_STR, 3): S_STR_B3,  # noqa: F821
}
# states where whitespace is a self-loop
_WS_STATES = {
    S_TOP, S_OBJ_OPEN, S_COLON, S_OBJ_VAL, S_OBJ_AFTER, S_OBJ_COMMA,  # noqa: F821
    S_ARR_OPEN, S_ARR_AFTER, S_ARR_COMMA, S_DONE,  # noqa: F821
}


class NeedKind(Exception):
    """Raised by the abstract stack when the kind of an unknown entry is consulted."""


class _Stack:
    """Concrete stack of container kinds (bottom .. top) with push/pop accounting."""

    __slots__ = ("items", "net", "max_net")

    def __init__(self, items=None):
        self.items = list(items) if items else []
        self.net = 0
        self.max_net = 0

    def push(self, kind):
        self.items.append(kind)
        self.net += 1
        if self.net > self.max_net:
            self.max_net = self.net

    def pop(self):
        self.net -= 1
        return self.items.pop()

    def top(self):
        return self.items[-1] if self.items else None

    def __len__(self):
        return len(self.items)


class _AbstractStack(_Stack):
    """Stack whose entries below the known ones are unknown unless ``bottom``."""

    __slots__ = ("bottom",)

    def __init__(self, items, bottom, net=0, max_net=0):
        super().__init__(items)
        self.bottom = bottom
        self.net = net
        self.max_net = max_net

    def top(self):
        if self.items:
            return self.items[-1]
        if self.bottom:
            return None
        raise NeedKind()

    def pop(self):
        if not self.items:
            raise NeedKind()
        return super().pop()


class JSONAutomaton:
    """Character-level JSON automaton. ``step`` returns the new state or -1."""

    @staticmethod
    def after_value(stack) -> int:
        top = stack.top()
        if top is None:
            return S_DONE  # noqa: F821
        return S_OBJ_AFTER if top == OBJ else S_ARR_AFTER  # noqa: F821

    @classmethod
    def value_start(cls, ch: str, stack) -> int:
        if ch == '"':
            return S_STR  # noqa: F821
        if ch == "{":
            if len(stack) >= MAX_DEPTH:
                return -1
            stack.push(OBJ)
            return S_OBJ_OPEN  # noqa: F821
        if ch == "[":
            if len(stack) >= MAX_DEPTH:
                return -1
            stack.push(ARR)
            return S_ARR_OPEN  # noqa: F821
        if ch == "-":
            return S_NUM_MINUS  # noqa: F821
        if ch == "0":
            return S_NUM_ZERO  # noqa: F821
        if ch in ONE_NINE:
            return S_NUM_INT  # noqa: F821
        if ch == "t":
            return S_LIT_rue  # noqa: F821
        if ch == "f":
            return S_LIT_alse  # noqa: F821
        if ch == "n":
            return S_LIT_ull  # noqa: F821
        return -1

    @classmethod
    def step(cls, st: int, ch: str, stack) -> int:
        if ch in WS and st in _WS_STATES:
            return st
        if st == S_TOP:  # noqa: F821
            if ch == "{":
                stack.push(OBJ)
                return S_OBJ_OPEN  # noqa: F821
            return -1
        if st == S_OBJ_OPEN:  # noqa: F821
            if ch == '"':
                return S_KEY  # noqa: F821
            if ch == "}":
                if stack.pop() != OBJ:
                    return -1
                return cls.after_value(stack)
            return -1
        if st == S_OBJ_COMMA:  # noqa: F821
            return S_KEY if ch == '"' else -1  # noqa: F821
        if st == S_KEY or st == S_STR:  # noqa: F821
            if ch == '"':
                return S_COLON if st == S_KEY else cls.after_value(stack)  # noqa: F821
            if ch == "\\":
                return S_KEY_ESC if st == S_KEY else S_STR_ESC  # noqa: F821
            if ord(ch) < 0x20:
                return -1
            return st
        if st == S_KEY_ESC or st == S_STR_ESC:  # noqa: F821
            if ch in ESCAPES:
                return S_KEY if st == S_KEY_ESC else S_STR  # noqa: F821
            if ch == "u":
                return S_KEY_U0 if st == S_KEY_ESC else S_STR_U0  # noqa: F821
            return -1
        if S_KEY_U0 <= st <= S_KEY_U3:  # noqa: F821
            if ch not in HEX:
                return -1
            return st + 1 if st < S_KEY_U3 else S_KEY  # noqa: F821
        if S_STR_U0 <= st <= S_STR_U3:  # noqa: F821
            if ch not in HEX:
                return -1
            return st + 1 if st < S_STR_U3 else S_STR  # noqa: F821
        if st in _BYTE_STATES:
            return -1  # continuation bytes are consumed outside the char automaton
        if st == S_COLON:  # noqa: F821
            return S_OBJ_VAL if ch == ":" else -1  # noqa: F821
        if st == S_OBJ_VAL or st == S_ARR_COMMA:  # noqa: F821
            return cls.value_start(ch, stack)
        if st == S_ARR_OPEN:  # noqa: F821
            if ch == "]":
                if stack.pop() != ARR:
                    return -1
                return cls.after_value(stack)
            return cls.value_start(ch, stack)
        if st == S_OBJ_AFTER:  # noqa: F821
            if ch == ",":
                return S_OBJ_COMMA  # noqa: F821
            if ch == "}":
                if stack.pop() != OBJ:
                    return -1
                return cls.after_value(stack)
            return -1
        if st == S_ARR_AFTER:  # noqa: F821
            if ch == ",":
                return S_ARR_COMMA  # noqa: F821
            if ch == "]":
                if stack.pop() != ARR:
                    return -1
                return cls.after_value(stack)
            return -1
        # numbers
        if st == S_NUM_MINUS:  # noqa: F821
            if ch == "0":
                return S_NUM_ZERO  # noqa: F821
            return S_NUM_INT if ch in ONE_NINE else -1  # noqa: F821
        if st == S_NUM_ZERO or st == S_NUM_INT:  # noqa: F821
            if ch in DIGITS and st == S_NUM_INT:  # noqa: F821
                return st
            if ch == ".":
                return S_NUM_FRAC0  # noqa: F821
            if ch in "eE":
                return S_NUM_EXP0  # noqa: F821
            return cls._end_number(ch, stack)
        if st == S_NUM_FRAC0:  # noqa: F821
            return S_NUM_FRAC if ch in DIGITS else -1  # noqa: F821
        if st == S_NUM_FRAC:  # noqa: F821
            if ch in DIGITS:
                return st
            if ch in "eE":
                return S_NUM_EXP0  # noqa: F821
            return cls._end_number(ch, stack)
        if st == S_NUM_EXP0:  # noqa: F821
            if ch in "+-":
                return S_NUM_EXP_SIGN  # noqa: F821
            return S_NUM_EXP if ch in DIGITS else -1  # noqa: F821
        if st == S_NUM_EXP_SIGN:  # noqa: F821
            return S_NUM_EXP if ch in DIGITS else -1  # noqa: F821
        if st == S_NUM_EXP:  # noqa: F821
            if ch in DIGITS:
                return st
            return cls._end_number(ch, stack)
        # literals
        if st in _LIT_CHAR:
            if ch != _LIT_CHAR[st]:
                return -1
            nxt = _LIT_TO[st]
            return nxt if nxt >= 0 else cls.after_value(stack)
        if st == S_DONE:  # noqa: F821
            return -1
        return -1

    @classmethod
    def _end_number(cls, ch: str, stack) -> int:
        """A number ends on ws , } ]: hand the char to the container's after-value state."""
        if ch not in WS and ch not in ",}]":
            return -1
        nst = cls.after_value(stack)
        if nst == S_DONE:  # noqa: F821
            return -1  # numbers cannot be top level here
        return cls.step(nst, ch, stack)

    @classmethod
    def run_text(cls, text: str, state: int = None, stack: Optional[List[str]] = None):
        """Feed ``text`` char by char. Returns (state, stack) or (-1, None)."""
        st = S_TOP if state is None else state  # noqa: F821
        stk = _Stack(stack)
        for ch in text:
            st = cls.step(st, ch, stk)
            if st < 0:
                return -1, None
        return st, stk.items

    @classmethod
    def accepts(cls, text: str) -> bool:
        """True iff ``text`` is one complete JSON object (optionally padded with ws)."""
        st, _ = cls.run_text(text)
        return st == S_DONE  # noqa: F821


# --------------------------------------------------------------------------
# token-level simulation


def consume_token(st: int, stack, info) -> int:
    """Apply one vocabulary token (lead_cont, text, trail_need) to (state, stack)."""
    lead, text, trail = info
    if st in _BYTE_STATES:
        need, base = _BYTE_STATES[st]
        if lead > need:
            return -1
        if lead < need:
            # a token made only of continuation bytes completes part of the sequence
            if text or trail:
                return -1
            return _TO_BYTE_STATE[(base, need - lead)]
        st = base
    elif lead != 0:
        return -1
    for ch in text:
        st = JSONAutomaton.step(st, ch, stack)
        if st < 0:
            return -1
    if trail:
        nxt = _TO_BYTE_STATE.get((st, trail))
        return nxt if nxt is not None else -1
    return st


Requirement = Tuple[Tuple[str, ...], bool]  # (kinds below the known top, top-down), exact-bottom


def _abstract_sim(st: int, text: str, stack: _AbstractStack, pattern: Tuple[str, ...], results: Dict[Requirement, int]):
    """Simulate ``text`` with an abstract stack; collect accepting requirements -> max rise."""
    i = 0
    n = len(text)
    while i < n:
        snap = (list(stack.items), stack.net, stack.max_net)
        try:
            ns = JSONAutomaton.step(st, text[i], stack)
        except NeedKind:
            for choice in (None, OBJ, ARR):
                items = list(snap[0])
                if choice is None:
                    s2 = _AbstractStack(items, True, snap[1], snap[2])
                    p2 = pattern
                else:
                    s2 = _AbstractStack([choice] + items, False, snap[1], snap[2])
                    p2 = pattern + (choice,)
                _abstract_sim(st, text[i:], s2, p2, results)
            return
        if ns < 0:
            return
        st = ns
        i += 1
    req = (pattern, stack.bottom)
    results[req] = max(results.get(req, 0), stack.max_net)


def analyze_token(st: int, top: Optional[str], info) -> Tuple[Dict[Requirement, int], int]:
    """Requirements under which ``info`` is accepted from (state, known top).

    Returns ({requirement: max_rise}, final_state_if_unique_or_-1).
    """
    lead, text, trail = info
    if st in _BYTE_STATES:
        need, base = _BYTE_STATES[st]
        if lead > need:
            return {}, -1
        if lead < need:
            if text or trail:
                return {}, -1
            return {((), False): 0}, -1
        st = base
    elif lead != 0:
        return {}, -1
    if text.count("}") + text.count("]") > MAX_CLOSERS_PER_TOKEN:
        return {}, -1
    if top is None:
        stack = _AbstractStack([], True)
    else:
        stack = _AbstractStack([top], False)
    results: Dict[Requirement, int] = {}
    # trailing partial UTF-8 bytes are only legal when we end inside string content
    if trail:
        if not text:
            end_ok = st in _CONTENT_STATES
            if end_ok:
                results[((), stack.bottom)] = 0
            return results, -1
        # simulate text; then check the final state is string content.  We
        # need the final state, so run a concrete check on each accepted path:
        tmp: Dict[Requirement, int] = {}
        _abstract_sim(st, text, stack, (), tmp)
        if not tmp:
            return {}, -1
        if top is None:
            tmp = {((), False): max(tmp.values())}
        # the final state does not depend on unknown kinds when it is inside a
        # string, so a concrete run with a deep dummy stack tells us
        fs, _ = _concrete_final(st, text, top)
        if fs not in _CONTENT_STATES:
            return {}, -1
        return tmp, -1
    _abstract_sim(st, text, stack, (), results)
    if top is None and results:
        # the stack is known to be empty: acceptance never depends on deeper entries
        results = {((), False): max(results.values())}
    return results, -1


def _concrete_final(st: int, text: str, top: Optional[str]):
    stk = _Stack([OBJ] * 8 + ([top] if top else []))
    for ch in text:
        st = JSONAutomaton.step(st, ch, stk)
        if st < 0:
            return -1, None
    return st, stk


def covers_all(reqs: FrozenSet[Requirement], prefix: Tuple[str, ...] = (), limit: int = 16) -> bool:
    """True when the requirement set accepts every possible stack below the known top."""
    if (prefix, False) in reqs:
        return True
    if (prefix, True) not in reqs or len(prefix) >= limit:
        return False
    return covers_all(reqs, prefix + (OBJ,), limit) and covers_all(reqs, prefix + (ARR,), limit)


def match_requirement(req: Requirement, stack: Sequence[str]) -> bool:
    pattern, bottom = req
    k = len(pattern)
    depth = len(stack)
    if depth < 1 + k:
        return False
    if bottom and depth != 1 + k:
        return False
    for i, kind in enumerate(pattern):
        if stack[-2 - i] != kind:
            return False
    return True


# --------------------------------------------------------------------------
# vocabulary analysis


def _byte_decoder_map() -> Dict[str, int]:
    try:
        from mlx_lm.tokenizer_utils import _byte_decoder

        return _byte_decoder()
    except Exception:  # pragma: no cover - fallback copy of the GPT-2 mapping
        char_to_bytes = {}
        limits = [0, ord("!"), ord("~") + 1, ord("¡"), ord("¬") + 1, ord("®"), ord("ÿ") + 1]
        n = 0
        for i, (start, stop) in enumerate(zip(limits, limits[1:])):
            if i % 2 == 0:
                for b in range(start, stop):
                    char_to_bytes[chr(2**8 + n)] = b
                    n += 1
            else:
                for b in range(start, stop):
                    char_to_bytes[chr(b)] = b
        return char_to_bytes


def _utf8_need(lead: int) -> int:
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def decompose_bytes(b: bytes) -> Optional[Tuple[int, str, int]]:
    """Split raw token bytes into (leading continuation bytes, text, missing trailing bytes).

    Returns None when the bytes cannot be a slice of valid UTF-8 text.
    """
    if not b:
        return None
    k = 0
    while k < len(b) and 0x80 <= b[k] < 0xC0:
        k += 1
    if k > 3:
        return None
    rest = b[k:]
    if not rest:
        return (k, "", 0)
    try:
        return (k, rest.decode("utf-8"), 0)
    except UnicodeDecodeError:
        pass
    for t in range(1, 4):
        if t > len(rest):
            break
        lead = rest[-t]
        need = _utf8_need(lead)
        if need <= t:
            continue
        if any(not (0x80 <= c < 0xC0) for c in rest[len(rest) - t + 1:]):
            continue
        mid = rest[:-t]
        try:
            return (k, mid.decode("utf-8"), need - t)
        except UnicodeDecodeError:
            return None
    return None


def _hf(tokenizer):
    """The underlying HF tokenizer of an mlx_lm TokenizerWrapper (or itself)."""
    return getattr(tokenizer, "_tokenizer", tokenizer)


def tokenizer_fingerprint(tokenizer) -> str:
    h = _hf(tokenizer)
    vocab = h.get_vocab()
    special = sorted(int(i) for i in getattr(h, "added_tokens_decoder", {}).keys())
    eos = sorted(int(i) for i in (getattr(tokenizer, "eos_token_ids", None) or []))
    payload = json.dumps(
        {"v": GRAMMAR_VERSION, "n": len(vocab), "special": special, "eos": eos, "vocab": sorted(vocab.items())},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def vocab_token_infos(tokenizer, vocab_size: Optional[int] = None):
    """Per-id (lead, text, trail) tuples; None for special/undecodable/out-of-range ids."""
    h = _hf(tokenizer)
    n_tok = len(h)
    V = max(vocab_size or 0, n_tok)
    bmap = _byte_decoder_map()
    special = set(int(i) for i in getattr(h, "added_tokens_decoder", {}).keys())
    special |= set(int(i) for i in (getattr(h, "all_special_ids", None) or []))
    infos: List[Optional[Tuple[int, str, int]]] = [None] * V
    strings = h.convert_ids_to_tokens(list(range(n_tok)))
    for i, s in enumerate(strings):
        if i in special or s is None:
            continue
        try:
            raw = bytes(bmap[c] for c in s)
        except KeyError:
            # not a byte-level token string (e.g. sentencepiece); fall back to decode
            txt = h.decode([i])
            if not txt or "�" in txt:
                continue
            infos[i] = (0, txt, 0)
            continue
        infos[i] = decompose_bytes(raw)
    return infos


# --------------------------------------------------------------------------
# the table


def state_classes() -> List[Tuple[int, Optional[str]]]:
    classes: List[Tuple[int, Optional[str]]] = [(S_TOP, None), (S_DONE, None)]  # noqa: F821
    for st in sorted(_OBJ_STATES):
        classes.append((st, OBJ))
    for st in sorted(_ARR_STATES):
        classes.append((st, ARR))
    for st in sorted(_VALUE_STATES):
        classes.append((st, OBJ))
        classes.append((st, ARR))
    return classes


class GrammarTable:
    """Precomputed per-tokenizer token admissibility for every automaton state class."""

    def __init__(self, vocab_size: int, infos, eos_ids: Sequence[int]):
        self.V = vocab_size
        self.infos = infos
        self.eos_ids = sorted(set(int(i) for i in eos_ids))
        self.classes = state_classes()
        self.class_index = {c: i for i, c in enumerate(self.classes)}
        C = len(self.classes)
        self.allowed = np.zeros((C, vocab_size), dtype=bool)
        # per class: list of (frozenset(requirements), np.array ids)
        self.dep: List[List[Tuple[FrozenSet[Requirement], np.ndarray]]] = [[] for _ in range(C)]
        # per class: (ids, rise) for tokens that raise the depth
        self.rise: List[Tuple[np.ndarray, np.ndarray]] = [
            (np.zeros(0, np.int64), np.zeros(0, np.int8)) for _ in range(C)
        ]
        self.max_pattern = 0
        self.global_max_rise = 0
        self.ws_ids = np.zeros(0, np.int64)
        # (state, stack signature, dtype, width) -> mx additive mask; shared by
        # all requests on this tokenizer (mx arrays are immutable)
        self.mask_cache: Dict[tuple, Any] = {}

    # -- building -----------------------------------------------------------

    @classmethod
    def build(cls, tokenizer, vocab_size: Optional[int] = None) -> "GrammarTable":
        t0 = time.perf_counter()
        infos = vocab_token_infos(tokenizer, vocab_size)
        V = len(infos)
        eos = list(getattr(tokenizer, "eos_token_ids", None) or [])
        if not eos:
            e = getattr(_hf(tokenizer), "eos_token_id", None)
            eos = [e] if e is not None else []
        table = cls(V, infos, eos)
        table._fill()
        log.info("JSON grammar table built: V=%d classes=%d in %.2fs", V, len(table.classes), time.perf_counter() - t0)
        return table

    def _fill(self):
        infos = self.infos
        valid_ids = [i for i, inf in enumerate(infos) if inf is not None]
        ws_only = [i for i in valid_ids if infos[i][1] and infos[i][0] == 0 and infos[i][2] == 0 and all(c in WS for c in infos[i][1])]
        self.ws_ids = np.array(ws_only, dtype=np.int64)
        plain = {}  # id -> True when the text is plain string content (no quote/backslash/control)
        by_first: Dict[Tuple[int, str], List[int]] = {}  # (lead, first char after ws) -> ids
        by_first_strict: Dict[Tuple[int, str], List[int]] = {}  # (lead, first char) -> ids
        for i in valid_ids:
            lead, text, trail = infos[i]
            plain[i] = all(c >= " " and c not in '"\\' for c in text)
            if text:
                by_first_strict.setdefault((lead, text[0]), []).append(i)
                stripped = text.lstrip(WS)
                key = (lead, stripped[0] if stripped else "")
                by_first.setdefault(key, []).append(i)
            else:
                by_first_strict.setdefault((lead, ""), []).append(i)
                by_first.setdefault((lead, ""), []).append(i)

        for ci, (st, top) in enumerate(self.classes):
            base = st
            need = 0
            if st in _BYTE_STATES:
                need, base = _BYTE_STATES[st]
            allowed = self.allowed[ci]
            dep_groups: Dict[FrozenSet[Requirement], List[int]] = {}
            rise_ids: List[int] = []
            rise_vals: List[int] = []

            if base in _CONTENT_STATES:
                # fast path: plain content tokens stay inside the string
                for i in valid_ids:
                    lead, text, trail = infos[i]
                    if lead != need:
                        if need and lead < need and not text and not trail:
                            allowed[i] = True  # pure continuation bytes
                        continue
                    if plain[i]:
                        allowed[i] = True
                        continue
                    reqs, _ = analyze_token(st, top, infos[i])
                    self._record(i, reqs, allowed, dep_groups, rise_ids, rise_vals)
            else:
                # candidate first chars (after optional whitespace when ws self-loops)
                ws_loop = base in _WS_STATES
                probe_stack_items = ([OBJ] * 4 + [top]) if top else []
                ok_first = set()
                table_src = by_first if ws_loop else by_first_strict
                for (lead, ch), ids in table_src.items():
                    if lead != need:
                        continue
                    if ch == "":
                        if ws_loop:
                            ok_first.add(ch)  # whitespace-only tokens
                        continue
                    stk = _Stack(probe_stack_items)
                    if JSONAutomaton.step(base, ch, stk) >= 0:
                        ok_first.add(ch)
                for ch in ok_first:
                    for i in table_src.get((need, ch), []):
                        lead, text, trail = infos[i]
                        if trail:
                            # trailing partial bytes need to end in string content
                            if '"' not in text:
                                continue
                        if ws_loop and ch == "" and text and not trail:
                            allowed[i] = True  # whitespace-only token
                            continue
                        reqs, _ = analyze_token(st, top, infos[i])
                        self._record(i, reqs, allowed, dep_groups, rise_ids, rise_vals)

            self.dep[ci] = [(k, np.array(sorted(v), dtype=np.int64)) for k, v in dep_groups.items()]
            for k in dep_groups:
                for pattern, _ in k:
                    self.max_pattern = max(self.max_pattern, len(pattern))
            self.rise[ci] = (np.array(rise_ids, dtype=np.int64), np.array(rise_vals, dtype=np.int8))
            if rise_vals:
                self.global_max_rise = max(self.global_max_rise, max(rise_vals))

    @staticmethod
    def _record(i, reqs, allowed, dep_groups, rise_ids, rise_vals):
        if not reqs:
            return
        allowed[i] = True
        rise = max(reqs.values())
        if rise > 0:
            rise_ids.append(i)
            rise_vals.append(rise)
        key = frozenset(reqs.keys())
        if not covers_all(key):
            dep_groups.setdefault(key, []).append(i)

    # -- persistence --------------------------------------------------------

    def save(self, path: Path):
        meta = {
            "version": GRAMMAR_VERSION,
            "V": self.V,
            "classes": [[st, top] for st, top in self.classes],
            "eos_ids": self.eos_ids,
            "dep": [
                [[[[list(p), bool(b)] for p, b in sorted(k)], ids.tolist()] for k, ids in groups]
                for groups in self.dep
            ],
            "rise": [[ids.tolist(), vals.tolist()] for ids, vals in self.rise],
            "max_pattern": self.max_pattern,
            "global_max_rise": self.global_max_rise,
            "ws_ids": self.ws_ids.tolist(),
            "infos": [list(x) if x is not None else None for x in self.infos],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(
            tmp,
            allowed=np.packbits(self.allowed, axis=1),
            meta=np.array(json.dumps(meta, ensure_ascii=False)),
        )
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "GrammarTable":
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            packed = z["allowed"]
        if meta.get("version") != GRAMMAR_VERSION:
            raise ValueError("grammar table version mismatch")
        infos = [tuple(x) if x is not None else None for x in meta["infos"]]
        table = cls(meta["V"], infos, meta["eos_ids"])
        table.classes = [(st, top) for st, top in meta["classes"]]
        table.class_index = {c: i for i, c in enumerate(table.classes)}
        table.allowed = np.unpackbits(packed, axis=1, count=meta["V"]).astype(bool)
        table.dep = [
            [(frozenset((tuple(p), bool(b)) for p, b in reqs), np.array(ids, dtype=np.int64)) for reqs, ids in groups]
            for groups in meta["dep"]
        ]
        table.rise = [(np.array(ids, dtype=np.int64), np.array(vals, dtype=np.int8)) for ids, vals in meta["rise"]]
        table.max_pattern = meta["max_pattern"]
        table.global_max_rise = meta["global_max_rise"]
        table.ws_ids = np.array(meta["ws_ids"], dtype=np.int64)
        return table

    # -- queries ------------------------------------------------------------

    def class_of(self, state: int, stack: Sequence[str]) -> int:
        top = stack[-1] if stack else None
        if state in _OBJ_STATES:
            top = OBJ
        elif state in _ARR_STATES:
            top = ARR
        elif state in (S_TOP, S_DONE):  # noqa: F821
            top = None
        return self.class_index[(state, top)]

    def allowed_mask(self, state: int, stack: Sequence[str], ws_exhausted: bool = False) -> np.ndarray:
        """Boolean admissibility for the concrete (state, stack); EOS added in DONE."""
        ci = self.class_of(state, stack)
        allowed = self.allowed[ci].copy()
        for reqs, ids in self.dep[ci]:
            if not any(match_requirement(r, stack) for r in reqs):
                allowed[ids] = False
        depth = len(stack)
        if depth + self.global_max_rise > MAX_DEPTH:
            ids, vals = self.rise[ci]
            if len(ids):
                allowed[ids[vals > (MAX_DEPTH - depth)]] = False
        if state == S_DONE:  # noqa: F821
            if ws_exhausted:
                allowed[:] = False
            if self.eos_ids:
                allowed[self.eos_ids] = True
        return allowed

    def mask_key(self, state: int, stack: Sequence[str], ws_exhausted: bool) -> tuple:
        k = self.max_pattern + 1
        sig = tuple(stack[-k:][::-1])
        depth = len(stack)
        exact = depth if depth <= k else -1
        cap = depth if depth + self.global_max_rise > MAX_DEPTH else -1
        return (state, sig, exact, cap, ws_exhausted)


# --------------------------------------------------------------------------
# cache


_TABLE_LOCK = threading.Lock()
_TABLES: Dict[str, GrammarTable] = {}


def cache_dir() -> Optional[Path]:
    root = os.environ.get("HF_HOME")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    return Path(root) / "squipi-json-grammar"


def get_table(tokenizer, vocab_size: Optional[int] = None, use_disk: bool = True) -> GrammarTable:
    """Return the (process-wide cached) grammar table for ``tokenizer``."""
    fp = tokenizer_fingerprint(tokenizer)
    key = f"{fp}-{vocab_size or 0}"
    with _TABLE_LOCK:
        table = _TABLES.get(key)
        if table is not None:
            return table
        path = None
        if use_disk:
            d = cache_dir()
            if d is not None:
                path = d / f"{key}.npz"
                if path.exists():
                    try:
                        table = GrammarTable.load(path)
                        log.info("JSON grammar table loaded from %s", path)
                    except Exception as e:  # corrupt / stale cache
                        log.warning("JSON grammar cache unreadable (%s); rebuilding", e)
                        table = None
        if table is None:
            table = GrammarTable.build(tokenizer, vocab_size)
            if path is not None:
                try:
                    table.save(path)
                    log.info("JSON grammar table cached at %s", path)
                except Exception as e:
                    log.warning("JSON grammar cache not writable (%s); keeping in memory", e)
        _TABLES[key] = table
        return table


# --------------------------------------------------------------------------
# the logits processor


class JSONGrammarProcessor:
    """mlx_lm logits processor: ``processor(tokens, logits) -> logits``.

    ``tokens`` is the full history (prompt + generated); the processor records
    its length on the first call and tracks the automaton over the generated
    suffix.  While the model is inside a ``<think>`` block the logits pass
    through unchanged; constraining starts after the think-end token.
    """

    PHASE_THINK = 0
    PHASE_JSON = 1
    PHASE_END = 2

    _MASK_CACHE_LIMIT = 128

    def __init__(
        self,
        table: GrammarTable,
        think_start_ids: Sequence[int] = (),
        think_end_ids: Sequence[int] = (),
        thinking_hint: bool = False,
        tail_scan: int = 32,
    ):
        self.table = table
        self.think_start_ids = tuple(int(i) for i in think_start_ids or ())
        self.think_end_ids = tuple(int(i) for i in think_end_ids or ())
        self.thinking_hint = bool(thinking_hint) and bool(self.think_end_ids)
        self.tail_scan = tail_scan
        self.state = S_TOP  # noqa: F821
        self.stack: List[str] = []
        self.phase = self.PHASE_JSON
        self.ws_in_done = 0
        self.disabled = False
        self.steps = 0
        self._seen = -1
        self._recent: List[int] = []
        self._mask_cache: Dict[tuple, Any] = table.mask_cache
        self._logged = False

    # -- state tracking -----------------------------------------------------

    def _first_call(self, tokens):
        n = tokens.size
        tail = tokens[max(0, n - self.tail_scan): n].tolist() if n else []
        thinking = self.thinking_hint
        if self.think_start_ids and self.think_end_ids:
            s = _rfind(tail, self.think_start_ids)
            e = _rfind(tail, self.think_end_ids)
            if s >= 0 or e >= 0:
                thinking = s > e
        self.phase = self.PHASE_THINK if thinking else self.PHASE_JSON

    def advance(self, token_id: int):
        """Consume one generated token id."""
        if self.phase == self.PHASE_END:
            return
        if self.phase == self.PHASE_THINK:
            self._recent.append(token_id)
            if len(self._recent) > 8:
                del self._recent[:-8]
            k = len(self.think_end_ids)
            if k and tuple(self._recent[-k:]) == self.think_end_ids:
                self.phase = self.PHASE_JSON
            return
        if token_id in self.table.eos_ids:
            self.phase = self.PHASE_END
            return
        info = self.table.infos[token_id] if 0 <= token_id < len(self.table.infos) else None
        if info is None:
            raise ValueError(f"token {token_id} is not admissible")
        stk = _Stack(self.stack)
        ns = consume_token(self.state, stk, info)
        if ns < 0:
            raise ValueError(f"token {token_id} ({info[1]!r}) violates the JSON grammar in state {_STATE_NAMES[self.state]}")
        if self.state == S_DONE:  # noqa: F821
            self.ws_in_done += 1
        self.state = ns
        self.stack = stk.items

    def allowed_now(self) -> np.ndarray:
        return self.table.allowed_mask(self.state, self.stack, self.ws_in_done >= DONE_WS_LIMIT)

    # -- mlx hook -----------------------------------------------------------

    def __call__(self, tokens, logits):
        if self.disabled:
            return logits
        try:
            n = tokens.size
            if self._seen < 0:
                self._seen = n
                self._first_call(tokens)
            elif n > self._seen:
                new = tokens[self._seen: n].tolist()
                self._seen = n
                for t in new:
                    self.advance(int(t))
            elif n < self._seen:
                raise RuntimeError("token history shrank")
            self.steps += 1
            if self.phase != self.PHASE_JSON:
                return logits
            if logits.ndim != 2 or logits.shape[0] != 1:
                raise RuntimeError(f"unsupported logits shape {tuple(logits.shape)}")
            return logits + self._mask(logits)
        except Exception as e:  # never break a request
            if not self._logged:
                log.warning("JSON grammar processor disabled for this request: %s", e)
                self._logged = True
            self.disabled = True
            return logits

    def _mask(self, logits):
        import mlx.core as mx

        V = logits.shape[-1]
        key = self.table.mask_key(self.state, self.stack, self.ws_in_done >= DONE_WS_LIMIT) + (V, str(logits.dtype))
        m = self._mask_cache.get(key)
        if m is None:
            allowed = self.allowed_now()
            if V != allowed.shape[0]:
                fixed = np.zeros(V, dtype=bool)
                k = min(V, allowed.shape[0])
                fixed[:k] = allowed[:k]
                allowed = fixed
            arr = np.where(allowed, np.float32(0.0), np.float32(-np.inf)).astype(np.float32)
            m = mx.array(arr)[None]
            if logits.dtype != mx.float32:
                m = m.astype(logits.dtype)
            if len(self._mask_cache) >= self._MASK_CACHE_LIMIT:
                self._mask_cache.clear()
            self._mask_cache[key] = m
        return m


def _rfind(seq: List[int], sub: Tuple[int, ...]) -> int:
    k = len(sub)
    if not k or len(seq) < k:
        return -1
    for i in range(len(seq) - k, -1, -1):
        if tuple(seq[i: i + k]) == sub:
            return i
    return -1


def wants_json(body: Any) -> bool:
    """True when an OpenAI-style request body asks for a JSON object response."""
    if not isinstance(body, dict):
        return False
    rf = body.get("response_format")
    if isinstance(rf, str):
        return rf in ("json_object", "json_schema")
    if isinstance(rf, dict):
        return rf.get("type") in ("json_object", "json_schema")
    return False


def make_processor(tokenizer, table: Optional[GrammarTable] = None, thinking_hint: Optional[bool] = None, vocab_size: Optional[int] = None) -> JSONGrammarProcessor:
    """Build a fresh per-request processor for ``tokenizer``."""
    if table is None:
        table = get_table(tokenizer, vocab_size)
    starts = getattr(tokenizer, "think_start_tokens", None) or ()
    ends = getattr(tokenizer, "think_end_tokens", None) or ()
    if thinking_hint is None:
        thinking_hint = bool(getattr(tokenizer, "has_thinking", False))
    return JSONGrammarProcessor(table, starts, ends, thinking_hint=thinking_hint)
