"""
MultiTabText - the main node: a tabbed text editor (comments, random selectors). Any number of
optional `text` inputs (text, text_2, ...) are added before (default) or after the output when connected.

The text is typed into a tabbed editor (see web/multi_tab_text.js). The editor
stores all tabs as JSON in a hidden widget called `tabs_data`. On execution every
CHECKED tab (or the active tab if none is checked) is processed, joined with new
lines and returned. Each tab is processed separately:

  1. Comments are removed: lines starting with `#` or `//`, and `/* ... */` blocks
     (inline or multi-line).
  2. Dynamic selectors are resolved: `{one|two|}` -> a random entry
     (empty entries allowed, selectors can be nested, e.g. `{a|{b|c}}`).
     Escape literal characters with a backslash: \\{ \\} \\| \\\\
  3. Optional checkbox "Remove empty lines" (saved in the editor state as
     `removeEmptyLines`): empty / whitespace-only lines are removed from the final output.

Model-aware templates (see prompt_vars.py) need no sockets: ComfyUI passes the API prompt through the
hidden `PROMPT` input, and the graph is searched for the nearest checkpoint / diffusion model. Every tab
may then use `%model%`, `%model_type%` and the other placeholders, plus `@if` / `@elseif` / `@else` /
`@endif` blocks:

  @if %model_type% == sdxl
  masterpiece, best quality
  @elseif %model_type% == flux
  a highly detailed photo
  @else
  {{a photo|a painting}}
  @endif
"""

import functools
import json
import os
import random
import re

try:  # imported as part of the package (ComfyUI)
    from .prompt_vars import apply_conditionals, build_context, substitute_variables, uses_prompt_data
except ImportError:  # loaded as a plain module (tests, tools)
    from prompt_vars import apply_conditionals, build_context, substitute_variables, uses_prompt_data

BUILD = "2026-10-21.7"  # shown in the startup / debug messages so mixed or duplicate installs are easy to spot

