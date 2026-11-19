#!/usr/bin/env python3
"""read-panel.py — a liuqin panel photo becomes a transcript that is provably the panel's words.

Why.  The panel is the only console (AGENTS.md: 诊断只能走面板里程碑控制台), and reading it by
eye is the slowest step of every boot round: one photo is 10 MB, the lines carry 16-digit
kernel addresses, and a phone camera plus rapidocr renders `0xfffffff568cb34a8` as
`0xffffffr568cb34a8`.  Misreading one digit has already cost rounds of speculation.

How.  Three layers, and each one fails loudly instead of guessing:

1. vocabulary   What the kernel and the driver *can* print is mined from this repository
                itself (`patches/xnu/*.patch`, `drivers/**`, `stubs/**`): every
                `liuqin_milestone("B", "bsd_init")` becomes a known name, every
                `__builtin_snprintf` format string becomes a line template with typed
                fields.  Nothing is hand-typed, so the vocabulary cannot drift from the tree.
                A row that matches no template is reported RAW.
2. anchored match + typed repair   A row is matched by the template's literal chunks
                (`" h="`, `" cnt="`, …) under the *measured* OCR confusion model, and each
                captured field is re-decoded inside its own alphabet only (`%p` → hex,
                `%u` → digits).  Repair never crosses a field boundary, so a repaired line
                cannot quietly become a different line.
3. validation   A repaired `%p` must be a well-formed address; with `--syms k.syms` it must
                also be a real symbol address (snapped when unique, graded SYMS).  Where a
                character has more than one legal reading and no symbol table to settle it,
                the field is graded AMBIG — the script says it does not know.

Grades: EXACT (no repair at all) · SYMS (address settled by the symbol table) ·
FIXED (repaired, every change a confusable pair inside one field) · AMBIG (open reading) ·
RAW (no template matched — reported verbatim).

`--round v40` matches the transcript against that round's judgement table
(`docs/panel-rules.toml`) and prints which cell the photo is in, the next step it prescribes,
and every signal the round expects that the photo does not show.

usage
    scripts/read-panel.py IMG_7059.PNG [IMG_7060.PNG …] [--round v40] [--syms k.syms]
    scripts/read-panel.py --list                      # what this tree can print
    scripts/read-panel.py --selftest                  # repair layer, no OCR, no device
    scripts/read-panel.py --raw …                     # OCR text before any repair
    scripts/read-panel.py --json …                    # machine readable
    OCR_PY=/path/to/python …                           # interpreter that can import rapidocr
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field as dsfield
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OCR_PYTHONS = [
    os.environ.get("OCR_PY", ""),
    str(Path.home() / ".dsh-qqbot/workspace/.venv-ocr/bin/python"),
    "python3",
]

# ===========================================================================
# 1. vocabulary mined from the tree
# ===========================================================================

MILESTONE_RE = re.compile(r'liuqin_milestone\s*\(\s*"([A-Za-z]):?"?\s*,\s*"([^"]+)"\)')
BOOTLOG_RE = re.compile(r'kernel_bootstrap(_thread)?_log\s*\(\s*"([^"]+)"')
# a format string can contain escaped quotes: …asc=\"%s\"…  — take the whole literal
FMT_RE = re.compile(r'"((?:M|U|I|P):(?:[^"\\]|\\.)*)"')
SKIP_DIRS = {"tests", "__pycache__", "docs", "out", "node_modules", ".git"}
SUFFIX = {".c", ".h", ".cpp", ".hpp", ".cc", ".patch", ".diff", ".inc"}


def _sources(roots):
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.suffix not in SUFFIX or not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            yield path


@dataclass
class F:
    kind: str            # ptr | hex16 | uint | int | word | text
    label: str           # the key this field carries, e.g. "h" from " h="
    candidates: set = dsfield(default_factory=set)


@dataclass
class Template:
    fmt: str
    origins: set
    chunks: list
    fields: list

    @property
    def shape(self) -> str:
        out = []
        for i, chunk in enumerate(self.chunks):
            out.append(chunk)
            if i < len(self.fields):
                out.append("<%s>" % self.fields[i].kind)
        return "".join(out)

    def render(self, values) -> str:
        out = []
        for i, chunk in enumerate(self.chunks):
            out.append(chunk)
            if i < len(self.fields):
                out.append(values[i])
        return "".join(out)

    @property
    def literal_cost_budget(self) -> float:
        lit = sum(len(c) for c in self.chunks)
        return max(1.6, 0.42 * lit)


class Vocabulary:
    def __init__(self) -> None:
        self.milestones: dict[str, set[str]] = {}
        self.templates: list[Template] = []
        self._by_shape: dict[str, Template] = {}
        self.dropped: list[str] = []

    def add_milestone(self, tag: str, name: str) -> None:
        self.milestones.setdefault(tag.upper(), set()).add(name)

    def add_template(self, fmt: str, origins, window: str) -> None:
        tpl = compile_template(fmt, origins, window)
        if tpl is None:
            return
        old = self._by_shape.get(tpl.shape)
        if old is not None:
            old.origins |= tpl.origins
            for a, b in zip(old.fields, tpl.fields):
                a.candidates |= b.candidates
            return
        self._by_shape[tpl.shape] = tpl
        self.templates.append(tpl)

    @property
    def names(self) -> set[str]:
        return {n for s in self.milestones.values() for n in s}

    def restrict_to_image(self, kernel: Path) -> None:
        """Drop everything the built image cannot print.

        The tree is not the image. `patches/xnu/*.patch` carries context lines, feature-gated
        blocks and `#if`s, so mining the tree advertises names this build never emits --
        `kernel_bootstrap_log("trust_cache_init")` sits in the liuqin patch as context while
        the block containing it is compiled out, and the reader "corrected" unrelated K rows
        into `K34:trust_cache_init`, a string that is not in kernel.macho at all. The image
        is the only authority on what can appear on the panel, so every name and every
        template head is checked against its bytes; anything absent is dropped and reported.
        """
        blob = kernel.read_bytes()
        for tag in list(self.milestones):
            keep = {n for n in self.milestones[tag] if (n + "\0").encode() in blob}
            for n in self.milestones[tag] - keep:
                self.dropped.append(f"{tag}:{n}")
            self.milestones[tag] = keep
            if not keep:
                del self.milestones[tag]
        kept = []
        for t in self.templates:
            head = t.chunks[0]
            # template heads keep their spaces but the image stores the literal; compare the
            # first literal word long enough to be unambiguous
            probe = head.strip().encode("utf-8", "replace")
            if len(probe) < 8 or probe in blob:
                kept.append(t)
            else:
                self.dropped.append(f"T:{head!r}")
        self.templates = kept
        self._by_shape = {t.shape: t for t in self.templates}

    def summary(self) -> str:
        ms = sum(len(v) for v in self.milestones.values())
        tags = " ".join(f"{k}:{len(v)}" for k, v in sorted(self.milestones.items()))
        extra = f", dropped {len(self.dropped)}" if self.dropped else ""
        return f"{ms} milestone names ({tags}), {len(self.templates)} line templates{extra}"

    def describe(self) -> str:
        lines = [f"vocabulary mined from {REPO}", f"  {self.summary()}", "", "line templates:"]
        for t in sorted(self.templates, key=lambda t: t.shape):
            lines.append(f"  {t.shape}")
            if t.fields and t.fields[0].candidates:
                cands = " ".join(sorted(t.fields[0].candidates))[:200]
                lines.append(f"      word candidates: {cands}")
            lines.append(f"      {'/'.join(sorted(t.origins))}")
        lines.append("")
        lines.append("milestone names:")
        for tag in sorted(self.milestones):
            lines.append(f"  {tag}: " + " ".join(sorted(self.milestones[tag])))
        return "\n".join(lines)


def classify_conv(token: str):
    """`%llu` → uint, `%p` → ptr, `%016llx` → hex16, `%s` → word; anything else → None."""
    m = re.match(r"^%[-+ 0-9#]*(ll|l)?([spduxX])$", token)
    if not m:
        return None
    conv = m.group(2)
    if conv == "p":
        return "ptr"
    if conv == "s":
        return "word"
    if conv in "xX":
        return "hex16" if re.match(r"^%016", token) else "ptr"
    if conv == "u":
        return "uint"
    if conv == "d":
        return "int"
    return None


def compile_template(fmt: str, origins, window: str):
    body = fmt.replace("\\n", "").replace("\\t", "").replace('\\"', '"')
    chunks: list[str] = []
    fields: list[F] = []
    out = ""
    i = 0
    while i < len(body):
        if body[i] != "%":
            out += body[i]
            i += 1
            continue
        m = re.match(r"%[-+ 0-9#]*l{0,2}[spduxX]", body[i:])
        if not m:
            return None
        kind = classify_conv(m.group(0))
        if kind is None:
            return None
        chunks.append(out)
        out = ""
        candidates = set()
        if kind == "word":
            candidates = {w for w in re.findall(r'"([a-z][a-z0-9_-]{1,12})"', window)
                          if not w.startswith(("M:", "U:", "I:", "K:", "B:"))
                          and not re.fullmatch(r"[0-9a-f]{5,}", w)}
            if not candidates:
                kind = "text"
        fields.append(F(kind, field_label(chunks[-1]), candidates))
        i += len(m.group(0))
    chunks.append(out)
    if not any(chunks):
        return None
    return Template(fmt=fmt, origins=set(origins), chunks=chunks, fields=fields)


def field_label(prev_chunk: str) -> str:
    """` cnt=` before a field → key `cnt`; nothing recognizable → positional key."""
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)=$", prev_chunk)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)[= ]? ?$", prev_chunk)
    return m.group(1) if m else ""


def load_vocabulary(extra: list[str] | None = None,
                    kernel: "Path | None" = None) -> Vocabulary:
    roots = [REPO / "patches" / "xnu", REPO / "drivers", REPO / "stubs"]
    roots += [Path(e) for e in (extra or [])]
    roots += [Path(p) for p in os.environ.get("XNU_PATCH_DIR", "").split(os.pathsep) if p]
    roots = [r for r in roots if r.is_dir()]
    vocab = Vocabulary()
    for path in _sources(roots):
        try:
            raw_lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        file_text = "\n".join(x[1:] if x.startswith("+") else x
                              for x in raw_lines if not x.startswith("-"))
        # Words a `%s` slot can take.  The call sites are usually hundreds of lines away
        # from the format string, so the pool is the whole file — and the match still has
        # to be confusable-close (budget 0.3·len), which is what keeps a wrong word out.
        file_words = {w for w in re.findall(r'"([a-z][a-z0-9_-]{1,12})"', file_text)
                      if not w.startswith(("M:", "U:", "I:", "K:", "B:"))
                      and not re.fullmatch(r"[0-9a-f]{5,}", w)}
        for i, raw in enumerate(raw_lines):
            if raw.startswith("-"):
                continue
            body = raw[1:] if raw.startswith("+") else raw
            for m in MILESTONE_RE.finditer(body):
                vocab.add_milestone(m.group(1), m.group(2))
            for m in BOOTLOG_RE.finditer(body):
                vocab.add_milestone("T" if m.group(1) else "K", m.group(2))
            window = "\n".join(x[1:] if x.startswith("+") else x
                               for x in raw_lines[max(0, i - 15):i + 16])
            for m in FMT_RE.finditer(body):
                if "%" not in m.group(1):
                    continue
                vocab.add_template(m.group(1), {path.name}, window + "\n" + file_text)
    # order matters for reporting: the longest, most specific templates first
    vocab.templates.sort(key=lambda t: -sum(len(c) for c in t.chunks))
    if kernel is not None:
        vocab.restrict_to_image(kernel)
    return vocab


# ===========================================================================
# 2. the confusion model — measured on the real photos, not invented
# ===========================================================================
#
# Each group was written down by diffing rapidocr output of IMG_7046..IMG_7063 against
# the ground truth in docs/boot-rounds-v35-v40.md.  Two characters may be swapped only
# inside the same group, and only inside one field.

GROUPS = [
    "0oOqQdD",     # 0xffff… → 0xffffff oo…
    "1lIi|!t",     # cnt=1 → cnt=l ; 1 → t
    "2zZ",
    "3",
    "4A",          # B04 → 864 style letter/number blur
    "5sS",
    "6bG",
    "7",
    "8B",          # B02 → 862
    "9gq",
    "f",
    "rR",          # 0xfffffff568 → 0xffffffr568 (r stands in for f)
    "cv",
    "ae",
    "xX",
    "mh",
    "nw",
    "kp",
    "uU",
    "y",
    "j",
    "g",
    "e",
    "s",           # s is also in "5sS"; both classes apply, see EXTRA below
    "i",
    "l",
    "o",           # o is also in "0oOqQdD"
    "0",
    "1",
    "5",
    "8",
    "9",
]
EXTRA_PAIRS = [
    (":", ";"), (":", "c"), (":", "J"), (":", "l"), (":", ""),
    ("=", "-"), ("=", "h"), ("=", "."),
    ("_", "."), (".", ","), ("|", "/"), ("(", "["), (")", "]"),
    ("x", "c"), ("x", "%"), ("n", "|"), ("v", "c"),
    ("b", "6"), ("d", "0"), ("D", "0"), ("t", "1"),
    ("c", "e"), ("e", "o"),
    ("r", "f"),      # 0xffffffr568 ← 0xfffffff568: a run of f's, one read as r
    ("M", "H"),      # 'H:stalled pre' — the leading M read as H
    ("N", "M"),
    ("S", "5"), ("B", "8"), ("G", "6"), ("Q", "0"),
]

_CLASS: dict[str, set[int]] = {}
for _gid, _group in enumerate(GROUPS):
    for _ch in _group:
        _CLASS.setdefault(_ch.lower(), set()).add(_gid)
        _CLASS.setdefault(_ch.upper(), set()).add(_gid)
_PAIR: set[frozenset] = {frozenset((a, b)) for a, b in EXTRA_PAIRS if a and b}


def classes_of(ch: str) -> set[int]:
    return _CLASS.get(ch.lower(), {500 + ord(ch)})


def confusable(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    if frozenset((a, b)) in _PAIR or frozenset((a.lower(), b.lower())) in _PAIR:
        return True
    return bool(classes_of(a) & classes_of(b))


def distance(needle: str, hay: str) -> float:
    """Alignment cost: equal 0, confusable 0.3, otherwise 1.0; gaps 0.45.

    Needleman-Wunsch without the traceback: we only need the cost, the field capture
    comes from where the literal chunks landed.
    """
    n, m = len(needle), len(hay)
    if n == 0 or m == 0:
        return float(n or m) * 0.45
    prev = [j * 0.45 for j in range(m + 1)]
    for i in range(1, n + 1):
        a = needle[i - 1]
        cur = [i * 0.45] + [0.0] * m
        for j in range(1, m + 1):
            b = hay[j - 1]
            if a == b:
                sub = prev[j - 1]
            elif a.lower() == b.lower():
                sub = prev[j - 1] + 0.05
            elif confusable(a, b):
                sub = prev[j - 1] + 0.3
            else:
                sub = prev[j - 1] + 1.0
            cur[j] = min(sub, cur[j - 1] + 0.45, prev[j] + 0.45)
        prev = cur
    return prev[m]


def locate(literal: str, hay: str, scan: int = 40):
    """Where does this literal chunk best sit in `hay`?  → (start, width, cost) or None."""
    if not literal:
        return (0, 0, 0.0)
    if not hay:
        return None
    limit = min(len(hay), scan)
    widest = int(len(literal) * 1.5) + 3
    best = None
    for start in range(limit):
        for width in range(max(1, len(literal) - 2), min(widest, len(hay) - start) + 1):
            cost = distance(literal, hay[start:start + width])
            if best is None or cost < best[2]:
                best = (start, width, cost)
        if best and best[2] == 0.0:
            break
    budget = max(1.0, 0.4 * len(literal))
    if best and best[2] <= budget:
        return best
    return None


# ===========================================================================
# 3. typed field repair
# ===========================================================================

ALPHABET = {
    "ptr": "0123456789abcdef",
    "hex16": "0123456789abcdef",
    "uint": "0123456789",
    "int": "0123456789",
    "text": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.+-",
    "word": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.+-",
}
SHAPE = {
    "ptr": re.compile(r"0x[0-9a-f]{1,16}"),
    "hex16": re.compile(r"0x[0-9a-f]{16}"),
    "uint": re.compile(r"[0-9]+"),
    "int": re.compile(r"-?[0-9]+"),
    "text": re.compile(r"[A-Za-z0-9_.+-]{1,48}"),
    "word": re.compile(r"[A-Za-z0-9_.+-]{1,48}"),
}


@dataclass
class FieldFix:
    truth: str
    grade: str            # EXACT | FIXED | SYMS | AMBIG
    ambiguous: int = 0
    raw: str = ""
    label: str = ""


def _squash(text: str) -> str:
    return "".join(c for c in text if c not in " \t·")


def _options(ch: str, alphabet: str) -> list[str]:
    if ch in alphabet:
        return [ch]
    return [c for c in alphabet if confusable(ch, c)]


def snap_address(body: str, syms: set[str]):
    """The unique symbol address this could have been, or None."""
    hits = [a for a in syms if len(a) == len(body) and distance(a, body) <= 1.5]
    return hits[0] if len(hits) == 1 else None


def _trim(body: str, alphabet: str):
    """Drop at most three characters from either edge until everything left is legal."""
    for _ in range(3):
        keep = "".join(c for c in body if c in alphabet)
        if keep == body:
            return body, True
        # only ever an edge: a hole in the middle means this is not the field we think
        if body[0] not in alphabet:
            body = body[1:]
            continue
        if body[-1] not in alphabet:
            body = body[:-1]
            continue
        return None
    return (body, True) if body else None


def _starts_with_hex(text: str) -> bool:
    return bool(re.match(r"(0x|ox|0[a-zA-Z])", text.lower()))


def opts_for_body(body: str, alphabet: str):
    """Per-character alphabet options, tolerating at most three stray edge characters.

    The anchor chunks (`" cnt="`, `" c0="`) are what we trust; a letter that leaked in at
    an edge belongs to the neighbour, so cutting it is a repair of the *capture*, not of
    the value.  A hole in the middle is a different matter: that returns None.
    """
    for candidate in (body, *(_trim_once(body, alphabet, n) for n in (1, 2, 3))):
        if candidate is None:
            continue
        per_char = [_options(ch, alphabet) for ch in candidate]
        if all(per_char):
            return candidate, per_char
    return None


def _trim_once(body: str, alphabet: str, n: int):
    if n <= 0 or not body:
        return body if body else None
    if body[0] not in alphabet and n % 2 == 1:
        return body[1:]
    if body[-1] not in alphabet:
        return body[:-1]
    if body[0] not in alphabet:
        return body[1:]
    return None


def repair_field(f: F, raw: str, syms=None) -> FieldFix | None:
    text = _squash(raw)
    if not text:
        return None
    label = f.label
    trimmed = False

    if f.kind == "word" and f.candidates:
        best = min(f.candidates, key=lambda c: distance(c, text))
        if distance(best, text) <= max(1.2, 0.3 * len(best)):
            return FieldFix(best, "EXACT" if best == text else "FIXED", 0, raw, label)
        return None

    if f.kind in ("ptr", "hex16"):
        body = text
        low = body.lower()
        for strip in ("0x", "ox", "0x", "0v", "0>", "dx", "ox"):
            if low.startswith(strip):
                body = body[2:]
                break
        else:
            if body.startswith("0") and len(body) > 3 and not body[1].isdigit():
                body = body[1:]
        resolved = opts_for_body(body, ALPHABET["ptr"] + "xX")
        if resolved is None:
            return None
        body, opts_list = resolved
        ambiguous = sum(1 for o in opts_list if len(o) > 1)
        pin = "".join(o[0] for o in opts_list)
        if f.kind == "hex16" and len(pin) != 16:
            return None
        # a kernel pointer is 16 digits; anything between "0xf8" and that is a dropped
        # digit, and only the symbol table may decide it
        if len(pin) not in range(1, 4) and len(pin) != 16:
            ambiguous = max(ambiguous, 1)
        if syms and ambiguous:
            for combo in _combos(opts_list):
                hit = snap_address(combo, syms)
                if hit:
                    return FieldFix("0x" + hit, "SYMS", 0, raw, label)
        if ambiguous == 0:
            truth = "0x" + pin
            if not SHAPE["ptr"].fullmatch(truth):
                return None
            return FieldFix(truth, "EXACT" if text == truth else "FIXED", 0, raw, label)
        if syms:
            # try every reading, keep the ones that are real symbol addresses
            real = []
            for combo in _combos(opts_list):
                hit = snap_address(combo, syms)
                if hit and hit not in real:
                    real.append(hit)
            if len(real) == 1:
                return FieldFix("0x" + real[0], "SYMS", 0, raw, label)
            if len(real) > 1:
                return FieldFix("0x" + real[0], "AMBIG", len(real), raw, label)
        return FieldFix("0x" + pin, "AMBIG", ambiguous, raw, label)

    signed = text.startswith("-")
    body = text.lstrip("-")
    resolved = opts_for_body(body, ALPHABET["uint"])
    if resolved is None:
        return None
    body, opts_list = resolved
    ambiguous = sum(1 for o in opts_list if len(o) > 1)
    pin = "".join(o[0] for o in opts_list)
    truth = ("-" if signed else "") + pin
    if not SHAPE[f.kind].fullmatch(truth):
        return None
    if ambiguous:
        return FieldFix(truth, "AMBIG", ambiguous, raw, label)
    return FieldFix(truth, "EXACT" if truth == text else "FIXED", 0, raw, label)


def load_syms(path: Path) -> set[str]:
    """Every 16-hex-digit address in an `nm`-style symbol dump.

    The tree's own `nm -arch arm64 -m` output is the cheapest oracle for the panel:
    a `%p` we read off a photo is only believable if it is an address the kernel has.
    """
    out: set[str] = set()
    for line in path.read_text(errors="replace").splitlines():
        m = re.search(r"\b([0-9a-f]{16})\b", line)
        if m:
            out.add(m.group(1))
    return out


def _combos(opts_list, cap=4096):
    space = 1
    for o in opts_list:
        space *= len(o)
    if space > cap:
        # too many readings to enumerate: keep the ones that differ in ≤2 places
        base = [o[0] for o in opts_list]
        out = ["".join(base)]
        for idx, opts in enumerate(opts_list):
            for alt in opts[1:]:
                variant = list(base)
                variant[idx] = alt
                out.append("".join(variant))
        return out[:cap]
    return ["".join(c) for c in itertools.product(*opts_list)]


# ===========================================================================
# 4. rows and matching
# ===========================================================================

@dataclass
class Row:
    text: str
    display: str
    y: float
    x: float
    photo: str
    conf: float
    x1: float = 0.0
    height: float = 0.0


@dataclass
class Reading:
    raw: str
    truth: str | None
    grade: str
    kind: str                    # template | milestone | raw
    template: str | None = None
    fields: dict = dsfield(default_factory=dict)
    worst: str = "EXACT"
    cost: float = 0.0
    photo: str = ""
    y: float = 0.0
    seen: int = 1


def rows_from_boxes(boxes, photo: str) -> list[Row]:
    """Cluster OCR boxes into panel lines.

    rapidocr already returns one box per text line most of the time, and the photo is
    *skewed* — the left end of a line sits 20 px higher than its right end — so merging
    neighbours by a generous vertical tolerance glues two panel lines into one row and
    breaks the template match.  A box is only appended to the previous row when it is a
    real continuation: nearly the same baseline **and** horizontally adjacent (a moire
    band that cut a line in half), never merely below it.
    """
    items = []
    for box in boxes:
        poly, text = box[0], box[1]
        xs = [float(q[0]) for q in poly]
        ys = [float(q[1]) for q in poly]
        height = max(ys) - min(ys)
        conf = float(box[2]) if len(box) > 2 and box[2] else 1.0
        items.append((min(ys) + height / 2, min(xs), max(xs), height, text, conf))
    items.sort(key=lambda t: (t[0], t[1]))
    rows: list[Row] = []
    for mid, x0, x1, height, text, conf in items:
        merged = False
        if rows:
            prev = rows[-1]
            same_baseline = abs(mid - prev.y) <= max(3.0, min(height, prev.height) * 0.30)
            adjacent = x0 >= prev.x1 - 12 and x0 - prev.x1 < max(4.0, prev.height * 2.0)
            if same_baseline and adjacent:
                prev.text += text
                prev.display = prev.display + " " + text
                prev.x1 = x1
                prev.y = (prev.y + mid) / 2.0
                prev.conf = min(prev.conf, conf)
                merged = True
        if not merged:
            rows.append(Row(text, text, mid, x0, photo, conf, x1, height))
    return rows


GRADE_ORDER = ["EXACT", "SYMS", "FIXED", "OPEN", "AMBIG", "RAW"]


def worse(a: str, b: str) -> str:
    return a if GRADE_ORDER.index(a) >= GRADE_ORDER.index(b) else b


def match_row(row: Row, vocab: Vocabulary, syms=None) -> Reading:
    text = _squash(row.text)
    best: tuple[float, Template, list] | None = None
    for tpl in vocab.templates:
        captured = _match_template(text, tpl)
        if captured is None:
            continue
        cost = captured[0]
        if best is None or cost < best[0]:
            best = (cost, tpl, captured[1], captured[2])
    mile = _match_milestone(text, vocab)
    if mile is not None and (best is None or mile[0] < best[0]):
        cost, truth, grade, name = mile
        return Reading(row.text, truth, grade, "milestone", None, {"tag": truth.split(":")[0],
                                                                "name": name}, grade, cost,
                       row.photo, row.y)
    if best is None:
        return Reading(row.text, None, "RAW", "raw", None, {}, "RAW", 99.0, row.photo, row.y)

    cost, tpl, raw_fields, tail = best
    values, grade = [], "EXACT"
    fields = {}
    for spec, rawvalue in zip(tpl.fields, raw_fields):
        fix = repair_field(spec, rawvalue, syms)
        if fix is None:
            return Reading(row.text, None, "RAW", "raw", tpl.shape, {}, "RAW", cost,
                           row.photo, row.y)
        values.append(fix.truth)
        grade = worse(grade, fix.grade)
        key = fix.label or f"f{len(fields)}"
        n = 1
        while key in fields:
            key = f"{fix.label or 'f'}#{n}"; n += 1
        fields[key] = fix.truth
    truth = tpl.render(values)
    if tail:
        # the rest of the line was printed by another call (or is moire junk): kept
        # verbatim, and its key=value pairs parsed without repair so a rule can use them
        grade = worse(grade, "OPEN")
        truth += tail
        fields["tail"] = tail
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)", tail):
            fields.setdefault(key, value)
        first = tail.split(" ")[0].strip()
        if tpl.chunks[-1].rstrip().endswith("n=") and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", first):
            fields["n"] = first
    return Reading(row.text, truth, grade, "template", tpl.shape, fields, grade, cost,
                   row.photo, row.y)


def debug_row(text: str, vocab: Vocabulary, syms=None) -> str:
    out = [f"row    {text}", f"squash {_squash(text)}", ""]
    for tpl in vocab.templates[:14]:
        out.append(f"--- {tpl.shape}")
        pos, ok = 0, True
        fields = []
        for index, chunk in enumerate(tpl.chunks):
            if not chunk:
                out.append(f"      (empty chunk {index})")
                continue
            spot = locate(chunk, text[pos:], scan=48 if index == 0 else 34)
            if spot is None:
                out.append(f"      chunk {chunk!r} at {pos}: NOT FOUND "
                           f"(best {min((distance(chunk, text[pos + s:pos + s + w]) for s in range(min(20, max(1, len(text) - pos))) for w in range(max(1, len(chunk) - 2), min(len(chunk) + 4, max(1, len(text) - pos - s) + 1)) if pos + s + w <= len(text)), default=float('nan')):.2f})")
                ok = False
                break
            start, width, cost = spot
            if index > 0:
                fields.append(text[pos:pos + start])
            out.append(f"      chunk {chunk!r} → @{pos + start}+{width} cost {cost:.2f} "
                       f"[{text[pos + start:pos + start + width]!r}]")
            pos += start + width
        if not ok:
            continue
        if len(fields) != len(tpl.fields):
            out.append(f"      field count {len(fields)} != {len(tpl.fields)}")
        tail = text[pos:]
        out.append(f"      tail {tail[:80]!r}")
        for spec, raw in zip(tpl.fields, fields):
            fix = repair_field(spec, raw, syms)
            out.append(f"      field {spec.kind:6s} label={spec.label or '-':7s} "
                       f"raw={raw!r:26s} → " +
                       ("None" if fix is None else f"{fix.truth!r} [{fix.grade}]"))
    mile = _match_milestone(_squash(text), vocab)
    out.append(f"milestone → {mile}")
    reading = match_row(Row(text, text, 0, 0, "debug", 1.0), vocab, syms)
    out.append(f"match_row → grade={reading.grade} kind={reading.kind}")
    out.append(f"            truth={reading.truth!r}")
    return "\n".join(out)


def _match_template(text: str, tpl: Template):
    """Anchor every literal chunk in order; whatever falls between is a field capture.

    Returns (cost, fields, tail).  A tail is what the panel printed after the template
    ended — real lines do that: `M:bad[…] … n=` is one snprintf and the class name is
    printed by the next call, so the tail is kept verbatim and graded OPEN, never repaired.
    """
    pos = 0
    cost = 0.0
    fields: list[str] = []
    for index, chunk in enumerate(tpl.chunks):
        if not chunk:
            continue
        spot = locate(chunk, text[pos:], scan=48 if index == 0 else 34)
        if spot is None:
            return None
        start, width, chunk_cost = spot
        if index > 0:
            fields.append(text[pos:pos + start])
        cost += chunk_cost
        pos += start + width
    # a template whose line ends inside a field (the usual case) has an empty final chunk:
    # whatever is left *is* that field, not a tail
    if len(fields) == len(tpl.fields) - 1:
        fields.append(text[pos:])
        return cost, fields, ""
    if len(fields) != len(tpl.fields):
        return None
    tail = text[pos:]
    if tail and not tpl.chunks[-1]:
        if fields:
            fields[-1] += tail            # the row ends inside the last field: it is part of it
        tail = ""
    # whatever the template did not explain is paid for, so a template that explains the
    # whole row always beats one that explains the beginning of it (v40 selftest)
    cost += 0.18 * len(tail)
    return cost, fields, tail


# The glyph class in front of a milestone's number is not hand-typed any more: it is every
# glyph the measured confusion model says can stand in for a digit (`8B`, `9gq`, `1lIi|!t` …).
# Hand-picking that class is what made rows like `B11q:at_hashinit` unparseable: `q` was in
# SUBSTITUTE but not in the class, so the row was thrown away whole.
NUMCLASS = "[0-9" + "".join(
    sorted({c for g in GROUPS for c in g if not c.isdigit() and any(d.isdigit() for d in g)})) + "]"
MILE_RE = re.compile(
    rf"^([KBTUIOPD])[^0-9A-Za-z]{{0,2}}({NUMCLASS}{{1,3}})[.;:_|lI]?([A-Za-z][A-Za-z0-9_.+-]*)$",
    re.I)
# The head of a milestone row is one glyph in front of a number, which is the most fragile
# thing on the panel: the camera eats it (`B155:at_bsd_bufferinit` -> `155:t_bsd_buffcrinit`).
# Such a row used to be RAW, i.e. the stage name -- the part that says how far BSD got --
# was thrown away with the letter. These two fallbacks match number+name without a head, and
# the colon form only accepts a head that is nothing but (letter)(digits), so a template
# line can never be mistaken for a milestone.
MILE_HEADLESS_RE = re.compile(
    rf"^({NUMCLASS}{{1,4}})[.;:_|lI]?([A-Za-z][A-Za-z0-9_.+-]{{3,}})$", re.I)
MILE_COLON_RE = re.compile(
    rf"^([KBTUIOPD]?{NUMCLASS}{{1,4}}):(?=[A-Za-z0-9])(.+)$", re.I)


def _match_milestone(text: str, vocab: Vocabulary):
    m = MILE_RE.match(text)
    headless = False
    if m:
        tag, number, name = m.group(1).upper(), m.group(2), m.group(3)
    else:
        m = MILE_HEADLESS_RE.match(text)
        if m:
            headless, tag, number, name = True, "", m.group(1), m.group(2)
        else:
            m = MILE_COLON_RE.match(text)
            if not m:
                return None
            headless = True
            head = m.group(1)
            tag = head[0].upper() if head[0].isalpha() else ""
            number = head[1:] if tag else head
            name = m.group(2)
    pool = vocab.milestones.get(tag) or set()
    if not pool:
        # A tag this build never prints must not borrow another subsystem's names: with a
        # two-name K pool, every unreadable K row snapped to whichever K name was closer.
        # Saying nothing (RAW) is the only honest answer.
        return None
    best = min(pool, key=lambda c: distance(c, name))
    cost = distance(best, name)
    if cost > max(1.5, 0.34 * len(best)):
        return None
    # The tag is not decoration: K is the kernel bootstrap, B the BSD stages, U this driver,
    # I the mount path, T the thread calls -- a round's verdict can hang off one letter. The
    # camera blurs that letter as easily as any glyph (`U137` -> `i37`), so when the letter
    # the camera produced cannot own the name it printed, and exactly one tag can, the name
    # decides the tag. If several tags use the same name, say we do not know instead of
    # keeping the guessed letter: answering with confidence on the wrong subsystem is the
    # one failure this tool must never have (the v42 photos produced `I37:root-plan-fail`
    # for a U row, and that grade was FIXED).
    owners = {t for t, ns in vocab.milestones.items() if best in ns}
    if headless:
        # a row without its head may only be answered when the name has exactly one owner,
        # and never better than FIXED: something in front of it is missing, by proof.
        if len(owners) != 1 or cost > max(1.2, 0.28 * len(best)):
            return None
        if not tag:
            tag = next(iter(owners))
        grade = "FIXED"
    else:
        if tag not in owners:
            if len(owners) == 1:
                tag = next(iter(owners))
            else:
                return max(cost, 0.1), f"{tag}{_digits(number)}:{best}", "AMBIG", best
        grade = "EXACT" if best == name else "FIXED"
    digits = _digits(number)
    # the counter is decorative for judgement; say so when it is not trustworthy
    if digits != number and not re.fullmatch(r"[0-9]+", number):
        grade = worse(grade, "OPEN")
    return cost, f"{tag}{digits}:{best}", grade, best


def _digits(number: str) -> str:
    out = ""
    for ch in number:
        opts = _options(ch, "0123456789")
        if not opts:
            return number
        out += opts[0]
    return out.lstrip("0") or "0"


def build_transcript(rows, vocab, syms=None) -> list[Reading]:
    out: list[Reading] = []
    seen: dict[str, Reading] = {}
    for row in rows:
        reading = match_row(row, vocab, syms)
        key = reading.truth or reading.raw
        if key in seen:
            seen[key].seen += 1
            continue
        seen[key] = reading
        out.append(reading)
    return out


# ===========================================================================
# 5. OCR
# ===========================================================================

def rapidocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR          # noqa: F401
    except Exception:
        return None
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _box_json(box):
    poly, text = box[0], box[1]
    conf = float(box[2]) if len(box) > 2 and box[2] else 1.0
    return [[[float(q[0]), float(q[1])] for q in poly], text, conf]


def ocr_dump(paths) -> None:
    """Internal: print the raw boxes as JSON so the OCR interpreter stays out of the pipeline."""
    engine = rapidocr_engine()
    if engine is None:
        sys.exit("read-panel: rapidocr_onnxruntime is not importable here")
    payload = {}
    for path in paths:
        boxes, _ = engine(str(path))
        payload[str(path)] = [_box_json(box) for box in (boxes or [])]
    print(json.dumps(payload))


CACHE = Path(os.environ.get("PANEL_OCR_CACHE",
                           str(Path.home() / ".dsh-qqbot/workspace/.panel-ocr-cache.json")))


def _cache_load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _cache_key(image: Path) -> tuple[str, int, float]:
    st = image.stat()
    return (str(image), st.st_size, st.st_mtime)


def collect_rows(images, use_cache=True) -> list[Row]:
    """OCR every photo, with a cache: one photo takes ~4 s and a round reads a dozen."""
    cache = _cache_load() if use_cache else {}
    todo, done = [], {}
    for image in images:
        key = _cache_key(Path(image))
        if key in cache:
            done[str(image)] = cache[key]["boxes"]
        else:
            todo.append(image)
    rows = []
    for image in images:          # keep the caller's order
        if str(image) in done:
            rows.extend(rows_from_boxes(done[str(image)], str(image)))
    if not todo:
        return rows
    fresh = _collect_rows_ocr(todo)
    for row in fresh:
        rows.append(row)
    if use_cache:
        for image in todo:
            key = _cache_key(Path(image))
            boxes = [b for b in _OCR_BOXES.get(str(image), [])]
            cache[str(key)] = {"boxes": boxes}
        try:
            CACHE.write_text(json.dumps(cache))
        except OSError:
            pass
    return rows


_OCR_BOXES: dict[str, list] = {}


def _collect_rows_ocr(images) -> list[Row]:
    engine = rapidocr_engine()
    if engine is not None:
        rows: list[Row] = []
        for image in images:
            boxes, _ = engine(str(image))
            _OCR_BOXES[str(image)] = [_box_json(b) for b in (boxes or [])]
            rows.extend(rows_from_boxes(boxes or [], str(image)))
        return rows
    for candidate in DEFAULT_OCR_PYTHONS:
        if not candidate:
            continue
        probe = Path(candidate)
        if not probe.exists():
            continue
        try:
            out = subprocess.run([str(probe), os.path.abspath(__file__), "--ocr-dump",
                                  *[str(i) for i in images]],
                                 capture_output=True, text=True, timeout=1200)
        except (OSError, subprocess.TimeoutExpired) as exc:
            sys.stderr.write(f"read-panel: {candidate}: {exc}\n")
            continue
        if out.returncode != 0:
            sys.stderr.write(f"read-panel: {candidate} failed: {out.stderr.strip()[:400]}\n")
            continue
        rows = []
        for path, boxes in json.loads(out.stdout.strip().splitlines()[-1]).items():
            _OCR_BOXES[path] = boxes
            rows.extend(rows_from_boxes(boxes, path))
        return rows
    sys.exit(
        "read-panel: no interpreter with rapidocr-onnxruntime.\n"
        "  python3 -m venv ~/.venv-ocr && ~/.venv-ocr/bin/pip install rapidocr-onnxruntime\n"
        "  (or OCR_PY=… scripts/read-panel.py …).  Everything except the OCR step runs anyway:\n"
        "  scripts/read-panel.py --list / --selftest")


# ===========================================================================
# 6. judgement table of a round
# ===========================================================================

def load_rules(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:
        sys.exit("read-panel: --round needs python ≥3.11 (tomllib) — or read the NOTES by hand")
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def field_of(truth: str, key: str):
    m = re.search(re.escape(key) + r"=?([A-Za-z0-9_.+-]+)", truth)
    return m.group(1) if m else None


def compare(value, op, target) -> bool:
    try:
        left = float(value)
        right = float(target)
    except (TypeError, ValueError):
        left, right = str(value), str(target)
        if op not in ("==", "!="):
            return False
    return {"==": left == right, "!=": left != right, ">=": left >= right,
            "<=": left <= right, ">": left > right, "<": left < right}[op]


def apply_rules(transcript, rules: dict, round_name: str) -> dict:
    lines = [r.truth or r.raw for r in transcript]
    blob = "\n".join(lines)
    matched, dead = [], []
    for rule in rules.get("rule", []):
        if rule.get("round") and rule["round"] != round_name:
            continue
        why = []
        ok = True
        for pattern in rule.get("require", []):
            if not re.search(pattern, blob):
                ok = False
                why.append(f"missing {pattern!r}")
        for pattern in rule.get("forbid", []):
            if re.search(pattern, blob):
                ok = False
                why.append(f"forbidden {pattern!r} present")
        for check in rule.get("check", []):
            line = next((l for l in lines if check["line"] in l), None)
            if line is None:
                ok = False
                why.append(f"no line {check['line']!r}")
                continue
            value = field_of(line, check["key"])
            if value is None or not compare(value, check.get("op", "=="), check["value"]):
                ok = False
                why.append(f"{check['line']} {check['key']}={value!r} not "
                           f"{check.get('op','=')} {check['value']!r}")
        (matched if ok else dead).append((rule, why))
    signals = []
    for signal in rules.get("signal", []):
        if signal.get("round") and signal["round"] != round_name:
            continue
        present = any(re.search(signal["pattern"], line) for line in lines)
        signals.append((signal, present))
    return {"matched": matched, "dead": dead, "signals": signals}


# ===========================================================================
# 7. selftest — the repair layer, no OCR, no device, deterministic
# ===========================================================================

HEXADDR = ["0xfffffff007ae36b0", "0xfffffff0072086d0", "0xfffffff007c1c130",
           "0xfffffff007adfcd8", "0xfffffff568caffe0", "0xfffffff007adfb90",
           "0xfffffff006b2a218", "0xfffffff0078926a0"]
SMALLHEX = ["0x6", "0xac", "0x18", "0x4a", "0x9b", "0xf8", "0x0"]


def sample_value(f: F, rng) -> str:
    if f.kind == "hex16":
        return rng.choice(HEXADDR) if rng.random() < 0.7 else "0x" + "".join(
            rng.choice("0123456789abcdef") for _ in range(16))
    if f.kind == "ptr":
        return rng.choice(HEXADDR + SMALLHEX)
    if f.kind == "uint":
        return str(rng.choice([0, 1, 2, 3, 9, 24, 155, 172, 4300, 1708]))
    if f.kind == "int":
        return str(rng.choice([-1, 0, 1, 16, 1652, -4096]))
    if f.kind == "word" and f.candidates:
        return rng.choice(sorted(f.candidates))
    return rng.choice(["pre", "post", "scan", "NativeUfsBlock", "base-dup"])


SUBSTITUTE = {}


def _substitute_map():
    for gid, group in enumerate(GROUPS):
        for ch in group:
            for variant in {ch, ch.upper(), ch.lower()}:
                alts = {c for c in group if c.lower() != ch.lower()} | {ch.upper(), ch.lower()}
                alts = {a for a in alts if a != variant}
                if alts:
                    SUBSTITUTE.setdefault(variant, set()).update(alts)
    for a, b in EXTRA_PAIRS:
        if a and b:
            SUBSTITUTE.setdefault(a, set()).add(b)
            SUBSTITUTE.setdefault(b, set()).add(a)
    return SUBSTITUTE


SUBSTITUTE = _substitute_map()


NUMERICISH = set("0123456789abcdefxXABCDEF")


def ocr_corrupt(line: str, rng, drop=0.03, insert=0.015, substitute=0.12) -> str:
    """Damage a panel line the way the camera + rapidocr did it.

    Measured on IMG_7046..IMG_7063: the English chunks survive (`M:stalled`, `cnt=`) and
    the damage concentrates in the digit runs, where `0`/`o`, `f`/`r`, `5`/`s` swap and the
    spaces around `=` disappear.  So digits get `substitute`, letters get a third of it.
    """
    out = []
    for ch in line:
        if rng.random() < drop:
            continue
        out.append(ch)
        if rng.random() < insert:
            out.append(rng.choice("0o1s5fBrx. "))
    chars = list("".join(out))
    for i, ch in enumerate(chars):
        rate = substitute if ch in NUMERICISH else substitute / 3
        if rng.random() < rate and SUBSTITUTE.get(ch):
            chars[i] = rng.choice(sorted(SUBSTITUTE[ch]))
    return "".join(chars)


SYMS_FIXTURE = set(a[2:] for a in HEXADDR)


def selftest(vocab: Vocabulary, seed=20261118) -> int:
    import random as _random
    rng = _random.Random(seed)
    cases: list[tuple[str, str, Reading]] = []
    for tpl in vocab.templates:
        if not tpl.fields:
            continue
        for strength in (0.06, 0.12, 0.20):
            truth = tpl.render([sample_value(f, rng) for f in tpl.fields])
            noisy = ocr_corrupt(truth, rng, substitute=strength)
            if strength > 0.08:            # rapidocr really does lose the spaces around `=`
                noisy = re.sub(r" (?=\w+=)", "", noisy)
                noisy = re.sub(r"= ", "=", noisy)
            cases.append((truth, noisy,
                          match_row(Row(noisy, noisy, 0, 0, "selftest", 1.0), vocab,
                                    SYMS_FIXTURE)))
    # milestone names must come back exactly, including the noisy tag/number
    for tag, name in sorted((t, n) for t, ns in vocab.milestones.items() for n in ns):
        for strength in (0.05, 0.14):
            truth = f"{tag}{rng.randrange(1, 199)}:{name}"
            noisy = ocr_corrupt(truth, rng, drop=0.03, insert=0.01, substitute=strength)
            cases.append((truth, noisy,
                          match_row(Row(noisy, noisy, 0, 0, "selftest", 1.0), vocab,
                                    SYMS_FIXTURE)))
    # A repair tool is not graded on "did it guess right" but on "did it ever answer
    # with confidence when it was wrong".  Three buckets, and only lies are failures:
    #   recovered  truth came back character for character
    #   flagged    the script said it could not settle it (AMBIG, or RAW)
    #   LIE        a confident grade on a line that is not the truth
    def meaning(truth: str) -> str:
        # a milestone's sequence number can lose a digit to the camera with nothing to
        # validate it against; the name is the meaning
        m = re.fullmatch(r"([A-Z])(\d+):(.*)", truth)
        return f"{m.group(1)}?:{m.group(3)}" if m else truth

    recovered = [c for c in cases if meaning(c[2].truth or "") == meaning(c[0])]
    flagged = [c for c in cases if meaning(c[2].truth or "") != meaning(c[0])
               and c[2].grade in ("AMBIG", "RAW", "OPEN")]
    lies = [c for c in cases if meaning(c[2].truth or "") != meaning(c[0])
            and c[2].grade not in ("AMBIG", "RAW", "OPEN")]
    syms_ok = [c for c in cases if c[2].grade == "SYMS"]
    print(f"selftest on {len(cases)} lines ({vocab.summary()}), seed {seed}, "
          f"syms fixture {len(SYMS_FIXTURE)} addresses")
    print(f"  recovered {len(recovered)} · flagged {len(flagged)} · LIE {len(lies)}"
          f" · settled by symbol table {len(syms_ok)}")
    for truth, noisy, reading in lies[:10]:
        print(f"  LIE grade={reading.grade:5s} cost={reading.cost:.1f}")
        print(f"      truth {truth}")
        print(f"      noisy {noisy}")
        print(f"      got   {reading.truth or reading.raw}")
    for truth, noisy, reading in flagged[:6]:
        print(f"  flagged {reading.grade:5s} truth={truth[:70]}")
        print(f"          noisy={noisy[:70]} got={str(reading.truth or reading.raw)[:70]}")
    return 1 if lies else 0


# ===========================================================================
# 8. cli
# ===========================================================================

def format_transcript(transcript, show_raw=False) -> str:
    out = []
    width = max((len(r.grade) for r in transcript), default=4)
    for r in transcript:
        mark = f"{r.grade:<{width}}"
        if r.kind in ("template", "milestone"):
            extra = f"   [{r.worst}]" if r.worst != r.grade else ""
            out.append(f"{mark}  {r.truth}{extra}")
        else:
            out.append(f"{mark}  {r.raw[:160]}")
        if show_raw:
            out.append(f"{' ' * width}  raw  {r.raw[:200]}")
    return "\n".join(out)


def report_round(transcript, rules: dict, round_name: str) -> str:
    result = apply_rules(transcript, rules, round_name)
    out = []
    for rule, _why in result["matched"]:
        out.append(f"判读格  {rule.get('id', '?')}: {rule.get('verdict', '')}")
        if rule.get("next"):
            out.append(f"  下一步  {rule['next']}")
    for rule, why in result["dead"]:
        if rule.get("id", "").startswith("!"):
            continue
        out.append(f"  not     {rule.get('id', '?')}: {'; '.join(why[:3])}")
    missing = [s for s, present in result["signals"] if not present]
    present = [s for s, ok in result["signals"] if ok]
    if present:
        out.append("  seen    " + ", ".join(s["name"] for s in present))
    if missing:
        out.append("  MISSING " + ", ".join(s["name"] for s in missing))
    if not result["matched"]:
        out.append(f"  这一轮（{round_name}）的判读表没有一格完全成立 —— 看 MISSING 与 not 行。")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="read-panel.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--round", help="judgement table round, e.g. v40")
    ap.add_argument("--rules", type=Path, default=REPO / "docs" / "panel-rules.toml")
    ap.add_argument("--syms", type=Path, help="nm-style symbol file to settle addresses")
    ap.add_argument("--kernel", type=Path,
                    default=REPO.parent / "UniXNU/out/liuqin/kernel/kernel.macho",
                    help="the built image; names it cannot print are dropped from the "
                         "vocabulary (pass '' to disable)")
    ap.add_argument("--list", action="store_true", help="print what this tree can print")
    ap.add_argument("--selftest", action="store_true", help="repair layer only, no OCR")
    ap.add_argument("--raw", action="store_true", help="also print the OCR text per line")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ocr-dump", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--debug-row", metavar="TEXT",
                    help="print how this one row is matched, against every template")
    ap.add_argument("--extra-vocab", action="append", default=[])
    ap.add_argument("--max-lines", type=int, default=0)
    args = ap.parse_args(argv)

    kernel = args.kernel if args.kernel and str(args.kernel) else None
    if kernel and not kernel.is_file():
        print(f"note: no image at {kernel}, vocabulary not restricted to what can print")
        kernel = None
    elif kernel:
        kernel = kernel.resolve()
    vocab = load_vocabulary(args.extra_vocab, kernel)
    if vocab.dropped:
        print("dropped (not present in the image): " + ", ".join(vocab.dropped))

    if args.debug_row:
        syms = load_syms(args.syms) if args.syms else None
        print(debug_row(args.debug_row, vocab, syms))
        return 0

    if args.list:
        print(vocab.describe())
        return 0
    if args.selftest:
        return selftest(vocab)
    if args.ocr_dump:
        ocr_dump(args.images)
        return 0
    if not args.images:
        ap.error("no photos given (or use --list / --selftest)")

    syms = load_syms(args.syms) if args.syms else None
    rows = collect_rows(args.images)
    transcript = build_transcript(rows, vocab, syms)
    if args.max_lines:
        transcript = transcript[:args.max_lines]

    if args.json:
        print(json.dumps([{k: v for k, v in r.__dict__.items()} for r in transcript],
                         ensure_ascii=False, indent=1))
        return 0

    grades = {}
    for r in transcript:
        grades[r.grade] = grades.get(r.grade, 0) + 1
    print(f"# liuqin panel transcript — {len(args.images)} photo(s), "
          f"{len(rows)} row(s) → {len(transcript)} line(s)  "
          + " ".join(f"{k}={v}" for k, v in sorted(grades.items())))
    if syms is not None:
        print(f"# symbol table: {len(syms)} addresses")
    print(format_transcript(transcript, args.raw))
    if args.round:
        if not args.rules.exists():
            sys.exit(f"read-panel: no judgement table at {args.rules}")
        print()
        print(report_round(transcript, load_rules(args.rules), args.round))
    return 0


if __name__ == "__main__":
    sys.exit(main())
