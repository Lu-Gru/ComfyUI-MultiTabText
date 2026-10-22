import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

console.log("[MultiTabText] frontend extension loaded (build 2026-10-21.7)");

const NODE_NAME = "RandomTextTabs";
const DATA_WIDGET = "tabs_data";

const CSS = `
.rtt-outer{position:relative;width:100%;height:100%;min-height:300px;box-sizing:border-box;}
.rtt-root{position:absolute;inset:0;display:flex;flex-direction:column;box-sizing:border-box;gap:4px;
  font-family:system-ui,sans-serif;font-size:12px;color:var(--input-text,#ddd);}
.rtt-tabbar{display:flex;flex-wrap:nowrap;gap:2px;overflow-x:auto;flex:0 0 auto;padding-bottom:2px;}
.rtt-tab{display:flex;align-items:center;gap:6px;padding:3px 8px;border-radius:4px 4px 0 0;cursor:pointer;
  white-space:nowrap;user-select:none;background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);opacity:.75;}
.rtt-tab.active{opacity:1;background:var(--comfy-input-bg,#222);border-bottom-color:var(--comfy-input-bg,#222);font-weight:600;}
.rtt-tab.dragging{opacity:.4;}
.rtt-tab.over{outline:2px dashed #7aa2f7;}
.rtt-rename{font:inherit;color:inherit;background:var(--comfy-input-bg,#222);border:1px solid #7aa2f7;border-radius:3px;padding:0 3px;outline:none;min-width:50px;}
.rtt-chk{margin:0;cursor:pointer;}
.rtt-close{opacity:.6;padding:0 2px;border-radius:3px;}
.rtt-close:hover{opacity:1;background:rgba(255,80,80,.35);}
.rtt-add{padding:3px 9px;cursor:pointer;border-radius:4px;background:var(--comfy-menu-bg,#353535);
  border:1px solid var(--border-color,#555);user-select:none;}
.rtt-toolbar{display:flex;gap:4px;flex:0 0 auto;flex-wrap:wrap;}
.rtt-btn{padding:2px 8px;cursor:pointer;border-radius:4px;background:var(--comfy-menu-bg,#353535);
  color:inherit;border:1px solid var(--border-color,#555);font-size:11px;}
.rtt-btn:hover,.rtt-add:hover{filter:brightness(1.3);}
.rtt-btn.rtt-danger:hover{background:rgba(255,80,80,.35);filter:none;}
.rtt-opt{display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;user-select:none;padding:0 4px;}
.rtt-opt input{margin:0;cursor:pointer;}
.rtt-body{position:relative;flex:1 1 auto;min-height:200px;border:1px solid var(--border-color,#555);border-radius:4px;
  background:var(--comfy-input-bg,#222);overflow:hidden;}
.rtt-body.rtt-flash{border-color:#4299E0;box-shadow:0 0 0 1px #4299E0;}
.rtt-scroll{position:absolute;inset:0;overflow:auto;}
.rtt-stack{position:relative;min-height:100%;box-sizing:border-box;padding:6px 0;--gw:calc(1ch + 14px);
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:18px;}
.rtt-scroll:not(.rtt-wrap) .rtt-stack{width:max-content;min-width:100%;}
.rtt-stack::before{content:"";position:absolute;top:0;bottom:0;left:0;width:var(--gw);pointer-events:none;
  background:rgba(127,127,127,.12);border-right:1px solid var(--border-color,#555);}
.rtt-rows{counter-reset:ln;}
.rtt-row{position:relative;box-sizing:border-box;min-height:18px;counter-increment:ln;padding:0 8px 0 calc(var(--gw) + 6px);
  white-space:pre;color:var(--input-text,#ddd);}
.rtt-wrap .rtt-row{white-space:pre-wrap;overflow-wrap:break-word;}
.rtt-row::before{content:counter(ln);position:absolute;left:0;top:0;box-sizing:border-box;width:var(--gw);padding:0 6px;
  text-align:right;color:#8a8a8a;user-select:none;}
.rtt-grey .cm{color:#8a8a8a;}
.rtt-grey .rtt-row.c::before{opacity:.35;text-decoration:line-through;}
.rtt-text{position:absolute;top:6px;bottom:6px;left:var(--gw);right:0;margin:0;padding:0 8px 0 6px;border:none;outline:none;
  resize:none;overflow:hidden;background:transparent;color:transparent;caret-color:var(--input-text,#ddd);
  font:inherit;white-space:pre;}
.rtt-wrap .rtt-text{white-space:pre-wrap;overflow-wrap:break-word;}
.rtt-text::selection{background:rgba(66,153,224,.45);color:transparent;}
.rtt-text::placeholder{color:#777;}
`;