# Set the environment variable MULTITABTEXT_DEBUG=1 before starting ComfyUI to log what each run receives.
_DEBUG = os.environ.get("MULTITABTEXT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _debug(message: str) -> None:
    if _DEBUG:
        print("[MultiTabText] " + message, flush=True)


DEFAULT_STATE = json.dumps({"active": 0, "tabs": [{"name": "Tab 1", "text": ""}]})

_COMMENT_LINE = re.compile(r"^\s*(#|//)")
_ESCAPABLE = "{}|\\"
_SPECIAL = re.compile(r"[\\{}|]")

# One private RNG for the whole module: seeded from the OS once, so it is not affected by
# other nodes calling random.seed(), and it avoids creating a new Random() on every run.
_RNG = random.Random()


def strip_comments(text: str) -> str:
    """
    Remove comments:
      - a line whose first non-space characters are `#` or `//`  -> whole line removed
      - `/* ... */` block comments (inline or spanning several lines) -> comment text removed;
        a line that contained only comment text is removed entirely.
    An unterminated `/*` comments out everything after it.
    """
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "/*" not in text:
        # Fast paths: no block comments at all.
        if "#" not in text and "//" not in text:
            return text
        match = _COMMENT_LINE.match
        lines = text.split("\n")
        kept = [line for line in lines if not match(line)]
        return text if len(kept) == len(lines) else "\n".join(kept)

    match = _COMMENT_LINE.match
    out, in_block = [], False
    for line in text.split("\n"):
        if not in_block:
            if match(line):
                continue
            if "/*" not in line:  # most lines
                out.append(line)
                continue
        had_comment = in_block
        kept, i = [], 0
        while i < len(line):
            if in_block:
                end = line.find("*/", i)
                if end == -1:
                    break
                in_block, i = False, end + 2
            else:
                start = line.find("/*", i)
                if start == -1:
                    kept.append(line[i:])
                    break
                kept.append(line[i:start])
                had_comment, in_block, i = True, True, start + 2
        result = "".join(kept)
        if had_comment and not result.strip():
            continue
        out.append(result)
    return "\n".join(out)


def _parse_sequence(s: str, i: int, rng, stops: str):
    """Parse text until one of `stops` (or end). Returns (resolved_text, index)."""
    buf = []
    n = len(s)
    search = _SPECIAL.search
    while i < n:
        m = search(s, i)  # jump straight to the next special character
        if m is None:
            buf.append(s[i:])
            i = n
            break
        j = m.start()
        if j > i:
            buf.append(s[i:j])
        c = s[j]
        if c == "\\" and j + 1 < n and s[j + 1] in _ESCAPABLE:
            buf.append(s[j + 1])
            i = j + 2
            continue
        if c in stops:
            i = j
            break
        if c == "{":
            chosen, k = _parse_group(s, j, rng)
            if chosen is None:  # unbalanced -> treat '{' literally
                buf.append(c)
                i = j + 1
            else:
                buf.append(chosen)
                i = k
            continue
        buf.append(c)
        i = j + 1
    return "".join(buf), i


def _parse_group(s: str, i: int, rng):
    """`s[i]` is '{'. Returns (random_choice, index_after_group) or (None, i)."""
    options = []
    j = i + 1
    while True:
        option, j = _parse_sequence(s, j, rng, "|}")
        options.append(option)
        if j >= len(s):
            return None, i
        if s[j] == "|":
            j += 1
            continue
        return rng.choice(options), j + 1  # s[j] == '}'


def resolve_selectors(text: str, rng) -> str:
    if "{" not in text and "\\" not in text:  # nothing to resolve
        return text
    result, _ = _parse_sequence(text, 0, rng, stops="")
    return result


def remove_empty_lines(text: str) -> str:
    """Drop lines that are empty or contain only whitespace."""
    return "\n".join([line for line in text.split("\n") if line.strip()])


def process_text(text: str, rng=None, drop_empty: bool = False, context=None) -> str:
    text = strip_comments(text)
    if context is not None:
        # @if blocks are resolved before the random selectors, so selectors in skipped branches are not
        # used; %placeholders% are replaced afterwards so a model name can never break a selector.
        text = apply_conditionals(text, context)
    result = resolve_selectors(text, rng or _RNG)
    if context is not None:
        result = substitute_variables(result, context)
    if drop_empty:
        # Done last, so lines that became empty after a selector picked "" are removed too.
        result = remove_empty_lines(result)
    return result


def _read_state(raw: str):
    """
    Returns (texts, drop_empty_lines, in_front) from the editor state.
    texts: every CHECKED tab in tab order; if no tab is checked, the active tab
    (so workflows saved before checkboxes existed behave as before).
    drop_empty_lines: the "Remove empty lines" checkbox, stored in the same state.
    in_front: the "In front" checkbox - connected texts go before (True, the default) or after the tabs' text.
    """
    try:
        data = json.loads(raw)
        tabs = data["tabs"]
        drop = data.get("removeEmptyLines") is True
        in_front = data.get("inFront") is not False  # missing / anything but an explicit false -> in front
        checked = [str(t.get("text", "")) for t in tabs if t.get("checked")]
        if checked:
            return checked, drop, in_front
        idx = max(0, min(int(data.get("active", 0)), len(tabs) - 1))
        return [str(tabs[idx].get("text", ""))], drop, in_front
    except Exception:
        return [str(raw)], False, True  # not JSON: treat as plain text


def _selected_texts(raw: str):
    return _read_state(raw)[0]


def process_tabs(texts, drop_empty: bool = False, rng=None, context=None) -> str:
    """Process each tab on its own (comments, @if blocks, selectors, placeholders), then join the non-empty results with new lines."""
    rng = rng or _RNG
    parts = [process_text(t, rng, context=context) for t in texts]
    result = "\n".join([p for p in parts if p.strip()])
    return remove_empty_lines(result) if drop_empty else result


class RandomTextTabs:
    @classmethod
    def INPUT_TYPES(cls):
        # `tabs_data` is hidden by the JS extension; it only carries the editor state.
        # The hidden PROMPT / UNIQUE_ID give the node the workflow graph (model detection) without adding sockets.
        return {
            "required": {
                "tabs_data": ("STRING", {"default": DEFAULT_STATE, "multiline": False}),
            },
            # Optional socket: when connected, that text is placed in front of the editor's output.
            "optional": {
                "text": ("STRING", {"forceInput": True}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "run"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, tabs_data="", **kwargs):
        # Output only varies between runs when a tab contains a {a|b} selector: then NaN (NaN != NaN) makes ComfyUI
        # re-run the node and re-roll. Otherwise the output is fully determined by the inputs, so a constant lets
        # ComfyUI reuse the cached result - and skip re-running every node downstream of this one.
        try:
            texts = _read_state(tabs_data)[0]
        except Exception:
            return float("nan")
        if any("{" in t for t in texts):
            return float("nan")
        if not any(uses_prompt_data(t) for t in texts):
            return ""
        # Model placeholders / @if depend on the graph, which is not part of the cache signature: include what
        # was detected so a different checkpoint (or newly added LoRA Manager metadata) is noticed.
        prompt = kwargs.get("prompt")
        if not isinstance(prompt, dict) or not prompt:
            return float("nan")  # no graph to compare against: keep re-running rather than cache a stale prompt
        try:
            return build_context(prompt, kwargs.get("unique_id")).fingerprint()
        except Exception:
            return float("nan")

    def run(self, tabs_data, text=None, prompt=None, unique_id=None, **kwargs):
        texts, drop_empty, in_front = _read_state(tabs_data)
        # The graph is only inspected when a tab actually uses a placeholder or a conditional.
        context = build_context(prompt, unique_id) if any(uses_prompt_data(t) for t in texts) else None
        result = process_tabs(texts, context=context)

        # Connected texts (`text`, then `text_2`, `text_3`, ... added by the frontend) go in FRONT of the
        # editor's output ("In front" checked, the default) or AFTER it (unchecked), in socket order, one per line. They are used as received (comments / {a|b}
        # selectors / %placeholders% are only processed inside the editor's own tabs). Empty texts are skipped.
        extras = []
        for key, value in kwargs.items():
            m = _TEXT_KEY.match(key)
            if m:
                extras.append((int(m.group(1)), value))
        incoming = [text] + [v for _, v in sorted(extras, key=lambda kv: kv[0])]
        prefixes = [str(v).rstrip("\r\n") for v in incoming if v is not None]
        prefixes = [p for p in prefixes if p.strip()]
        if prefixes:
            if in_front:
                pieces = prefixes + ([result] if result else [])
            else:
                result = result.rstrip("\r\n")  # no stray blank line between the tabs' text and what follows
                pieces = ([result] if result else []) + prefixes
            result = "\n".join(pieces)

        if _DEBUG:
            _debug(
                f"MultiTabText.run build={BUILD} removeEmptyLines={drop_empty} tabs_used={len(texts)} "
                f"texts_connected={len(prefixes)} in_front={in_front} lines_out={result.count(chr(10)) + 1 if result else 0}"
            )
        if context is not None:
            context.warn()
        # "Remove empty lines" applies to the complete output, connected texts included.
        return (remove_empty_lines(result) if drop_empty else result,)


def _split_entries(text: str):
    """Split on commas / newlines, but not inside (...) or {...} so replacements may contain commas."""
    entries, buf, paren, brace, i = [], [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            buf.append(text[i:i + 2])
            i += 2
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren = max(0, paren - 1) if paren else -1
        elif c == "{":
            brace += 1
        elif c == "}":
            brace = max(0, brace - 1) if brace else -1
        if paren < 0 or brace < 0:  # stray closing bracket -> structure is unreliable
            return [e for e in re.split(r"[,\n]", text)]
        if c in ",\n" and paren == 0 and brace == 0:
            entries.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if paren or brace:  # unbalanced -> fall back to plain splitting
        return [e for e in re.split(r"[,\n]", text)]
    entries.append("".join(buf))
    return entries


def parse_rules(tags_text: str):
    """
    Parse the tags box into rules: {lowercase old tag: (old tag, replacement or None)}.

      cat                 -> remove "cat"
      cat=(dog)           -> replace "cat" with "dog"
      cat=({dog|wolf|})   -> replace "cat" with a random choice each time ("" allowed)
      cat=()              -> same as removing
    """
    rules = {}
    for raw in _split_entries(tags_text or ""):
        entry = raw.strip()
        if not entry:
            continue
        old, new = entry, None
        if "=" in entry:
            old, new = entry.split("=", 1)
            old, new = old.strip(), new.strip()
            if new.startswith("(") and new.endswith(")"):
                new = new[1:-1]
            new = new.strip()
        if old and old.lower() not in rules:
            rules[old.lower()] = (old, new)
    return rules


def parse_tags(tags_text: str):
    """Old tags only (unique, longest first)."""
    return sorted((old for old, _ in parse_rules(tags_text).values()), key=len, reverse=True)


# Cleanup passes work on the whole text at once. [^\S\n] is "any whitespace except a line break",
# so every pattern behaves exactly like it would on a single line.
_C_EMPTY_BRACKETS = re.compile(r"\([^\S\n]*\)|\[[^\S\n]*\]")
_C_DUP_COMMAS = re.compile(r",(?:[^\S\n]*,)+")
_C_SPACE_COMMA = re.compile(r"[ \t]+,")
_C_MULTI_SPACE = re.compile(r"[ \t]{2,}")
# Every whitespace character except the line break (Unicode-aware, same set as regex \s minus "\n").
_WS_NO_NL = "".join(chr(c) for c in range(0x3001) if chr(c).isspace() and c != 10)
_TRIM_CHARS = _WS_NO_NL + ","  # characters trimmed from both ends of every line


def clean_separators(text: str) -> str:
    text = _C_EMPTY_BRACKETS.sub("", text)        # brackets left empty: "()" / "[]"
    if "," in text:
        text = _C_DUP_COMMAS.sub(",", text)       # ", ," / ",," -> ","
        if " ," in text or "\t," in text:
            text = _C_SPACE_COMMA.sub(",", text)  # "a ," -> "a,"
    if "  " in text or "\t" in text:              # (a lone single space never needs collapsing)
        text = _C_MULTI_SPACE.sub(" ", text)      # collapse repeated spaces
    # trim leading / trailing commas and spaces on every line
    return "\n".join([line.strip(_TRIM_CHARS) for line in text.split("\n")])


def _trie_regex(words) -> str:
    """
    Regex source matching any of `words`, preferring the LONGEST one (same result as a flat
    longest-first alternation) but sharing prefixes, so the regex engine does far fewer
    comparisons per character of text.
    """
    trie = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = True  # end of a word

    def emit(node):
        children = [(ch, sub) for ch, sub in node.items() if ch != ""]
        if not children:
            return ""
        alts = [re.escape(ch) + emit(sub) for ch, sub in children]
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return "(?:" + body + ")?" if "" in node else body  # greedy "?" => longer word wins

    return emit(trie)


@functools.lru_cache(maxsize=64)
def _compile_rules(tags_text: str, case_sensitive: bool, whole_words_only: bool):
    """Parse the tags box and build the matching regex once; repeated runs reuse it."""
    rules = parse_rules(tags_text)
    if not rules:
        return None
    # str value = fixed replacement (used as is, no per-match work); 1-tuple = contains {a|b} selectors to resolve
    lookup = {}
    for old, new in rules.values():
        if not new:
            value = ""
        elif "{" in new or "\\" in new:
            value = (new,)
        else:
            value = new
        lookup[old if case_sensitive else old.lower()] = value
    olds = sorted((old for old, _ in rules.values()), key=len, reverse=True)
    if all(o.isascii() for o in olds):
        # ASCII tags: shared-prefix regex. In case-insensitive mode letters are lowered first so that
        # different-case duplicates of a prefix cannot create competing branches.
        words = olds if case_sensitive else [o.lower() for o in olds]
        pattern = "(?:" + _trie_regex(words) + ")"
    else:
        pattern = "(?:" + "|".join(re.escape(o) for o in olds) + ")"
    if whole_words_only:
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    has_replacements = any(lookup.values())  # "" (plain removal) is falsy, everything else is a replacement
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE), lookup, has_replacements


def remove_tags(text: str, tags_text: str, case_sensitive=False, whole_words_only=True, clean=True,
                drop_empty_lines=False, rng=None) -> str:
    """Remove or replace tags. All tags are matched in ONE pass, so a replacement is never re-replaced."""
    compiled = _compile_rules(tags_text or "", bool(case_sensitive), bool(whole_words_only))
    if compiled is None:
        return text
    pattern, lookup, has_replacements = compiled

    if has_replacements:
        rng = rng or _RNG

        get = lookup.get

        def repl(m):
            value = get(m.group(0) if case_sensitive else m.group(0).lower(), "")
            if value.__class__ is str:
                return value
            return resolve_selectors(value[0], rng)  # fresh random choice for every occurrence

        text = pattern.sub(repl, text)
    else:
        text = pattern.sub("", text)  # plain removal: no callback needed

    if clean:
        if "\r" in text:
            text = text.replace("\r\n", "\n")
        text = clean_separators(text)
    if drop_empty_lines:
        text = remove_empty_lines(text)
    return text


_TEXT_KEY = re.compile(r"^text_(\d+)$")
SEPARATORS = {"comma": ", ", "new line": "\n", "space": " ", "none": ""}


def join_texts(parts, separator="comma") -> str:
    """Concatenate several texts into one. Empty / missing texts are skipped."""
    sep = SEPARATORS.get(separator, ", ")
    out = []
    for p in parts:
        if p is None:
            continue
        p = str(p)
        if separator == "comma":
            p = p.strip().strip(",").strip()   # avoid "a,, b" when a text already ends with a comma
        elif separator == "new line":
            p = p.strip("\r\n")
        elif separator == "space":
            p = p.strip()
        if p.strip():
            out.append(p)
    return sep.join(out)


class RemoveTags:
    """
    Removes or replaces tags. Inputs `text`, `text_2`, ... (dynamic) are concatenated first. Entries: `tag`, `old=(new)`, `old=({a|b|})`.

    support_comments (off by default): comments are ignored, in the texts AND in the tags box - lines starting with # or //,
    and /* ... */ blocks (inline or over several lines). They are not part of the output and are not read as tags.
    Every text is handled on its own, so an unclosed /* in one text never swallows another.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True}),
                "tags": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "one entry per line and/or comma separated\ncat            (remove)\ndog=(wolf)      (replace)\nbird=({eagle|hawk|})   (random)",
                }),
                "case_sensitive": ("BOOLEAN", {"default": False}),
                "whole_words_only": ("BOOLEAN", {"default": True}),
                "clean_separators": ("BOOLEAN", {"default": True}),
                "remove_empty_lines": ("BOOLEAN", {"default": False}),
                # Kept last so workflows saved before this option existed still load correctly.
                "separator": (list(SEPARATORS.keys()), {"default": "comma"}),
            },
            # Optional, so prompts / nodes made before this option existed (and the pass-through nodes the editor builds
            # for a bypassed Remove Tags) are still valid without it.
            "optional": {
                "support_comments": ("BOOLEAN", {"default": False, "label_on": "comments on", "label_off": "comments off"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "run"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, tags="", **kwargs):
        # Random replacements ({a|b}) must be re-rolled on every run.
        return float("nan") if "{" in (tags or "") else ""

    def run(self, text, tags, case_sensitive=False, whole_words_only=True, clean_separators=True,
            remove_empty_lines=False, separator="comma", support_comments=False, **kwargs):
        # Extra inputs text_2, text_3, ... are added dynamically by the frontend (web/multi_tab_text.js).
        text = "" if text is None else text
        extras = []
        for key, value in kwargs.items():
            m = _TEXT_KEY.match(key)
            if m:
                extras.append((int(m.group(1)), value))
        parts = [text] + [v for _, v in sorted(extras, key=lambda kv: kv[0])]
        if support_comments:  # each text on its own, before they are joined; the tags box too
            parts = [strip_comments(str(p)) if p is not None else p for p in parts]
            tags = strip_comments(tags or "")
        combined = join_texts(parts, separator) if len(parts) > 1 else parts[0]
        return (remove_tags(combined, tags, bool(case_sensitive), bool(whole_words_only),
                            bool(clean_separators), bool(remove_empty_lines)),)


# ---------------------------------------------------------------------------
# Lora Text Loader
# ---------------------------------------------------------------------------

_LORA_TAG = re.compile(r"<\s*lora\s*:([^<>]*)>", re.IGNORECASE)
_LORA_SPACE = re.compile(r"[ \t]{2,}")


def _lora_strength(raw, default):
    """(value, ok). An empty weight uses `default`; a non-number is reported as not ok."""
    text = str(raw or "").strip()
    if not text:
        return default, True
    try:
        return float(text), True
    except ValueError:
        return default, False


def parse_lora_tags(text):
    """
    Split a prompt into (prompt without LoRA tags, tags).

    Tag syntax, case-insensitive:

        <lora:name>              model 1.0 / clip 1.0
        <lora:name:0.8>          model 0.8 / clip 0.8
        <lora:name:0.8:0.5>      model 0.8 / clip 0.5

    Every `<lora:...>` tag is removed from the prompt, also when the LoRA cannot
    be loaded. Tags are returned as {"name", "model_strength", "clip_strength",
    "error", "raw"}. Only LoRA tags are touched; other angle brackets stay.
    """
    source = "" if text is None else str(text)
    if not _LORA_TAG.search(source):
        return source, []

    tags = []
    for match in _LORA_TAG.finditer(source):
        parts = match.group(1).split(":")
        name = parts[0].strip()
        model_raw = parts[1] if len(parts) > 1 else ""
        clip_raw = parts[2] if len(parts) > 2 else ""
        tag = {
            "name": name,
            "model_strength": 1.0,
            "clip_strength": 1.0,
            "error": "",
            "raw": match.group(0),
        }
        if not name:
            tag["error"] = "the LoRA name is empty"
        else:
            model_strength, ok = _lora_strength(model_raw, 1.0)
            if not ok:
                tag["error"] = "invalid model strength {!r}".format(model_raw.strip())
            else:
                clip_strength, ok = _lora_strength(clip_raw, model_strength)
                if not ok:
                    tag["error"] = "invalid clip strength {!r}".format(clip_raw.strip())
                else:
                    tag["model_strength"] = model_strength
                    tag["clip_strength"] = clip_strength
        tags.append(tag)

    # Remove the tags and tidy only what they leave behind: runs of spaces become one, a line
    # that consisted only of tags disappears, original blank lines are kept.
    lines = []
    for line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = _LORA_SPACE.sub(" ", _LORA_TAG.sub("", line)).strip()
        if cleaned or not line.strip():
            lines.append(cleaned)
    return "\n".join(lines).strip(), tags


def _lora_keys(value):
    path = str(value or "").replace("\\", "/").strip().lstrip("/").casefold()
    base = path.rsplit("/", 1)[-1]
    return path, base, os.path.splitext(base)[0]


def _find_lora_file(name, files):
    """Resolve a tag name to an entry of folder_paths.get_filename_list("loras")."""
    wanted_path, wanted_base, wanted_stem = _lora_keys(name)
    if not wanted_base:
        return None
    for file in files:
        file_path, file_base, file_stem = _lora_keys(file)
        if wanted_path == file_path or wanted_base in (file_base, file_stem):
            return file
    # The LoRA Tag Loader also accepts a prefix; prefer the closest (shortest) match.
    partial = []
    for file in files:
        file_path, file_base, file_stem = _lora_keys(file)
        if file_path.startswith(wanted_path) or file_base.startswith(wanted_base) or file_stem.startswith(wanted_stem):
            partial.append((len(file_stem), file_stem, file))
    partial.sort()
    return partial[0][2] if partial else None


class LoraTextLoader:
    """
    Loads the LoRAs written in `text` and outputs the prompt without the tags.

    Inputs MODEL and CLIP are passed through the matching LoRA loaders in the order the tags
    appear. A tag with no weight uses 1.0; a tag with one weight uses it for model and clip;
    a tag with two weights sets model and clip strength separately. Missing or invalid LoRAs
    are reported in the console and their tag is still removed from the prompt.

    Based on comfyui_lora_tag_loader (badjeff), with separate clip strength and without
    touching angle brackets that are not LoRA tags.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "text": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "text")
    FUNCTION = "load"
    CATEGORY = "MultiTabText"

    def load(self, model, clip, text):
        prompt, tags = parse_lora_tags(text)
        if not tags:
            return (model, clip, prompt)

        try:
            import folder_paths
            import comfy.sd
            import comfy.utils
        except Exception as exc:  # only possible outside ComfyUI
            print("[MultiTabText] WARNING: Lora Text Loader cannot load LoRAs here ({}). "
                  "The prompt is passed on unchanged apart from the removed tags.".format(exc), flush=True)
            return (model, clip, prompt)

        try:
            files = folder_paths.get_filename_list("loras")
        except Exception as exc:
            print("[MultiTabText] WARNING: Lora Text Loader could not read the LoRA list ({}).".format(exc), flush=True)
            files = []

        for tag in tags:
            if tag["error"]:
                print("[MultiTabText] WARNING: Lora Text Loader ignored {}: {}.".format(tag["raw"], tag["error"]),
                      flush=True)
                continue
            lora_name = _find_lora_file(tag["name"], files)
            if lora_name is None:
                print("[MultiTabText] WARNING: Lora Text Loader could not find LoRA {!r} in models/loras; "
                      "its tag was removed from the prompt.".format(tag["name"]), flush=True)
                continue
            try:
                lora_path = folder_paths.get_full_path("loras", lora_name)
            except Exception:
                lora_path = None
            if not lora_path or not os.path.isfile(lora_path):
                print("[MultiTabText] WARNING: Lora Text Loader found {!r} in the LoRA list but not on disk; "
                      "its tag was removed from the prompt.".format(lora_name), flush=True)
                continue
            try:
                lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
                model, clip = comfy.sd.load_lora_for_models(
                    model, clip, lora, tag["model_strength"], tag["clip_strength"])
            except Exception as exc:
                print("[MultiTabText] WARNING: Lora Text Loader could not load {!r}: {}.".format(lora_name, exc),
                      flush=True)
                continue
            print("[MultiTabText] Lora Text Loader: loaded {} as {!r} (model={}, clip={}) from {}".format(
                tag["raw"], lora_name, tag["model_strength"], tag["clip_strength"],
                os.path.abspath(lora_path)), flush=True)
        return (model, clip, prompt)


NODE_CLASS_MAPPINGS = {"RandomTextTabs": RandomTextTabs, "RemoveTags": RemoveTags, "LoraTextLoader": LoraTextLoader}
NODE_DISPLAY_NAME_MAPPINGS = {
    "RandomTextTabs": "MultiTabText",
    "RemoveTags": "MultiTabText Remove Tags",
    "LoraTextLoader": "Lora Text Loader",
}
