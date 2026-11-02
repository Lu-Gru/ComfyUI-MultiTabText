// Ctrl+/ (Cmd+/ on macOS) toggles comments in a textarea, like a code editor:
// with a selection the selected text becomes a /* ... */ block comment, without a selection
// the line under the caret is switched on and off with "//".
//
// The line syntax is the one MultiTabText understands: "//" or "#" at the start of a line.
// Line comments are always written as "// " after the indentation; uncommenting removes
// whichever prefix is there. Block comments are written around the selection as it is.

const LINE_IS_COMMENT = /^\s*(#|\/\/)/;
const LINE_COMMENT = /^(\s*)(#|\/\/) ?/;
const LINE_PREFIX = "// ";
const BLOCK_OPEN = "/*";
const BLOCK_CLOSE = "*/";

const lineStartAt = (text, pos) => text.lastIndexOf("\n", pos - 1) + 1;

// Column the caret keeps on its line after the prefix was added or removed.
function caretColumn(oldLine, col, uncommenting) {
  if (uncommenting) {
    const m = oldLine.match(LINE_COMMENT);
    if (!m) return col;
    const removed = m[0].length - m[1].length;
    if (col <= m[1].length) return col;
    return Math.max(m[1].length, col - removed);
  }
  const indent = oldLine.match(/^\s*/)[0].length;
  return col < indent ? col : col + LINE_PREFIX.length;
}

// Selection: wrap it in /* ... */. If it already is a block comment (or sits inside one),
// remove the markers instead, so the shortcut really toggles.
function toggleBlockComment(value, start, end) {
  const selected = value.slice(start, end);
  const wrapped = selected.match(/^\/\*([\s\S]*)\*\/$/);
  if (wrapped) {
    return {
      text: value.slice(0, start) + wrapped[1] + value.slice(end),
      selectionStart: start,
      selectionEnd: start + wrapped[1].length,
      first: start,
      lastEnd: end,
      replacement: wrapped[1],
    };
  }
  if (value.slice(0, start).endsWith(BLOCK_OPEN) && value.slice(end).startsWith(BLOCK_CLOSE)) {
    return {
      text: value.slice(0, start - BLOCK_OPEN.length) + selected + value.slice(end + BLOCK_CLOSE.length),
      selectionStart: start - BLOCK_OPEN.length,
      selectionEnd: end - BLOCK_OPEN.length,
      first: start - BLOCK_OPEN.length,
      lastEnd: end + BLOCK_CLOSE.length,
      replacement: selected,
    };
  }
  const replacement = BLOCK_OPEN + selected + BLOCK_CLOSE;
  return {
    text: value.slice(0, start) + replacement + value.slice(end),
    selectionStart: start + BLOCK_OPEN.length,
    selectionEnd: end + BLOCK_OPEN.length,
    first: start,
    lastEnd: end,
    replacement,
  };
}

// Pure part of the toggle: returns the new text and selection, or null when nothing would change.
// `start` / `end` are textarea selection offsets.
export function toggleCommentText(value, start, end) {
  if (typeof value !== "string") return null;
  start = Math.max(0, Math.min(start ?? 0, value.length));
  end = Math.max(start, Math.min(end ?? start, value.length));
  if (end > start) return toggleBlockComment(value, start, end);

  const first = lineStartAt(value, start);
  let lastEnd = value.indexOf("\n", start);
  if (lastEnd === -1) lastEnd = value.length;

  const block = value.slice(first, lastEnd);
  const lines = block.split("\n");
  const hasText = lines.some((line) => line.trim() !== "");
  const allCommented = hasText && lines.every((line) => line.trim() === "" || LINE_IS_COMMENT.test(line));

  const newLines = allCommented
    ? lines.map((line) => line.replace(LINE_COMMENT, "$1"))
    : lines.map((line) => line.replace(/^(\s*)/, `$1${LINE_PREFIX}`));
  const replacement = newLines.join("\n");
  if (replacement === block) return null;

  const col = start - first;
  const caret = first + caretColumn(lines[0], col, allCommented);

  return {
    text: value.slice(0, first) + replacement + value.slice(lastEnd),
    selectionStart: caret,
    selectionEnd: caret,
    first,
    lastEnd,
    replacement,
  };
}

// Apply the toggle to a textarea. Uses the browser's own insertText command when available so the
// change stays in the native undo history; otherwise it replaces the block directly.
export function toggleTextareaComment(textarea) {
  if (!textarea || typeof textarea.value !== "string" || textarea.readOnly || textarea.disabled) return false;
  const value = textarea.value;
  const result = toggleCommentText(value, textarea.selectionStart ?? 0, textarea.selectionEnd ?? 0);
  if (!result) return false;

  const doc = textarea.ownerDocument ?? (typeof document !== "undefined" ? document : null);
  textarea.focus();

  let done = false;
  let canInsert = false;
  try {
    canInsert = !!doc?.queryCommandSupported?.("insertText");
  } catch (e) {
    canInsert = false;
  }
  if (canInsert) {
    try {
      textarea.setSelectionRange(result.first, result.lastEnd);
      done = doc.execCommand("insertText", false, result.replacement);
    } catch (e) {
      done = false;
    }
  }

  if (!done || textarea.value !== result.text) {
    try {
      if (typeof textarea.setRangeText === "function") {
        textarea.setRangeText(result.replacement, result.first, result.lastEnd, "preserve");
      }
    } catch (e) {
      /* fall through to assigning the whole value */
    }
    if (textarea.value !== result.text) textarea.value = result.text;
    // Programmatic changes do not fire an input event; typing does, and the editors listen for it.
    const EventCtor = textarea.ownerDocument?.defaultView?.Event ?? (typeof Event !== "undefined" ? Event : null);
    if (EventCtor) textarea.dispatchEvent(new EventCtor("input", { bubbles: true }));
  }

  textarea.setSelectionRange(result.selectionStart, result.selectionEnd);
  return true;
}

function isCommentShortcut(e) {
  return !!e && (e.ctrlKey || e.metaKey) && !e.altKey && !e.isComposing && e.key === "/";
}

function onCommentKeyDown(e) {
  if (!isCommentShortcut(e)) return;
  const host = e.currentTarget;
  const target = e.target;
  if (target?.tagName !== "TEXTAREA") return;
  if (host !== target && !host?.contains?.(target)) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.repeat) return; // holding the keys must not toggle back and forth
  toggleTextareaComment(target);
}

// Add the shortcut to a textarea, or to a container that holds it (the listener survives the
// textarea being re-created inside the container).
export function installCommentToggle(element) {
  if (!element || typeof element.addEventListener !== "function" || element._mttCommentToggle) return false;
  element._mttCommentToggle = true;
  element.addEventListener("keydown", onCommentKeyDown, true);
  return true;
}

// Add the shortcut to one of ComfyUI's own text widgets. The DOM element may be created a little
// after the widget itself (and is replaced when the frontend switches render modes), so retry a
// few times before giving up.
export function installCommentToggleOnWidget(widget, tries = 20) {
  const attempt = (left) => {
    const el = widget?.element ?? widget?.inputEl;
    if (el && (el.tagName === "TEXTAREA" || el.querySelector?.("textarea"))) return installCommentToggle(el);
    if (left > 0 && typeof setTimeout === "function") setTimeout(() => attempt(left - 1), 100);
    return false;
  };
  return attempt(tries);
}