function injectCSS() {
  if (document.getElementById("rtt-style")) return;
  const s = document.createElement("style");
  s.id = "rtt-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

function defaultState() {
  return { active: 0, wrap: false, greyComments: true, removeEmptyLines: false, inFront: true, importAppend: false, tabs: [{ name: "Tab 1", text: "" }] };
}

function normalizeState(o) {
  if (!o || !Array.isArray(o.tabs) || !o.tabs.length) return defaultState();
  const tabs = o.tabs.map((t, i) => ({
    name: String(t?.name ?? `Tab ${i + 1}`),
    text: String(t?.text ?? ""),
    checked: !!t?.checked,
  }));
  const active = Math.min(Math.max(parseInt(o.active) || 0, 0), tabs.length - 1);
  return { active, wrap: !!o.wrap, greyComments: o.greyComments !== false, removeEmptyLines: o.removeEmptyLines === true, inFront: o.inFront !== false, importAppend: o.importAppend === true, tabs };
}

function parseState(value) {
  try {
    return normalizeState(JSON.parse(value));
  } catch (e) {
    return defaultState();
  }
}

const COMMENT_RE = /^\s*(#|\/\/)/;
const isCommentLine = (line) => {
  const c = line.charCodeAt(0);
  if (c > 32 && c < 127 && c !== 35 && c !== 47) return false; // first character is a visible char other than # and /
  return COMMENT_RE.test(line);
};

// Mirrors nodes.py strip_comments: "#" / "//" line comments and /* ... */ blocks (may span lines).
// Returns the line split into plain / comment parts, the block state after the line,
// and whether the line contains nothing but comment (such lines are removed from the output).
function analyzeLine(line, inBlock) {
  if (!inBlock) {
    if (isCommentLine(line)) return { parts: [{ t: line, c: true }], inBlock: false, whole: true };
    if (line.indexOf("/*") === -1) return { parts: [{ t: line, c: false }], inBlock: false, whole: false }; // most lines
  }
  const parts = [];
  let had = inBlock;
  let keep = "";
  let i = 0;
  while (i < line.length) {
    if (inBlock) {
      const end = line.indexOf("*/", i);
      if (end === -1) {
        parts.push({ t: line.slice(i), c: true });
        i = line.length;
      } else {
        parts.push({ t: line.slice(i, end + 2), c: true });
        inBlock = false;
        i = end + 2;
      }
    } else {
      const start = line.indexOf("/*", i);
      if (start === -1) {
        parts.push({ t: line.slice(i), c: false });
        keep += line.slice(i);
        i = line.length;
      } else {
        if (start > i) {
          parts.push({ t: line.slice(i, start), c: false });
          keep += line.slice(i, start);
        }
        parts.push({ t: "/*", c: true });
        had = true;
        inBlock = true;
        i = start + 2;
      }
    }
  }
  return { parts, inBlock, whole: had && !keep.trim() };
}

function download(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function pickFiles(accept, multiple) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.onchange = () => resolve(Array.from(input.files || []));
    input.click();
  });
}

// Other extensions (e.g. LoRA Manager) may try to write plain text into a node's first widget.
// Never let that overwrite the hidden JSON state.
function guardValue(widget, accept, hooks = {}) {
  let owner = widget;
  let desc = null;
  while (owner && !(desc = Object.getOwnPropertyDescriptor(owner, "value"))) owner = Object.getPrototypeOf(owner);
  if (!desc) return;
  const pull = hooks.refresh; // returns a fresh value string when the stored one is stale, else null
  if (desc.get || desc.set) {
    Object.defineProperty(widget, "value", {
      configurable: true,
      enumerable: true,
      get() {
        const fresh = pull?.();
        if (fresh != null) desc.set?.call(this, fresh);
        return desc.get.call(this);
      },
      set(v) {
        if (!accept(v)) return;
        hooks.onSet?.();
        desc.set.call(this, v);
      },
    });
  } else {
    let raw = desc.value;
    Object.defineProperty(widget, "value", {
      configurable: true,
      enumerable: true,
      get() {
        const fresh = pull?.();
        if (fresh != null) raw = fresh;
        return raw;
      },
      set(v) {
        if (!accept(v)) return;
        hooks.onSet?.();
        raw = v;
      },
    });
  }
}

const isStateString = (v) => {
  try {
    return typeof v === "string" && Array.isArray(JSON.parse(v)?.tabs);
  } catch (e) {
    return false;
  }
};

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
const NEEDS_ESC = /[&<>]/;
const escHtml = (s) => (NEEDS_ESC.test(s) ? s.replace(/[&<>]/g, (c) => ESC_MAP[c]) : s);

function rowHtml(line, inBlock) {
  // Fast path for the common case: an ordinary line outside any block comment (no objects allocated).
  if (!inBlock && line.indexOf("/*") === -1 && !isCommentLine(line)) {
    return { html: `<div class="rtt-row">${escHtml(line)}</div>`, inBlock: false };
  }
  const r = analyzeLine(line, inBlock);
  const text =
    r.parts.length === 1 && !r.parts[0].c
      ? escHtml(r.parts[0].t)
      : r.parts.map((p) => (p.c ? `<span class="cm">${escHtml(p.t)}</span>` : escHtml(p.t))).join("");
  return {
    html: `<div class="rtt-row${r.whole ? " c" : ""}">${text}</div>`,
    inBlock: r.inBlock,
  };
}

// Work out the smallest range of rows that must be rebuilt after the text changed.
// oldStates[i] = "inside a /* block */" before line i (length = oldLines.length + 1).
// Returns null when nothing changed, else { start, oldEnd, html, states }: replace old rows
// [start, oldEnd) with `html`. Rows after the edit are reused as long as the block-comment state matches.
function planRows(oldLines, oldStates, lines) {
  const n = lines.length;
  const m = oldLines.length;
  const min = Math.min(n, m);
  let p = 0;
  while (p < min && lines[p] === oldLines[p]) p++;
  if (p === n && n === m) return null;
  let s = 0;
  while (s < min - p && lines[n - 1 - s] === oldLines[m - 1 - s]) s++;
  const delta = m - n;
  const end = n - s;
  const states = oldStates.slice(0, p + 1);
  let inBlock = states[p];
  let html = "";
  let k = p;
  for (; k < n; k++) {
    if (k >= end && inBlock === oldStates[k + delta]) break; // rest of the rows is unchanged
    const r = rowHtml(lines[k], inBlock);
    html += r.html;
    inBlock = r.inBlock;
    states.push(inBlock);
  }
  if (k < n) for (let q = k + delta + 1; q <= m; q++) states.push(oldStates[q]);
  return { start: p, oldEnd: k + delta, html, states };
}

// Hide a native widget; it is still saved with the workflow and sent to Python.
function hideWidget(w) {
  if (!w) return;
  w.type = "hidden";
  w.hidden = true;
  if (w.options) w.options.hidden = true;
  w.computeSize = () => [0, -4];
  if (w.element) w.element.style.display = "none";
}

function createEditor(node, dataWidget) {
  let state = parseState(dataWidget.value);
  // The hidden widget's JSON is only produced when somebody reads it (queueing / saving the workflow),
  // not on every keystroke.
  let dirty = false;
  guardValue(dataWidget, isStateString, {
    refresh: () => {
      if (!dirty) return null;
      dirty = false;
      return JSON.stringify(state);
    },
    onSet: () => {
      dirty = false; // a value loaded from a workflow replaces our pending state
    },
  });
  let dragFrom = -1;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  const root = el("div", "rtt-root");
  const tabbar = el("div", "rtt-tabbar");
  const toolbar = el("div", "rtt-toolbar");
  const body = el("div", "rtt-body");
  // One scroll container holds: numbered rows (colored text, wraps like the textarea) + a transparent textarea on top.
  const scroll = el("div", "rtt-scroll");
  const stack = el("div", "rtt-stack");
  const rowsEl = el("div", "rtt-rows");
  const ta = el("textarea", "rtt-text");
  ta.wrap = "off";
  ta.spellcheck = false;
  ta.placeholder = "Type text here.\n# or // = comment line\n/* block comment */\n{red|green|} flower"
    + "\n%model% = model name; %model_type% outputs the model type (sdxl, pony, flux, anima, moss, ...)"
    + "\n<lora:detail:0.8> = LoRA; <lora:style:0.6:0.4> = model:clip strength (Lora Text Loader)"
    + "\n\n@if %model_type% == flux\nhighly detailed photo\n"
    + "@elseif %model_type% == pony || %model_type% == sdxl || %model_type% == illustrious\n"
    + "masterpiece, best quality\n@else\na photo\n@endif";
  stack.append(rowsEl, ta);
  scroll.append(stack);
  body.append(scroll);
  root.append(tabbar, toolbar, body);
  const outer = el("div", "rtt-outer");
  outer.append(root);

  // ---------- state helpers ----------
  // Tell ComfyUI the workflow changed so its autosave / change tracker stores it.
  const notifyNow = () => {
    try {
      const wf = app.extensionManager?.workflow?.activeWorkflow ?? app.workflowManager?.activeWorkflow;
      wf?.changeTracker?.checkState?.();
    } catch (e) {
      /* ignore */
    }
  };
  let notifyTimer = null;
  const save = () => {
    dirty = true; // JSON is built lazily by the widget's getter
    clearTimeout(notifyTimer);
    notifyTimer = setTimeout(notifyNow, 400);
  };
  // Flush immediately when the page is hidden/closed so a refresh never loses text.
  const flush = () => {
    if (!node.graph) return;
    clearTimeout(notifyTimer);
    notifyNow();
  };
  const onVisibility = () => {
    if (document.visibilityState === "hidden") flush();
  };
  window.addEventListener("beforeunload", flush);
  window.addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", onVisibility);

  const uniqueName = () => {
    const used = new Set(state.tabs.map((t) => t.name));
    let n = 1;
    while (used.has(`Tab ${n}`)) n++;
    return `Tab ${n}`;
  };

  // ---------- line numbers + colored text (only changed rows are rebuilt) ----------
  let rowLines = [];
  let rowStates = [false];
  let gwDigits = 0;
  const tpl = document.createElement("template");
  const renderRows = () => {
    const lines = ta.value.split("\n");
    const plan = planRows(rowLines, rowStates, lines);
    if (!plan) return;
    const oldCount = rowLines.length;
    if (plan.start === 0 && plan.oldEnd >= oldCount) {
      rowsEl.innerHTML = plan.html; // everything changed: cheaper than patching
    } else {
      const kids = rowsEl.children;
      const ref = plan.oldEnd < oldCount ? kids[plan.oldEnd] : null;
      if (plan.oldEnd > plan.start) {
        const range = document.createRange();
        range.setStartBefore(kids[plan.start]);
        range.setEndAfter(kids[plan.oldEnd - 1]);
        range.deleteContents();
      }
      if (plan.html) {
        tpl.innerHTML = plan.html;
        rowsEl.insertBefore(tpl.content, ref);
      }
    }
    rowLines = lines;
    rowStates = plan.states;
    const digits = String(lines.length).length;
    if (digits !== gwDigits) {
      gwDigits = digits;
      stack.style.setProperty("--gw", `calc(${digits}ch + 14px)`);
    }
  };

  // Keep the caret line inside the visible area (vertical only). Batched to one layout read per frame.
  let caretRaf = 0;
  const ensureCaretVisible = () => {
    if (caretRaf) return;
    caretRaf = requestAnimationFrame(() => {
      caretRaf = 0;
      const v = ta.value;
      const caret = ta.selectionStart;
      let line = 0;
      for (let i = v.indexOf("\n"); i !== -1 && i < caret; i = v.indexOf("\n", i + 1)) line++;
      const row = rowsEl.children[line];
      if (!row) return;
      const top = row.offsetTop;
      const bottom = top + row.offsetHeight;
      if (top - 6 < scroll.scrollTop) scroll.scrollTop = Math.max(0, top - 6);
      else if (bottom + 6 > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = bottom + 6 - scroll.clientHeight;
    });
  };

  // Apply "wrap" / "grey comments" options to the view.
  let wrapCb = null;
  let greyCb = null;
  let emptyCb = null;
  let frontCb = null;
  let appendCb = null;
  const applyView = () => {
    scroll.classList.toggle("rtt-wrap", !!state.wrap);
    scroll.classList.toggle("rtt-grey", !!state.greyComments);
    ta.wrap = state.wrap ? "soft" : "off";
    if (wrapCb) wrapCb.checked = !!state.wrap;
    if (greyCb) greyCb.checked = !!state.greyComments;
    if (emptyCb) emptyCb.checked = !!state.removeEmptyLines;
    if (frontCb) frontCb.checked = state.inFront !== false;
    if (appendCb) appendCb.checked = state.importAppend === true;
  };

  const loadActive = () => {
    ta.value = state.tabs[state.active].text;
    renderRows();
  };

  // ---------- tabs ----------
  const selectTab = (i) => {
    if (i === state.active) return;
    state.active = i;
    save();
    Array.from(tabbar.querySelectorAll(".rtt-tab")).forEach((n, idx) =>
      n.classList.toggle("active", idx === i)
    );
    loadActive();
  };

  const addTab = (name = null, text = "") => {
    state.tabs.push({ name: name || uniqueName(), text, checked: false });
    state.active = state.tabs.length - 1;
    save();
    renderTabs();
    loadActive();
    ta.focus();
  };

  const removeTab = (i) => {
    const tab = state.tabs[i];
    if (tab.text.trim() && !confirm(`Remove "${tab.name}" and its text?`)) return;
    if (state.tabs.length === 1) {
      state.tabs[0] = { name: "Tab 1", text: "" }; // always keep one tab
      state.active = 0;
    } else {
      state.tabs.splice(i, 1);
      if (state.active > i || state.active >= state.tabs.length) state.active = Math.max(0, state.active - 1);
    }
    save();
    renderTabs();
    loadActive();
  };

  // "Tab 1 copy", "Tab 1 copy 2", ... (a name that is not used yet)
  const copyName = (name) => {
    const used = new Set(state.tabs.map((t) => t.name));
    const base = name.replace(/ copy( \d+)?$/, "") + " copy"; // a copy of a copy stays "X copy 2", not "X copy copy"
    let candidate = base;
    for (let n = 2; used.has(candidate); n++) candidate = `${base} ${n}`;
    return candidate;
  };

  // Copy the active tab (name, text) right behind it and switch to the copy. The copy is not ticked, so the output of
  // the node does not double by accident.
  const duplicateTab = () => {
    const source = state.tabs[state.active];
    state.tabs.splice(state.active + 1, 0, { name: copyName(source.name), text: source.text, checked: false });
    state.active += 1;
    save();
    renderTabs();
    loadActive();
    ta.focus();
  };

  const moveTab = (from, to) => {
    const activeTab = state.tabs[state.active];
    const [t] = state.tabs.splice(from, 1);
    state.tabs.splice(to, 0, t);
    state.active = state.tabs.indexOf(activeTab);
    save();
    renderTabs();
  };

  const startRename = (i, tabEl, nameSpan) => {
    const input = el("input", "rtt-rename");
    input.type = "text";
    input.value = state.tabs[i].name;
    input.size = Math.max(6, input.value.length + 1);
    tabEl.draggable = false;
    nameSpan.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    const finish = (commit) => {
      if (done) return;
      done = true;
      const v = input.value.trim();
      if (commit && v) {
        state.tabs[i].name = v;
        save();
      }
      renderTabs();
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation(); // keep ComfyUI shortcuts away from the text box
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });
    input.addEventListener("keyup", (e) => e.stopPropagation());
    input.addEventListener("blur", () => finish(true));
    ["click", "dblclick", "mousedown", "pointerdown"].forEach((ev) =>
      input.addEventListener(ev, (e) => e.stopPropagation())
    );
  };

  const renderTabs = () => {
    tabbar.innerHTML = "";
    state.tabs.forEach((tab, i) => {
      const t = el("div", "rtt-tab" + (i === state.active ? " active" : ""));
      t.title = "Click: select | Double-click: rename (Enter to confirm, Esc to cancel) | Drag: reorder | Checkbox: include in output";
      t.draggable = true;
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.className = "rtt-chk";
      chk.checked = !!tab.checked;
      chk.title = "Include this tab in the output. All checked tabs are output in tab order; if none is checked, the active tab is output.";
      ["click", "dblclick", "mousedown"].forEach((ev) => chk.addEventListener(ev, (e) => e.stopPropagation()));
      chk.addEventListener("change", () => {
        tab.checked = chk.checked;
        save();
      });
      const nameSpan = el("span", "rtt-name", tab.name);
      t.append(chk, nameSpan);
      const x = el("span", "rtt-close", "\u00d7");
      x.title = "Remove tab";
      x.onclick = (e) => {
        e.stopPropagation();
        removeTab(i);
      };
      t.append(x);

      t.onclick = () => selectTab(i);
      t.ondblclick = (e) => {
        e.preventDefault();
        startRename(i, t, nameSpan);
      };
      t.addEventListener("dragstart", (e) => {
        dragFrom = i;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(i));
        t.classList.add("dragging");
      });
      t.addEventListener("dragend", () => {
        dragFrom = -1;
        t.classList.remove("dragging");
        tabbar.querySelectorAll(".over").forEach((n) => n.classList.remove("over"));
      });
      t.addEventListener("dragover", (e) => {
        if (dragFrom < 0) return;
        e.preventDefault();
        t.classList.add("over");
      });
      t.addEventListener("dragleave", () => t.classList.remove("over"));
      t.addEventListener("drop", (e) => {
        if (dragFrom < 0) return;
        e.preventDefault();
        e.stopPropagation();
        const from = dragFrom;
        dragFrom = -1;
        if (from !== i) moveTab(from, i);
      });
      tabbar.append(t);
    });
    const add = el("div", "rtt-add", "+");
    add.title = "New tab";
    add.onclick = () => addTab();
    tabbar.append(add);
  };

  // ---------- import / export ----------
  const safeName = (s) => s.replace(/[^\w\- ]+/g, "_").trim() || "tab";

  const btn = (label, title, fn, extraClass = "") => {
    const b = el("button", "rtt-btn" + (extraClass ? " " + extraClass : ""), label);
    b.type = "button";
    b.title = title;
    b.onclick = fn;
    toolbar.append(b);
    return b;
  };

  btn("Duplicate tab", "Copy the active tab (name and text) right after it", duplicateTab);

  btn("Export tab", "Save the current tab as a .txt file", () => {
    const tab = state.tabs[state.active];
    download(`${safeName(tab.name)}.txt`, tab.text, "text/plain");
  });

  btn("Import tab(s)", "Load one or more .txt files, each into a new tab", async () => {
    const files = await pickFiles(".txt,.text,.md,text/plain", true);
    for (const f of files) {
      const text = await f.text();
      state.tabs.push({ name: f.name.replace(/\.[^.]+$/, "") || uniqueName(), text });
      state.active = state.tabs.length - 1;
    }
    if (files.length) {
      save();
      renderTabs();
      loadActive();
    }
  });

  btn("Export all", "Save all tabs as one .json file", () => {
    download("MultiTabText.json", JSON.stringify(state, null, 2), "application/json");
  });

  btn("Import all", "Import tabs from an exported .json file: replaces all tabs, or is added after them if 'Append on import' is ticked", async () => {
    const [f] = await pickFiles(".json,application/json", false);
    if (!f) return;
    let data;
    try {
      data = JSON.parse(await f.text());
      if (!Array.isArray(data?.tabs) || !data.tabs.length) throw new Error("no tabs");
    } catch (e) {
      alert("Invalid file: expected a JSON file exported with 'Export all'.");
      return;
    }
    if (state.importAppend) {
      // Add the imported tabs after the current ones; the current toolbar options stay as they are.
      const firstNew = state.tabs.length;
      state.tabs.push(...normalizeState(data).tabs);
      state.active = firstNew;
    } else {
      if (!confirm("Replace ALL current tabs with the imported ones?")) return;
      state = normalizeState({ ...data, importAppend: false }); // the append setting itself is not taken from the file
    }
    applyView();
    save();
    renderTabs();
    loadActive();
  });

  btn(
    "Clear all",
    "Close all tabs and clear all text (keeps the toolbar options)",
    () => {
      const hasContent = state.tabs.length > 1 || state.tabs.some((t) => t.text.trim());
      if (hasContent && !confirm("Close all tabs and clear all text? This cannot be undone.")) return;
      state.tabs = [{ name: "Tab 1", text: "", checked: false }];
      state.active = 0;
      save();
      renderTabs();
      loadActive();
    },
    "rtt-danger"
  );

  const option = (label, title, key) => {
    const l = el("label", "rtt-opt");
    l.title = title;
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.onchange = () => {
      state[key] = cb.checked;
      save();
      applyView();
    };
    l.append(cb, document.createTextNode(label));
    toolbar.append(l);
    return cb;
  };
  wrapCb = option("Wrap text", "Wrap long lines instead of scrolling sideways", "wrap");
  greyCb = option("Grey comments", "Show comments (# or // lines, /* ... */ blocks) in grey", "greyComments");
  frontCb = option(
    "In front",
    "Connected texts: checked = placed before this node's text, unchecked = placed after it",
    "inFront"
  );
  appendCb = option(
    "Append on import",
    "Import all: checked = add the imported tabs after the current ones, unchecked = replace all current tabs",
    "importAppend"
  );
  emptyCb = option("Remove empty lines", "Remove empty (or whitespace-only) lines from the output", "removeEmptyLines");
  applyView();

  // ---------- textarea events ----------
  ta.addEventListener("input", () => {
    state.tabs[state.active].text = ta.value;
    renderRows();
    ensureCaretVisible();
    save();
  });
  ta.addEventListener("keyup", (e) => {
    if (e.key.startsWith("Arrow") || e.key === "PageUp" || e.key === "PageDown") ensureCaretVisible();
  });
  ta.addEventListener("blur", flush);
  // The textarea never scrolls itself (the outer container does); undo any drift so text stays aligned with the rows.
  ta.addEventListener("scroll", () => {
    if (ta.scrollLeft || ta.scrollTop) {
      ta.scrollLeft = 0;
      ta.scrollTop = 0;
    }
  });
  // Clicking the line-number column should still focus the editor.
  stack.addEventListener("mousedown", (e) => {
    if (e.target !== ta && !e.target.closest?.(".rtt-rename")) {
      e.preventDefault();
      ta.focus();
    }
  });
  scroll.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });

  // ---------- text sent from outside (LoRA Manager) goes into the ACTIVE tab ----------
  let flashTimer = null;
  const flashBody = () => {
    body.classList.add("rtt-flash");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => body.classList.remove("rtt-flash"), 1500);
  };
  const setText = (text) => {
    state.tabs[state.active].text = String(text ?? "");
    ta.value = state.tabs[state.active].text;
    renderRows();
    save();
    flashBody();
  };
  const appendText = (text) => {
    const add = String(text ?? "");
    if (!add.trim()) return;
    const cur = state.tabs[state.active].text;
    setText(cur && !cur.endsWith("\n") ? cur + "\n" + add : cur + add);
    scroll.scrollTop = scroll.scrollHeight;
  };

  // ---------- init ----------
  renderTabs();
  loadActive();
  save();

  return {
    root: outer,
    getText: () => state.tabs[state.active].text,
    applyInjected: (text, mode) => (mode === "replace" ? setText(text) : appendText(text)),
    dispose() {
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    },
    reload() {
      state = parseState(dataWidget.value);
      applyView();
      renderTabs();
      loadActive();
    },
  };
}


// ---------------------------------------------------------------------------
// RemoveTags: dynamic text inputs (text, text_2, text_3, ...)
// ---------------------------------------------------------------------------
const TEXT_INPUT_RE = /^text(?:_\d+)?$/;

function syncTextInputs(node) {
  if (!node.inputs || node._rttSyncing) return;
  if (app.configuringGraph) {
    // Workflow still loading: links are not restored yet, try again shortly.
    setTimeout(() => syncTextInputs(node), 50);
    return;
  }
  node._rttSyncing = true;
  try {
    const textIdx = () =>
      node.inputs.map((inp, i) => (TEXT_INPUT_RE.test(inp.name) ? i : -1)).filter((i) => i >= 0);

    // 1. remove unconnected dynamic inputs (the first "text" input always stays)
    let idxs = textIdx();
    for (let k = idxs.length - 1; k >= 1; k--) {
      if (node.inputs[idxs[k]].link == null) node.removeInput(idxs[k]);
    }

    // 2. number the remaining ones in order: text, text_2, text_3, ...
    idxs = textIdx();
    idxs.forEach((ix, k) => {
      const inp = node.inputs[ix];
      const name = k === 0 ? "text" : `text_${k + 1}`;
      inp.name = name;
      if (k > 0) inp.localized_name = name;
    });

    // 3. keep exactly one free socket at the end
    const last = node.inputs[idxs[idxs.length - 1]];
    if (last.link != null) node.addInput(`text_${idxs.length + 1}`, "STRING");

    const min = node.computeSize();
    node.setSize([Math.max(node.size[0], min[0]), Math.max(node.size[1], min[1])]);
    node.setDirtyCanvas?.(true, true);
  } catch (e) {
    console.error("[MultiTabText] dynamic inputs failed:", e);
  } finally {
    node._rttSyncing = false;
  }
}

function setupDynamicTextInputs(nodeType) {
  const scheduleSync = (node) => {
    clearTimeout(node._rttTimer);
    node._rttTimer = setTimeout(() => syncTextInputs(node), 0);
  };

  const onConnectionsChange = nodeType.prototype.onConnectionsChange;
  nodeType.prototype.onConnectionsChange = function (type) {
    const r = onConnectionsChange?.apply(this, arguments);
    if (type === (globalThis.LiteGraph?.INPUT ?? 1)) scheduleSync(this);
    return r;
  };

  const onConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    const r = onConfigure?.apply(this, arguments);
    scheduleSync(this);
    return r;
  };
}

// ---------------------------------------------------------------------------
// MultiTabText: when the node is BYPASSED it outputs an empty text
// (ComfyUI normally drops bypassed nodes, which would also drop their connections).
// ---------------------------------------------------------------------------
const MODE_NEVER = 2; // muted
const MODE_BYPASS = 4;
const MAIN_NODE_TYPE = "RandomTextTabs";
const EMPTY_STATE = JSON.stringify({ active: 0, tabs: [{ name: "bypassed", text: "" }] });

const getLink = (graph, id) => (id == null ? null : graph.links?.get?.(id) ?? graph.links?.[id] ?? null);

const isMainNode = (n) => n?.comfyClass === MAIN_NODE_TYPE;
const hasTextLinks = (n) => (n.inputs ?? []).some((i) => TEXT_INPUT_RE.test(i.name) && i.link != null);
// A MUTED or BYPASSED MultiTabText that has connected texts still outputs them (concatenated); muting / bypassing
// just switches off its own tabs. Without connected texts, a bypassed one outputs an empty text.
const isTagsNode = (n) => n?.type === "RemoveTags";
// A BYPASSED Remove Tags node passes its connected texts through unchanged (no tags removed or replaced), joined with
// its own `separator` setting; with nothing connected it passes an empty text. ComfyUI alone would pass only the first one.
const isPassThroughTags = (n) => isTagsNode(n) && n.mode === MODE_BYPASS;
const isUsableOffMain = (n) => isMainNode(n) && (n.mode === MODE_NEVER || n.mode === MODE_BYPASS) && hasTextLinks(n);

// Follow an input link through bypassed pass-through nodes; return the MultiTabText node feeding it that needs
// special handling (a bypassed one, or a muted one with connected texts), if any.
function findBypassedSource(graph, node, slot) {
  let link = getLink(graph, node.inputs?.[slot]?.link);
  for (let guard = 0; link && guard < 100; guard++) {
    const origin = graph.getNodeById(link.origin_id);
    if (!origin) return null;
    if (isMainNode(origin) && (origin.mode === MODE_BYPASS || isUsableOffMain(origin))) return origin;
    if (isPassThroughTags(origin)) return origin;
    if (origin.mode !== MODE_BYPASS) return null; // normal node: already handled by ComfyUI
    const idx = origin.inputs?.findIndex(
      (i) => i.link != null && (i.type === link.type || i.type === "*" || link.type === "*")
    );
    if (idx == null || idx < 0) return null;
    link = getLink(graph, origin.inputs[idx].link);
  }
  return null;
}

// Prompt reference ([nodeId, slot]) for whatever feeds a text input of a muted / bypassed MultiTabText.
// Bypassed / muted sources are ignored; Reroute-like nodes are looked through; such a MultiTabText is built on demand.
function resolveTextSource(graph, out, linkId, ensure) {
  let link = getLink(graph, linkId);
  for (let guard = 0; link && guard < 100; guard++) {
    const origin = graph.getNodeById(link.origin_id);
    if (!origin) return null;
    if (isUsableOffMain(origin) || isPassThroughTags(origin)) {
      const sid = ensure(origin);
      return sid ? [sid, 0] : null;
    }
    if (origin.mode === MODE_BYPASS || origin.mode === MODE_NEVER) return null;
    if (out[String(origin.id)]) return [String(origin.id), link.origin_slot ?? 0];
    if (origin.isVirtualNode && origin.inputs?.[0]?.link != null) {
      link = getLink(graph, origin.inputs[0].link);
      continue;
    }
    return null;
  }
  return null;
}

// Add a muted / bypassed MultiTabText (with connected texts) to the prompt: empty tabs + its text inputs, so it outputs just the texts.
function ensureOffMain(graph, out, node, building = new Set()) {
  const id = String(node.id);
  if (out[id]) return id;
  if (building.has(id)) return null; // cycle guard
  building.add(id);
  const own = parseState(node.widgets?.find((w) => w.name === DATA_WIDGET)?.value);
  const inputs = {
    tabs_data: JSON.stringify({
      active: 0,
      removeEmptyLines: own.removeEmptyLines,
      inFront: own.inFront,
      tabs: [{ name: "off", text: "", checked: false }],
    }),
  };
  for (const inp of node.inputs ?? []) {
    if (!TEXT_INPUT_RE.test(inp.name) || inp.link == null) continue;
    const ref = resolveTextSource(graph, out, inp.link, (n) => ensureSpecial(graph, out, n, building));
    if (ref) inputs[inp.name] = ref;
  }
  out[id] = { class_type: MAIN_NODE_TYPE, inputs, _meta: { title: node.title || "MultiTabText (inputs only)" } };
  building.delete(id);
  return id;
}

// Add a bypassed Remove Tags to the prompt as a plain pass-through: no tags, no cleanup, its own separator.
function ensureTagsPass(graph, out, node, building = new Set()) {
  const id = String(node.id);
  if (out[id]) return id;
  if (building.has(id)) return null; // cycle guard
  building.add(id);
  const inputs = {
    tags: "",
    case_sensitive: false,
    whole_words_only: true,
    clean_separators: false,
    remove_empty_lines: false,
    separator: node.widgets?.find((w) => w.name === "separator")?.value ?? "comma",
  };
  for (const inp of node.inputs ?? []) {
    if (!TEXT_INPUT_RE.test(inp.name) || inp.link == null) continue;
    const ref = resolveTextSource(graph, out, inp.link, (n) => ensureSpecial(graph, out, n, building));
    if (ref) inputs[inp.name] = ref;
  }
  if (inputs.text === undefined) inputs.text = ""; // required input: an empty text when nothing is connected / usable
  out[id] = { class_type: "RemoveTags", inputs, _meta: { title: node.title || "Remove Tags (bypassed)" } };
  building.delete(id);
  return id;
}

// Build whichever special node this is (MultiTabText with connected texts, or a bypassed Remove Tags).
function ensureSpecial(graph, out, node, building) {
  return isTagsNode(node) ? ensureTagsPass(graph, out, node, building) : ensureOffMain(graph, out, node, building);
}

function injectBypassedMainNodes(result, graph) {
  const out = result?.output;
  if (!out || !graph) return result;
  const all = graph._nodes ?? graph.nodes ?? [];
  if (!all.some((n) => isPassThroughTags(n) || (isMainNode(n) && (n.mode === MODE_BYPASS || isUsableOffMain(n))))) return result; // nothing to do
  for (const node of all) {
    if (node.mode === MODE_NEVER || node.mode === MODE_BYPASS) continue;
    const entry = out[String(node.id)];
    if (!entry) continue;
    const isTextTarget = node.type === "RemoveTags" || isMainNode(node);
    (node.inputs ?? []).forEach((inp, slot) => {
      if (inp.link == null) return;
      const src = findBypassedSource(graph, node, slot);
      if (!src) return;
      let sid;
      if (isPassThroughTags(src)) {
        // bypassed Remove Tags: its connected texts, unchanged (also for text inputs of Remove Tags / MultiTabText)
        sid = ensureTagsPass(graph, out, src);
        if (!sid) return;
      } else if (isUsableOffMain(src)) {
        // muted / bypassed MultiTabText with connected texts: outputs those texts
        sid = ensureOffMain(graph, out, src);
        if (!sid) return;
      } else {
        // bypassed with nothing connected: an empty text. Text inputs of RemoveTags / MultiTabText simply ignore it
        // (see applyRemoveTagsRules).
        if (isTextTarget && TEXT_INPUT_RE.test(inp.name)) return;
        sid = String(src.id);
        if (!out[sid]) {
          out[sid] = {
            class_type: MAIN_NODE_TYPE,
            inputs: { tabs_data: EMPTY_STATE },
            _meta: { title: src.title || "MultiTabText (bypassed)" },
          };
        }
      }
      // Override even when ComfyUI already forwarded the bypassed node's own input (MultiTabText has inputs).
      entry.inputs[inp.name] = [sid, 0];
    });
  }
  return result;
}

// RemoveTags / MultiTabText: text inputs fed by a bypassed (or muted) node are ignored, except when that node still has
// text to give (a muted / bypassed MultiTabText with connected texts, a bypassed Remove Tags).
// If nothing usable is left, the node still runs and outputs an empty string.
function applyRemoveTagsRules(result, graph) {
  const out = result?.output;
  if (!out || !graph) return result;
  for (const node of graph._nodes ?? graph.nodes ?? []) {
    const isTags = node.type === "RemoveTags";
    if ((!isTags && node.comfyClass !== MAIN_NODE_TYPE) || node.mode === MODE_NEVER || node.mode === MODE_BYPASS) continue;
    const entry = out[String(node.id)];
    if (!entry) continue;
    entry.inputs = entry.inputs ?? {};
    for (const inp of node.inputs ?? []) {
      if (!TEXT_INPUT_RE.test(inp.name) || inp.link == null) continue;
      const origin = graph.getNodeById(getLink(graph, inp.link)?.origin_id);
      // A muted / bypassed MultiTabText that still outputs its connected texts is a usable source.
      if (origin && (origin.mode === MODE_BYPASS || origin.mode === MODE_NEVER) && !isUsableOffMain(origin) && !isPassThroughTags(origin)) {
        delete entry.inputs[inp.name];
      }
    }
    if (isTags && entry.inputs.text === undefined) entry.inputs.text = ""; // required input: supply an empty text
  }
  return result;
}

// ---------------------------------------------------------------------------
// ComfyUI-Lora-Manager bridge: lets "Send to workflow" target MultiTabText and
// writes the received prompt / LoRA syntax into the ACTIVE tab.
// ---------------------------------------------------------------------------
const REGISTER_AS_LORA_TARGET = true; // also accept <lora:...> syntax sent from model cards
const ROOT_GRAPH_ID = "root";
const graphIdOf = (g) => g?.id ?? ROOT_GRAPH_ID;

function childGraphs(graph) {
  const sub = graph?._subgraphs;
  if (!sub) return [];
  const arr = typeof sub.values === "function" ? Array.from(sub.values()) : Object.values(sub);
  return arr.map((s) => s?.graph || s?._graph || s).filter((s) => s && s !== graph);
}

function allGraphNodes(root = app.graph) {
  const out = [];
  const seen = new Set();
  const visit = (g) => {
    if (!g) return;
    const id = graphIdOf(g);
    if (seen.has(id)) return;
    seen.add(id);
    (g._nodes ?? []).forEach((node) => out.push({ graph: g, node }));
    childGraphs(g).forEach(visit);
  };
  visit(root);
  return out;
}

function findNode(graphId, nodeId) {
  const num = typeof nodeId === "string" ? Number(nodeId) : nodeId;
  const key = Number.isNaN(num) ? nodeId : num;
  if (!graphId) return app.graph?.getNodeById?.(key) ?? null;
  for (const { graph } of allGraphNodes()) {
    if (graphIdOf(graph) === graphId) return graph.getNodeById?.(key) ?? null;
  }
  return app.graph?.getNodeById?.(key) ?? null;
}

// Entries in the format LoRA Manager's workflow registry uses (POST /api/lm/register-nodes).
function ourRegistryEntries() {
  return allGraphNodes()
    .filter(({ node }) => node?.comfyClass === MAIN_NODE_TYPE)
    .map(({ graph, node }) => ({
      node_id: node.id,
      graph_id: graphIdOf(graph),
      graph_name: typeof graph?.name === "string" && graph.name.trim() ? graph.name : null,
      bgcolor: node.bgcolor ?? node.color ?? null,
      title: node.title || MAIN_NODE_TYPE,
      type: MAIN_NODE_TYPE,
      comfy_class: MAIN_NODE_TYPE,
      mode: node.mode,
      marker_role: node.properties?.lm_marker_role ?? null,
      capabilities: {
        supports_lora: REGISTER_AS_LORA_TARGET,
        has_text_widget: true,
        widget_names: (node.widgets ?? []).map((w) => w?.name).filter(Boolean),
      },
    }));
}

function deliverToMainNode(msg, text, mode) {
  const id = msg?.node_id ?? msg?.id;
  if (id == null || Number(id) === -1) return; // broadcast is for LoRA nodes only
  const node = findNode(msg.graph_id, id);
  if (!node || node.comfyClass !== MAIN_NODE_TYPE || !node._rtt) return;
  node._rtt.applyInjected(text, mode);
}

// Ask LoRA Manager to re-send its node list (it listens for this event and forces a re-registration).
let registryTimer = null;
function scheduleRegistryRefresh() {
  clearTimeout(registryTimer);
  registryTimer = setTimeout(() => {
    try {
      api.dispatchEvent(new CustomEvent("lora_registry_refresh"));
    } catch (e) {
      /* LoRA Manager not installed: nothing to do */
    }
  }, 400);
}

function installLoraManagerBridge() {
  if (installLoraManagerBridge._done) return;
  installLoraManagerBridge._done = true;

  // Prompt text (recipes, generation parameters)
  api.addEventListener("lm_widget_update", (e) => {
    const m = e?.detail ?? {};
    if (m.action === "inject_text") deliverToMainNode(m, m.value, m.mode ?? "replace");
  });
  // LoRA syntax from model cards
  api.addEventListener("lora_code_update", (e) => {
    const m = e?.detail ?? {};
    deliverToMainNode(m, m.lora_code, m.mode ?? "append");
  });

  // Add our nodes to the registry request LoRA Manager sends, so they appear as send targets.
  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    if (init && typeof init.body === "string" && String(init.method).toUpperCase() === "POST") {
      try {
        const url = typeof input === "string" ? input : input?.url ?? String(input);
        if (url.includes("/lm/register-nodes")) {
          const body = JSON.parse(init.body);
          if (Array.isArray(body.nodes)) {
            for (const mine of ourRegistryEntries()) {
              const existing = body.nodes.find((n) => n.node_id === mine.node_id && n.graph_id === mine.graph_id);
              if (existing) existing.capabilities = { ...(existing.capabilities ?? {}), has_text_widget: true };
              else body.nodes.push(mine);
            }
            init = { ...init, body: JSON.stringify(body) };
          }
        }
      } catch (e) {
        console.error("[MultiTabText] registry patch failed:", e);
      }
    }
    return originalFetch.call(this, input, init);
  };
}

app.registerExtension({
  name: "MultiTabText.Editor",

  async afterConfigureGraph() {
    scheduleRegistryRefresh();
  },

  async setup() {
    installLoraManagerBridge();
    const original = app.graphToPrompt;
    if (typeof original !== "function" || original._mttPatched) return;
    const patched = async function (...args) {
      const result = await original.apply(this, args);
      try {
        injectBypassedMainNodes(result, app.graph);
        return applyRemoveTagsRules(result, app.graph);
      } catch (e) {
        console.error("[MultiTabText] bypass handling failed:", e);
        return result;
      }
    };
    patched._mttPatched = true;
    app.graphToPrompt = patched;
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "RemoveTags") {
      setupDynamicTextInputs(nodeType);
      return;
    }
    if (nodeData.name !== NODE_NAME) return;
    setupDynamicTextInputs(nodeType); // MultiTabText also takes any number of text inputs
    injectCSS();

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);

      const dataWidget = this.widgets.find((w) => w.name === DATA_WIDGET);
      hideWidget(dataWidget); // the raw JSON is edited through the tab editor instead

      try {
        const editor = createEditor(this, dataWidget);
        // Type contains "string" so LoRA Manager's text injection picks this widget (and never the hidden JSON one).
        this.addDOMWidget("editor", "rtt_string_editor", editor.root, {
          serialize: false,
          hideOnZoom: false,
          getMinHeight: () => 300,
          getValue: () => editor.getText(),
          setValue: () => {}, // writes are handled through the lm_widget_update listener instead
        });
        scheduleRegistryRefresh();
        this._rtt = editor;
        this.setSize([560, 460]);
        console.log("[MultiTabText] editor created");
      } catch (err) {
        console.error("[MultiTabText] failed to create editor:", err);
      }
      return r;
    };

    // When a saved workflow is loaded, widget values are restored first, then this runs.
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      // Make sure the saved editor state is what the hidden widget holds.
      const values = info?.widgets_values;
      let saved = values?.[0];
      if (typeof saved === "string" && saved.trim().startsWith("{")) {
        try {
          // Workflows saved when "Remove empty lines" was a separate widget: carry the old value over once.
          const o = JSON.parse(saved);
          if (o && !("removeEmptyLines" in o) && values[1] === true) {
            o.removeEmptyLines = true;
            saved = JSON.stringify(o);
          }
        } catch (e) {
          /* keep as is */
        }
        const w = this.widgets?.find((x) => x.name === DATA_WIDGET);
        if (w) w.value = saved;
      }
      this._rtt?.reload();
      scheduleRegistryRefresh();
      return r;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this._rtt?.dispose?.();
      scheduleRegistryRefresh();
      return onRemoved?.apply(this, arguments);
    };
  },
});
