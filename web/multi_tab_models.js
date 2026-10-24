import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

console.log("[MultiTabText] MultiTabModels extension loaded (build 2026-10-22.2)");

// MultiTabModels: tabs, each with a choice for ComfyUI's "Load Checkpoint", "Load Diffusion Model", "Load CLIP" and
// "Load VAE", an optional CLIP skip (CLIP Set Last Layer), the sampler settings (steps, cfg, sampler_name, scheduler)
// of the KSampler and a LoRA text field. All tabs live in node.properties (saved with the workflow). Only the ACTIVE
// tab's choices go into the hidden `selection` widget, which is the node's only prompt input - so editing other tabs
// never reloads a model.

const NODE_NAME = "MultiTabModels";
const PROP = "tabs_state";
const SELECTION_WIDGET = "selection";

// file: true  -> the first input picks a model file and may be left on "(none)" (nothing is loaded for that group)
// only: [...] -> use just these inputs of the ComfyUI node, in this order
const GROUPS = [
  { key: "ckpt", title: "Load Checkpoint", cls: "CheckpointLoaderSimple", file: true },
  { key: "unet", title: "Load Diffusion Model", cls: "UNETLoader", file: true },
  { key: "clip", title: "Load CLIP", cls: "CLIPLoader", file: true },
  { key: "vae", title: "Load VAE", cls: "VAELoader", file: true },
  { key: "sampling", title: "Sampler settings", cls: "KSampler", only: ["steps", "cfg", "sampler_name", "scheduler"] },
];

const combo = (name, values) => ({ name, kind: "combo", values });

// CLIP Set Last Layer's stop_at_clip_layer: -1 is the last layer, -2 skips one layer, and so on.
const CLIP_SKIP_INPUT = { name: "layer", kind: "int", def: -1, min: -24, max: -1, step: 1 };

// Used only when ComfyUI's node definitions cannot be read.
const FALLBACK_SCHEMA = {
  ckpt: [combo("ckpt_name", [])],
  unet: [combo("unet_name", []), combo("weight_dtype", ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"])],
  clip: [combo("clip_name", []), combo("type", ["stable_diffusion"]), combo("device", ["default", "cpu"])],
  vae: [combo("vae_name", [])],
  sampling: [
    { name: "steps", kind: "int", def: 20, min: 1, max: 10000, step: 1 },
    { name: "cfg", kind: "float", def: 8, min: 0, max: 100, step: 0.1, round: 0.01 },
    combo("sampler_name", ["euler"]),
    combo("scheduler", ["simple"]),
  ],
};

const CSS = `
.mtm-outer{position:relative;width:100%;height:100%;min-height:340px;box-sizing:border-box;}
.mtm-root{position:absolute;inset:0;display:flex;flex-direction:column;gap:4px;box-sizing:border-box;
  font-family:system-ui,sans-serif;font-size:12px;color:var(--input-text,#ddd);}
.mtm-tabbar{display:flex;flex-wrap:nowrap;gap:2px;overflow-x:auto;flex:0 0 auto;padding-bottom:2px;}
.mtm-tab{display:flex;align-items:center;gap:6px;padding:3px 8px;border-radius:4px 4px 0 0;cursor:pointer;white-space:nowrap;
  user-select:none;background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);opacity:.75;}
.mtm-tab.active{opacity:1;background:var(--comfy-input-bg,#222);border-bottom-color:var(--comfy-input-bg,#222);font-weight:600;}
.mtm-tab.dragging{opacity:.4;}
.mtm-tab.over{outline:2px dashed #7aa2f7;}
.mtm-rename{font:inherit;color:inherit;background:var(--comfy-input-bg,#222);border:1px solid #7aa2f7;border-radius:3px;
  padding:0 3px;outline:none;min-width:50px;}
.mtm-close{opacity:.6;padding:0 2px;border-radius:3px;}
.mtm-close:hover{opacity:1;background:rgba(255,80,80,.35);}
.mtm-add{padding:3px 9px;cursor:pointer;border-radius:4px;background:var(--comfy-menu-bg,#353535);
  border:1px solid var(--border-color,#555);user-select:none;}
.mtm-toolbar{display:flex;gap:4px;flex:0 0 auto;flex-wrap:wrap;align-items:center;}
.mtm-btn{padding:2px 8px;cursor:pointer;border-radius:4px;background:var(--comfy-menu-bg,#353535);color:inherit;
  border:1px solid var(--border-color,#555);font-size:11px;}
.mtm-btn:hover,.mtm-add:hover{filter:brightness(1.3);}
.mtm-btn.mtm-danger:hover{background:rgba(255,80,80,.35);filter:none;}
.mtm-opt{display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;user-select:none;padding:0 4px;}
.mtm-opt input{margin:0;cursor:pointer;}
.mtm-groups{flex:1 1 auto;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:6px;padding:6px;box-sizing:border-box;
  border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#222);}
.mtm-group{border:1px solid var(--border-color,#555);border-radius:4px;padding:6px 8px;}
.mtm-gtitle{font-weight:600;margin-bottom:4px;}
.mtm-row{display:flex;align-items:center;gap:6px;margin:3px 0;}
.mtm-label{flex:0 0 92px;opacity:.75;overflow:hidden;text-overflow:ellipsis;}
.mtm-select{flex:1 1 auto;min-width:0;display:flex;align-items:center;justify-content:space-between;gap:6px;box-sizing:border-box;
  cursor:pointer;padding:2px 6px;border-radius:4px;background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);
  user-select:none;outline:none;}
.mtm-select:hover,.mtm-select:focus{border-color:#7aa2f7;}
.mtm-sel-text{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.mtm-caret{flex:0 0 auto;opacity:.7;}
.mtm-num{flex:1 1 auto;min-width:0;width:100%;box-sizing:border-box;font:inherit;color:inherit;padding:2px 6px;border-radius:4px;
  background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);outline:none;}
.mtm-num:hover,.mtm-num:focus{border-color:#7aa2f7;}
.mtm-num:disabled{opacity:.45;cursor:not-allowed;}
.mtm-pop{position:fixed;z-index:100000;display:flex;flex-direction:column;box-sizing:border-box;font:12px system-ui,sans-serif;
  color:var(--input-text,#ddd);background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);border-radius:4px;
  box-shadow:0 4px 14px rgba(0,0,0,.45);outline:none;}
.mtm-pop-filter{margin:4px;padding:2px 6px;font:inherit;color:inherit;background:var(--comfy-input-bg,#222);
  border:1px solid var(--border-color,#555);border-radius:3px;outline:none;}
.mtm-pop-list{overflow:auto;}
.mtm-pop-item{padding:3px 8px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mtm-pop-item.current{font-weight:600;}
.mtm-pop-item.selected{background:rgba(66,153,224,.45);}
.mtm-lora{width:100%;box-sizing:border-box;min-height:54px;resize:vertical;font:12px ui-monospace,Menlo,Consolas,monospace;color:inherit;
  padding:4px 6px;border-radius:4px;background:var(--comfy-menu-bg,#353535);border:1px solid var(--border-color,#555);outline:none;}
.mtm-lora:focus{border-color:#7aa2f7;}
.mtm-hint{opacity:.6;font-size:11px;margin-top:3px;}
.mtm-preview{white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,Menlo,Consolas,monospace;}
.mtm-groups.mtm-flash{border-color:#4299E0;box-shadow:0 0 0 1px #4299E0;}
.mtm-note{opacity:.7;padding:4px;}
`;

function injectCSS() {
  if (document.getElementById("mtm-style")) return;
  const s = document.createElement("style");
  s.id = "mtm-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

// ---------------------------------------------------------------------------
// ComfyUI's own loader definitions -> which inputs each group has and which values they offer
// ---------------------------------------------------------------------------
let schema = null; // { unet: [{name, values, def}], clip: [...], vae: [...] }
let schemaPromise = null;
const editors = new Set();

function parseInput(name, spec) {
  if (!Array.isArray(spec)) return null;
  const opts = spec[1] ?? {};
  if (Array.isArray(spec[0])) return { name, kind: "combo", values: spec[0].map(String), def: opts.default }; // legacy: [[values], {opts}]
  if (spec[0] === "COMBO") return { name, kind: "combo", values: (opts.options ?? []).map(String), def: opts.default }; // newer
  if (spec[0] === "INT" || spec[0] === "FLOAT") {
    const isInt = spec[0] === "INT";
    return { name, kind: isInt ? "int" : "float", def: opts.default, min: opts.min, max: opts.max, step: opts.step ?? (isInt ? 1 : 0.1), round: opts.round };
  }
  return null; // links (MODEL, LATENT, ...) and anything else are not editable here
}

async function fetchGroupSchema(group) {
  try {
    const res = await api.fetchApi(`/object_info/${group.cls}`);
    const info = (await res.json())?.[group.cls];
    const required = info?.input?.required ?? {};
    const names = group.only ?? info?.input_order?.required ?? Object.keys(required);
    const inputs = names.map((n) => parseInput(n, required[n])).filter((i) => i && (group.only || i.kind === "combo"));
    return inputs.length ? inputs : null;
  } catch (e) {
    return null;
  }
}

function loadSchema(force = false) {
  if (schemaPromise && !force) return schemaPromise;
  schemaPromise = (async () => {
    const next = {};
    await Promise.all(
      GROUPS.map(async (g) => {
        next[g.key] = (await fetchGroupSchema(g)) ?? FALLBACK_SCHEMA[g.key];
      })
    );
    schema = next;
    editors.forEach((ed) => ed.onSchema());
    return schema;
  })();
  return schemaPromise;
}

// The value a new tab starts with. Model files start on "(none)": with a checkpoint AND separate loaders available,
// nothing should be loaded until it is chosen. Everything else gets ComfyUI's own default.
function defaultFor(input, isFile) {
  if (input.kind === "combo") {
    if (isFile) return "";
    return input.def !== undefined && input.values.includes(input.def) ? input.def : input.values[0] ?? "";
  }
  return input.def ?? input.min ?? 0;
}

// ---------------------------------------------------------------------------
// State: { active, importAppend, tabs: [{ name, ckpt: {ckpt_name}, unet: {...}, clip: {...}, vae: {...},
//          clipSkip: {enabled, layer}, sampling: {steps, cfg, ...} }] }
// A value that is undefined means "not chosen yet": it gets ComfyUI's default as soon as the lists are known.
// ---------------------------------------------------------------------------
function clipSkipOf(tab) {
  const skip = tab?.clipSkip ?? {};
  const raw = Number(skip.layer);
  return {
    enabled: skip.enabled === true,
    layer: Number.isFinite(raw) ? Math.min(-1, Math.max(-24, Math.round(raw))) : CLIP_SKIP_INPUT.def,
  };
}

function normalizeTab(t, i) {
  const tab = {
    name: String(t?.name ?? `Tab ${i + 1}`),
    lora: typeof t?.lora === "string" ? t.lora : "",
    clipSkip: clipSkipOf(t),
  };
  for (const g of GROUPS) {
    tab[g.key] = {};
    for (const [k, v] of Object.entries(t?.[g.key] ?? {})) {
      if (typeof v === "string" || (typeof v === "number" && Number.isFinite(v))) tab[g.key][k] = v;
    }
  }
  return tab;
}

function normalizeState(o) {
  const src = Array.isArray(o?.tabs) && o.tabs.length ? o.tabs : [{}];
  const tabs = src.map(normalizeTab);
  const active = Math.min(Math.max(parseInt(o?.active) || 0, 0), tabs.length - 1);
  // loraHeight: the height (px) the user gave the LoRA text box; one size for all tabs, saved with the workflow
  const loraHeight = Number.isFinite(o?.loraHeight) && o.loraHeight > 0 ? Math.round(o.loraHeight) : undefined;
  return { active, importAppend: o?.importAppend === true, loraHeight, tabs };
}

function fillDefaults(state) {
  if (!schema) return;
  for (const tab of state.tabs) {
    for (const g of GROUPS) {
      tab[g.key] = tab[g.key] ?? {};
      schema[g.key].forEach((input, idx) => {
        if (tab[g.key][input.name] === undefined) tab[g.key][input.name] = defaultFor(input, g.file && idx === 0);
      });
    }
  }
}

// What the Python side receives: the active tab's choices (nothing is sent for values that are still unset).
function selectionOf(tab) {
  const out = { lora: tab?.lora ?? "", clip_skip: clipSkipOf(tab) };
  for (const g of GROUPS) out[g.key] = { ...(tab?.[g.key] ?? {}) };
  return out;
}

function download(filename, content) {
  const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function pickFiles(multiple) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json,application/json";
    input.multiple = multiple;
    input.onchange = () => resolve(Array.from(input.files || []));
    input.click();
  });
}

function hideWidget(w) {
  if (!w) return;
  w.type = "hidden";
  w.hidden = true;
  if (w.options) w.options.hidden = true;
  w.computeSize = () => [0, -4];
  if (w.element) w.element.style.display = "none";
}

// Clamp to ComfyUI's min / max and round (whole numbers for INT, the node's precision for FLOAT).
function normalizeNumber(input, v) {
  if (input.min !== undefined) v = Math.max(input.min, v);
  if (input.max !== undefined) v = Math.min(input.max, v);
  if (input.kind === "int") return Math.round(v);
  const precision = input.round || input.step;
  const decimals = precision && precision < 1 ? (String(precision).split(".")[1] ?? "").length : 0;
  return Number(v.toFixed(decimals));
}

// A number input for INT / FLOAT values (steps, cfg). Typed values are stored right away; when the field is left
// they are clamped to ComfyUI's min / max (and rounded for INT / to the node's precision for FLOAT). The mouse wheel
// changes the value by one step while the pointer is over the field.
function numberField(store, input, save) {
  const num = document.createElement("input");
  num.type = "number";
  num.className = "mtm-num";
  num.setAttribute("data-capture-wheel", "true"); // let the wheel handler run instead of ComfyUI's canvas zoom
  if (input.min !== undefined) num.min = String(input.min);
  if (input.max !== undefined) num.max = String(input.max);
  num.step = String(input.step ?? 1);
  num.value = String(store[input.name] ?? "");
  num.title = [input.min !== undefined ? `min ${input.min}` : "", input.max !== undefined ? `max ${input.max}` : ""].filter(Boolean).join(", ");
  const normalize = (v) => normalizeNumber(input, v);
  const typed = () => (num.value.trim() === "" || !Number.isFinite(Number(num.value)) ? null : Number(num.value));
  num.addEventListener("input", () => {
    const v = typed();
    if (v === null) return;
    store[input.name] = input.kind === "int" ? Math.round(v) : v;
    save();
  });
  num.addEventListener("change", () => {
    const v = normalize(typed() ?? defaultFor(input, false));
    store[input.name] = v;
    num.value = String(v);
    save();
  });
  num.addEventListener(
    "wheel",
    (e) => {
      if (num.disabled || !e.deltaY) return;
      e.preventDefault(); // change the value instead of scrolling the node / canvas
      e.stopPropagation();
      closePopup();
      const step = Math.abs(Number(input.step)) || 1;
      const base = normalize(typed() ?? defaultFor(input, false));
      const v = normalize(base + (e.deltaY < 0 ? step : -step));
      store[input.name] = v;
      num.value = String(v);
      save();
    },
    { passive: false }
  );
  ["keydown", "keyup"].forEach((ev) => num.addEventListener(ev, (e) => e.stopPropagation())); // no ComfyUI shortcuts while typing
  return num;
}

// ---------------------------------------------------------------------------
// Drop-down list. Same behaviour as ComfyUI's combo menu: lists with more than 10 entries get a "Filter list" box
// (focused on open) that hides entries not containing the typed text (case-insensitive); Up/Down move the
// highlight, Enter picks it, Escape closes; the current value is highlighted and scrolled into view.
// ---------------------------------------------------------------------------
const FILTER_MIN_ENTRIES = 10; // the filter box appears when there are MORE entries than this (as in ComfyUI)
let activePopup = null;

function closePopup() {
  activePopup?.close();
}

function openPopup(field, choices, current, onPick) {
  closePopup();
  const pop = document.createElement("div");
  pop.className = "mtm-pop";
  pop.tabIndex = -1;
  pop.setAttribute("data-capture-wheel", "true"); // wheel scrolls the list instead of zooming the canvas

  let filter = null;
  if (choices.length > FILTER_MIN_ENTRIES) {
    filter = document.createElement("input");
    filter.type = "text";
    filter.className = "mtm-pop-filter";
    filter.placeholder = "Filter list";
    pop.append(filter);
  }
  const list = document.createElement("div");
  list.className = "mtm-pop-list";
  pop.append(list);

  const items = choices.map(([value, label]) => {
    const node = document.createElement("div");
    node.className = "mtm-pop-item" + (value === current ? " current" : "");
    node.textContent = label;
    node.title = label;
    list.append(node);
    return { value, label, text: label.toLocaleLowerCase(), node };
  });

  let displayed = items.slice();
  let selected = Math.max(0, displayed.findIndex((it) => it.value === current));
  const paint = () => {
    const target = displayed[selected];
    items.forEach((it) => it.node.classList.toggle("selected", it === target)); // also clears hidden entries
    target?.node.scrollIntoView?.({ block: "nearest" });
  };
  const choose = (item) => {
    if (!item) return;
    close();
    onPick(item.value);
    field.focus?.();
  };

  items.forEach((it) => {
    it.node.addEventListener("click", () => choose(it));
    it.node.addEventListener("mouseenter", () => {
      const i = displayed.indexOf(it);
      if (i >= 0) {
        selected = i;
        paint();
      }
    });
  });

  if (filter) {
    filter.addEventListener("input", () => {
      const term = filter.value.toLocaleLowerCase();
      const keep = displayed[selected];
      displayed = items.filter((it) => {
        const visible = !term || it.text.includes(term);
        it.node.style.display = visible ? "block" : "none";
        return visible;
      });
      const idx = displayed.indexOf(keep); // keep the highlighted entry if it is still shown
      selected = idx >= 0 ? idx : 0;
      paint();
    });
  }

  const onKey = (e) => {
    e.stopPropagation(); // keep ComfyUI's shortcuts out of the list
    switch (e.key) {
      case "ArrowUp":
        e.preventDefault();
        if (displayed.length) selected = (selected - 1 + displayed.length) % displayed.length;
        paint();
        break;
      case "ArrowDown":
        e.preventDefault();
        if (displayed.length) selected = (selected + 1) % displayed.length;
        paint();
        break;
      case "Enter":
        e.preventDefault();
        choose(displayed[selected]);
        break;
      case "Escape":
        e.preventDefault();
        close();
        field.focus?.();
        break;
    }
  };
  (filter ?? pop).addEventListener("keydown", onKey);
  (filter ?? pop).addEventListener("keyup", (e) => e.stopPropagation());

  // Close when clicking / scrolling / zooming anywhere else.
  const onOutside = (e) => {
    if (!pop.contains?.(e.target) && e.target !== field && !field.contains?.(e.target)) close();
  };
  const onResize = () => close();
  document.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("wheel", onOutside, { capture: true, passive: true });
  window.addEventListener("resize", onResize);
  window.addEventListener("blur", onResize);

  function close() {
    if (activePopup !== handle) return;
    activePopup = null;
    document.removeEventListener("pointerdown", onOutside, true);
    window.removeEventListener("wheel", onOutside, true);
    window.removeEventListener("resize", onResize);
    window.removeEventListener("blur", onResize);
    pop.remove();
  }
  const handle = { field, close };
  activePopup = handle;

  // Place it under the field (above it when there is more room there) and keep it on screen.
  document.body.appendChild(pop);
  const rect = field.getBoundingClientRect();
  const vh = window.innerHeight || 800;
  const vw = window.innerWidth || 1200;
  const below = vh - rect.bottom - 8;
  const above = rect.top - 8;
  const placeBelow = below >= 200 || below >= above;
  const room = Math.max(120, Math.min(380, placeBelow ? below : above));
  list.style.maxHeight = `${room - (filter ? 34 : 0)}px`;
  pop.style.minWidth = `${Math.max(rect.width, 200)}px`;
  pop.style.maxWidth = `${Math.max(240, vw - 16)}px`;
  pop.style.left = `${Math.max(8, Math.min(rect.left, vw - Math.max(rect.width, 200) - 8))}px`;
  if (placeBelow) pop.style.top = `${rect.bottom + 2}px`;
  else pop.style.bottom = `${vh - rect.top + 2}px`;
  paint();
  (filter ?? pop).focus?.();
}

// ---------------------------------------------------------------------------
// LoRA Manager: which file kind was sent, and LoRA text merging
// ---------------------------------------------------------------------------
const MODEL_KINDS = { ckpt: "ckpt_name", unet: "unet_name", clip: "clip_name", vae: "vae_name" }; // group key -> the input that holds the file
const KIND_BY_WIDGET = { ckpt_name: "ckpt", unet_name: "unet", clip_name: "clip", vae_name: "vae" };

const normPath = (s) => String(s).replace(/\\/g, "/").toLowerCase();

// The list entry that is the same file as `value` (slash style, case and extra leading folders do not matter).
function matchInList(values, value) {
  if (values.includes(value)) return value;
  const n = normPath(value);
  const same = values.find((v) => normPath(v) === n);
  if (same) return same;
  const suffix = values.filter((v) => normPath(v).endsWith("/" + n) || n.endsWith("/" + normPath(v)));
  if (suffix.length === 1) return suffix[0];
  const base = n.split("/").pop();
  const byName = values.filter((v) => normPath(v).split("/").pop() === base);
  return byName.length === 1 ? byName[0] : null;
}

// Decide which loader group a file sent by LoRA Manager belongs to. The widget name is the first hint;
// a checkpoint / diffusion model that is only found in the other of those two lists is moved there. CLIP and VAE
// files stay in their own group. Unknown widget names only match a file that is really in one of the lists.
function resolveFile(widgetName, value) {
  const listOf = (kind) => schema?.[kind]?.find((i) => i.name === MODEL_KINDS[kind])?.values ?? [];
  const hinted = KIND_BY_WIDGET[widgetName] ?? null;
  if (hinted === "ckpt" || hinted === "unet") {
    const order = [hinted, hinted === "ckpt" ? "unet" : "ckpt"];
    for (const kind of order) {
      const found = matchInList(listOf(kind), value);
      if (found) return { kind, input: MODEL_KINDS[kind], value: found, known: true };
    }
    return { kind: hinted, input: MODEL_KINDS[hinted], value, known: false }; // not on disk (any more): kept and marked "(not found)"
  }
  if (hinted) {
    const found = matchInList(listOf(hinted), value);
    return { kind: hinted, input: MODEL_KINDS[hinted], value: found ?? value, known: !!found };
  }
  for (const kind of Object.keys(MODEL_KINDS)) {
    const found = matchInList(listOf(kind), value);
    if (found) return { kind, input: MODEL_KINDS[kind], value: found, known: true };
  }
  return null; // not a model widget and not a known file: not for us
}

// Comments in the LoRA text (same rules as MultiTabText): lines starting with # or //, and /* ... */ blocks.
// stripLoraComments mirrors the Python side (what is output as LORA); commentRanges tells where comments are.
const COMMENT_LINE = /^\s*(#|\/\/)/;

function stripLoraComments(text) {
  const out = [];
  let inBlock = false;
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (!inBlock && COMMENT_LINE.test(line)) continue;
    let had = inBlock;
    let kept = "";
    let i = 0;
    while (i < line.length) {
      if (inBlock) {
        const end = line.indexOf("*/", i);
        if (end === -1) break;
        inBlock = false;
        i = end + 2;
      } else {
        const start = line.indexOf("/*", i);
        if (start === -1) {
          kept += line.slice(i);
          break;
        }
        kept += line.slice(i, start);
        had = true;
        inBlock = true;
        i = start + 2;
      }
    }
    if (had && !kept.trim()) continue; // a line with nothing but comment
    out.push(kept);
  }
  return out.join("\n");
}

// [start, end) character ranges of `text` that are comments.
function commentRanges(text) {
  const ranges = [];
  let pos = 0;
  let inBlock = false;
  let blockStart = 0;
  for (const line of text.split("\n")) {
    const lineStart = pos;
    const lineEnd = pos + line.length;
    if (inBlock) blockStart = lineStart;
    if (!inBlock && COMMENT_LINE.test(line)) {
      ranges.push([lineStart, lineEnd]);
    } else {
      let i = 0;
      while (i < line.length) {
        if (inBlock) {
          const end = line.indexOf("*/", i);
          if (end === -1) {
            ranges.push([blockStart, lineEnd]);
            i = line.length;
          } else {
            ranges.push([blockStart, lineStart + end + 2]);
            inBlock = false;
            i = end + 2;
          }
        } else {
          const start = line.indexOf("/*", i);
          if (start === -1) break;
          inBlock = true;
          blockStart = lineStart + start;
          i = start + 2;
        }
      }
    }
    pos = lineEnd + 1;
  }
  return ranges;
}

// Generation parameters LoRA Manager can send ("Send params to workflow") that this node has a field for.
// Anything else it sends (seed, size, ...) is not for this node.
const PARAM_FIELDS = { steps: "steps", cfg: "cfg", sampler_name: "sampler_name", scheduler: "scheduler" };
const looseName = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");

const LORA_TAG = /<lora:[^>]*>/gi;
const loraName = (tag) => tag.slice(6).split(":")[0].trim().toLowerCase();
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Append LoRA text. A <lora:...> tag whose name is already present only updates that tag (new strength), like
// LoRA Manager does for its own loader nodes; everything else (trigger words ...) is added after the tags.
// Commented-out tags are left alone and never count as "already present"; text is never appended into a comment
// (after a line comment it goes on a new line, before an unclosed /* block it is inserted in front of the block).
function mergeLoraText(existing, incoming) {
  let text = existing;
  const appendTo = (add, separator) => {
    const body = text.trimEnd();
    if (!body) return add;
    const last = body.length - 1;
    const range = commentRanges(body).find(([a, b]) => last >= a && last < b);
    if (!range) return `${body}${separator}${add}`;
    // The text ends inside a comment. An unclosed /* block would swallow what is added, so insert before it;
    // after a line comment a new line is enough.
    const openBlock = body.startsWith("/*", range[0]) && body.indexOf("*/", range[0] + 2) === -1;
    if (!openBlock) return `${body}\n${add}`;
    const head = body.slice(0, range[0]).trimEnd();
    return `${head}${head ? separator : ""}${add}\n${body.slice(range[0])}`;
  };
  for (const tag of incoming.match(LORA_TAG) ?? []) {
    const same = new RegExp(`<lora:\\s*${escapeRe(loraName(tag))}\\s*(?::[^>]*)?>`, "gi");
    const comments = commentRanges(text);
    let hit = null;
    for (let m = same.exec(text); m; m = same.exec(text)) {
      if (!comments.some(([a, b]) => m.index >= a && m.index < b)) {
        hit = m;
        break;
      }
    }
    text = hit ? text.slice(0, hit.index) + tag + text.slice(hit.index + hit[0].length) : appendTo(tag, " ");
  }
  const rest = incoming.replace(LORA_TAG, "").replace(/^[\s,]+/, "").trim(); // trigger words: drop the comma that followed the tag
  if (rest) text = appendTo(rest, "\n");
  return text;
}

// LoRA Manager only offers a node as a target of "Send Params to Workflow" when its registry entry carries the marker
// role "send_gen_params" (what "Mark as -> Send Gen Params Target" in a node's right-click menu sets). MultiTabModels claims
// that role for itself, so nothing has to be marked by hand. A role the user chose on the node is respected.
const GEN_PARAMS_ROLE = "send_gen_params";

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
  for (const { graph } of allGraphNodes()) if (graphIdOf(graph) === graphId) return graph.getNodeById?.(key) ?? null;
  return app.graph?.getNodeById?.(key) ?? null;
}

// Entries in the format LoRA Manager's workflow registry uses (POST /api/lm/register-nodes). Listing the widget names
// "ckpt_name" / "unet_name" / "clip_name" / "vae_name" makes this node a target of model sends, "steps" / "cfg" /
// "sampler_name" / "scheduler" / "clip_skip" of "send params"; supports_lora makes it a target of "send LoRA".
function registryEntries() {
  return allGraphNodes()
    .filter(({ node }) => node?.comfyClass === NODE_NAME)
    .map(({ graph, node }) => ({
      node_id: node.id,
      graph_id: graphIdOf(graph),
      graph_name: typeof graph?.name === "string" && graph.name.trim() ? graph.name : null,
      bgcolor: node.bgcolor ?? node.color ?? null,
      title: node.title || NODE_NAME,
      type: NODE_NAME,
      comfy_class: NODE_NAME,
      mode: node.mode,
      marker_role: node.properties?.lm_marker_role ?? GEN_PARAMS_ROLE,
      capabilities: {
        supports_lora: true,
        has_text_widget: false,
        widget_names: ["ckpt_name", "unet_name", "clip_name", "vae_name", "steps", "cfg", "sampler_name", "scheduler", "clip_skip"],
      },
    }));
}

let registryTimer = null;
function scheduleRegistryRefresh() {
  clearTimeout(registryTimer);
  registryTimer = setTimeout(() => {
    try {
      api.dispatchEvent(new CustomEvent("lora_registry_refresh")); // LoRA Manager re-sends its node list
    } catch (e) {
      /* LoRA Manager not installed */
    }
  }, 400);
}

function installLoraManagerBridge() {
  if (installLoraManagerBridge._done) return;
  installLoraManagerBridge._done = true;

  const target = (msg) => {
    const id = msg?.node_id ?? msg?.id;
    if (id == null || Number(id) === -1) return null; // -1 = broadcast to LoRA loader nodes only
    const node = findNode(msg.graph_id, id);
    return node?.comfyClass === NODE_NAME ? node._mtm ?? null : null;
  };

  // "Send checkpoint / diffusion model / CLIP / VAE / params to workflow": a widget name + a value
  api.addEventListener("lm_widget_update", (e) => {
    const m = e?.detail ?? {};
    if (m.action === "inject_text" || m.widget_name === undefined) return;
    const editor = target(m);
    if (!editor) return;
    const name = String(m.widget_name);
    const key = name.toLowerCase().replace(/[\s-]+/g, "_");
    console.debug(`[MultiTabText] LoRA Manager sent ${name} =`, m.value);
    if (key === "clip_skip" || key === "clipskip" || key === "stop_at_clip_layer") {
      editor.applyClipSkip(m.value);
    } else if (Object.hasOwn(PARAM_FIELDS, key)) {
      editor.applyParam(key, m.value); // steps / cfg / sampler / scheduler
    } else editor.applyFile(key, m.value); // ckpt_name / unet_name / clip_name / vae_name (or a known file under another name)
  });
  // "Send LoRA to workflow": LoRA syntax, appended (merged) or replacing
  api.addEventListener("lora_code_update", (e) => {
    const m = e?.detail ?? {};
    target(m)?.applyLora(m.lora_code, m.mode ?? "append");
  });

  // Add our nodes to the registry request LoRA Manager sends, so they are offered as targets.
  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    if (init && typeof init.body === "string" && String(init.method).toUpperCase() === "POST") {
      try {
        const url = typeof input === "string" ? input : input?.url ?? String(input);
        if (url.includes("/lm/register-nodes")) {
          const body = JSON.parse(init.body);
          if (Array.isArray(body.nodes)) {
            for (const mine of registryEntries()) {
              const existing = body.nodes.find((n) => n.node_id === mine.node_id && n.graph_id === mine.graph_id);
              if (existing) {
                existing.capabilities = { ...(existing.capabilities ?? {}), ...mine.capabilities };
                existing.marker_role = existing.marker_role || mine.marker_role; // keep a role chosen by the user
              } else {
                body.nodes.push(mine);
              }
            }
            init = { ...init, body: JSON.stringify(body) };
            console.debug(`[MultiTabText] told LoRA Manager about ${registryEntries().length} MultiTabModels node(s) (role: ${GEN_PARAMS_ROLE})`);
          }
        }
      } catch (e) {
        console.error("[MultiTabText] registry patch failed:", e);
      }
    }
    return originalFetch.call(this, input, init);
  };
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
function createEditor(node, selectionWidget) {
  node.properties = node.properties ?? {};
  let state = normalizeState(node.properties[PROP]);
  let dragFrom = -1;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  const outer = el("div", "mtm-outer");
  const root = el("div", "mtm-root");
  const tabbar = el("div", "mtm-tabbar");
  const toolbar = el("div", "mtm-toolbar");
  const groupsEl = el("div", "mtm-groups");
  groupsEl.setAttribute("data-capture-wheel", "true"); // scroll the group list instead of zooming the canvas
  root.append(tabbar, toolbar, groupsEl);
  outer.append(root);

  // ----- persistence -----
  const notifyNow = () => {
    try {
      const wf = app.extensionManager?.workflow?.activeWorkflow ?? app.workflowManager?.activeWorkflow;
      wf?.changeTracker?.checkState?.();
    } catch (e) {
      /* ignore */
    }
  };
  let notifyTimer = null;
  const syncSelection = () => {
    selectionWidget.value = JSON.stringify(selectionOf(state.tabs[state.active]));
  };
  const save = () => {
    node.properties[PROP] = state;
    syncSelection();
    node.setDirtyCanvas?.(true, true);
    clearTimeout(notifyTimer);
    notifyTimer = setTimeout(notifyNow, 300);
  };
  const flush = () => {
    if (!node.graph) return;
    clearTimeout(notifyTimer);
    notifyNow();
  };
  window.addEventListener("beforeunload", flush);
  window.addEventListener("pagehide", flush);

  const uniqueName = () => {
    const used = new Set(state.tabs.map((t) => t.name));
    let n = 1;
    while (used.has(`Tab ${n}`)) n++;
    return `Tab ${n}`;
  };

  // ----- size of the LoRA text box: remembered across tab switches, saves and reloads -----
  const MIN_LORA_HEIGHT = 54;
  let sizeObserver = null;
  const recordLoraHeight = (h) => {
    h = Math.max(MIN_LORA_HEIGHT, Math.min(2000, Math.round(h)));
    if (h !== state.loraHeight) {
      state.loraHeight = h;
      save();
    }
  };
  const watchLoraSize = (textarea) => {
    sizeObserver?.disconnect();
    if (typeof ResizeObserver === "undefined") {
      textarea.addEventListener("mouseup", () => textarea.offsetHeight > 0 && recordLoraHeight(textarea.offsetHeight));
      return;
    }
    let baseline = null; // the size the box has when it appears; only later changes are the user's doing
    sizeObserver = new ResizeObserver(() => {
      const h = Math.round(textarea.offsetHeight);
      if (h <= 0) return; // not visible (collapsed node, zoomed away): nothing to learn
      if (baseline === null) {
        baseline = h;
        return;
      }
      if (h !== baseline) {
        baseline = h;
        recordLoraHeight(h);
      }
    });
    sizeObserver.observe(textarea);
  };

  // CLIP Set Last Layer lives visually in the Load Checkpoint group, but applies to the final CLIP output.
  const appendClipSkip = (box, tab) => {
    const row = el("div", "mtm-row");
    row.append(el("div", "mtm-label", "CLIP skip"));
    const toggleLabel = el("label", "mtm-opt");
    toggleLabel.title = "Apply ComfyUI's CLIP Set Last Layer to this tab's CLIP output";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = tab.clipSkip.enabled;
    const layer = numberField(tab.clipSkip, CLIP_SKIP_INPUT, save);
    layer.title = "stop_at_clip_layer: -1 = last layer, -2 = skip one layer, ...";
    layer.disabled = !tab.clipSkip.enabled;
    toggle.addEventListener("change", () => {
      tab.clipSkip.enabled = toggle.checked;
      layer.disabled = !toggle.checked;
      save();
    });
    toggleLabel.append(toggle, document.createTextNode("use"));
    row.append(toggleLabel, layer);
    box.append(row);
  };

  // ----- loader groups (the active tab) -----
  const renderGroups = () => {
    closePopup();
    groupsEl.innerHTML = "";
    if (!schema) {
      groupsEl.append(el("div", "mtm-note", "Loading model lists..."));
      return;
    }
    const tab = state.tabs[state.active];
    for (const g of GROUPS) {
      const box = el("div", "mtm-group");
      box.append(el("div", "mtm-gtitle", g.title));
      schema[g.key].forEach((input, idx) => {
        const row = el("div", "mtm-row");
        row.append(el("div", "mtm-label", input.name));
        if (input.kind !== "combo") {
          row.append(numberField(tab[g.key], input, save));
          box.append(row);
          return;
        }
        const field = el("div", "mtm-select");
        field.tabIndex = 0;
        field.setAttribute?.("role", "combobox");
        field.setAttribute?.("data-capture-wheel", "true"); // wheel steps through the choices instead of zooming the canvas
        const textEl = el("span", "mtm-sel-text");
        field.append(textEl, el("span", "mtm-caret", "\u25be"));
        const current = tab[g.key][input.name];
        const choices = input.values.map((v) => [v, v]);
        if (g.file && idx === 0) choices.unshift(["", "(none)"]); // the file input may be left empty -> nothing is loaded
        if (current !== undefined && current !== "" && !input.values.includes(current)) choices.push([current, `${current}  (not found)`]);
        const show = () => {
          const value = tab[g.key][input.name] ?? "";
          textEl.textContent = choices.find(([v]) => v === value)?.[1] ?? value;
          field.title = value;
        };
        show();
        const open = () => {
          if (activePopup?.field === field) return closePopup(); // click again = close
          openPopup(field, choices, tab[g.key][input.name] ?? "", (value) => {
            tab[g.key][input.name] = value;
            show();
            save();
          });
        };
        // Mouse wheel steps through the choices (like ComfyUI's own combo widgets).
        field.addEventListener(
          "wheel",
          (e) => {
            if (activePopup?.field === field || !e.deltaY) return; // an open list closes on scroll
            e.preventDefault();
            e.stopPropagation();
            closePopup();
            const values = choices.map(([value]) => value);
            const i = values.indexOf(tab[g.key][input.name] ?? "");
            const next = i < 0 ? (e.deltaY < 0 ? values.length - 1 : 0) : (i + (e.deltaY < 0 ? -1 : 1) + values.length) % values.length;
            tab[g.key][input.name] = values[next];
            show();
            save();
          },
          { passive: false }
        );
        field.addEventListener("click", open);
        field.addEventListener("keydown", (e) => {
          if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
            e.preventDefault();
            e.stopPropagation();
            open();
          }
        });
        row.append(field);
        box.append(row);
      });
      if (g.key === "ckpt") appendClipSkip(box, tab);
      groupsEl.append(box);
    }

    const loraBox = el("div", "mtm-group");
    loraBox.append(el("div", "mtm-gtitle", "LoRA text  (output LORA)"));
    const loraText = el("textarea", "mtm-lora");
    loraText.spellcheck = false;
    loraText.placeholder = "<lora:name:0.8>  - typed here or sent from LoRA Manager\n# comment    // comment    /* comment */";
    loraText.value = tab.lora ?? "";
    if (state.loraHeight) loraText.style.height = `${state.loraHeight}px`;
    const preview = el("div", "mtm-hint mtm-preview");
    const showPreview = () => {
      const output = stripLoraComments(loraText.value);
      preview.style.display = output === loraText.value ? "none" : "block"; // only when comments change the output
      preview.textContent = `Output without comments:\n${output.trim() ? output : "(empty)"}`; // the output starts on its own line
    };
    loraText.addEventListener("input", () => {
      tab.lora = loraText.value;
      showPreview();
      save();
    });
    ["keydown", "keyup"].forEach((ev) => loraText.addEventListener(ev, (e) => e.stopPropagation())); // no ComfyUI shortcuts while typing
    loraBox.append(
      loraText,
      el("div", "mtm-hint", "Output on LORA, e.g. into a LoRA loader's text input. Comments (# or // lines, /* ... */) are not output."),
      preview
    );
    showPreview();
    groupsEl.append(loraBox);
    watchLoraSize(loraText); // after it is in the page, so the observer sees the real size
  };

  // ----- tabs -----
  const selectTab = (i) => {
    if (i === state.active) return;
    state.active = i;
    save();
    Array.from(tabbar.querySelectorAll(".mtm-tab")).forEach((n, idx) => n.classList.toggle("active", idx === i));
    renderGroups();
  };

  // Scroll the tab bar just enough to show a tab (only horizontally).
  const revealTab = (i) => {
    const tab = tabbar.querySelectorAll(".mtm-tab")[i];
    if (!tab) return;
    const bar = tabbar.getBoundingClientRect();
    const rect = tab.getBoundingClientRect();
    if (rect.left < bar.left) tabbar.scrollLeft -= bar.left - rect.left + 2;
    else if (rect.right > bar.right) tabbar.scrollLeft += rect.right - bar.right + 2;
  };

  // Mouse wheel over the tab bar switches tabs (up = previous, down = next, wrapping around).
  tabbar.setAttribute("data-capture-wheel", "true");
  tabbar.addEventListener(
    "wheel",
    (e) => {
      if (!e.deltaY || state.tabs.length < 2 || e.target?.closest?.(".mtm-rename")) return;
      e.preventDefault();
      e.stopPropagation();
      const step = e.deltaY < 0 ? -1 : 1;
      const next = (state.active + step + state.tabs.length) % state.tabs.length;
      selectTab(next);
      revealTab(next);
    },
    { passive: false }
  );

  const addTab = (tab = null) => {
    state.tabs.push(tab ?? normalizeTab({ name: uniqueName() }, state.tabs.length));
    state.active = state.tabs.length - 1;
    fillDefaults(state);
    save();
    renderTabs();
    renderGroups();
  };

  const removeTab = (i) => {
    if (state.tabs.length === 1) {
      state.tabs[0] = normalizeTab({ name: "Tab 1" }, 0); // always keep one tab
      state.active = 0;
    } else {
      state.tabs.splice(i, 1);
      if (state.active > i || state.active >= state.tabs.length) state.active = Math.max(0, state.active - 1);
    }
    fillDefaults(state);
    save();
    renderTabs();
    renderGroups();
  };

  // "Tab 1 copy", "Tab 1 copy 2", ... (a name that is not used yet)
  const copyName = (name) => {
    const used = new Set(state.tabs.map((t) => t.name));
    const base = name.replace(/ copy( \d+)?$/, "") + " copy"; // a copy of a copy stays "X copy 2", not "X copy copy"
    let candidate = base;
    for (let n = 2; used.has(candidate); n++) candidate = `${base} ${n}`;
    return candidate;
  };

  // Copy the active tab (every choice, the sampler settings and the LoRA text) right behind it and switch to the copy.
  // It is a deep copy: changing the copy never changes the original.
  const duplicateTab = () => {
    const copy = JSON.parse(JSON.stringify(state.tabs[state.active]));
    copy.name = copyName(copy.name);
    state.tabs.splice(state.active + 1, 0, copy);
    state.active += 1;
    save();
    renderTabs();
    renderGroups();
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
    const input = el("input", "mtm-rename");
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
      e.stopPropagation();
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });
    input.addEventListener("keyup", (e) => e.stopPropagation());
    input.addEventListener("blur", () => finish(true));
    ["click", "dblclick", "mousedown", "pointerdown"].forEach((ev) => input.addEventListener(ev, (e) => e.stopPropagation()));
  };

  const renderTabs = () => {
    tabbar.innerHTML = "";
    state.tabs.forEach((tab, i) => {
      const t = el("div", "mtm-tab" + (i === state.active ? " active" : ""));
      t.title = "Click: select (this tab's models are loaded) | Double-click: rename | Drag: reorder";
      t.draggable = true;
      const nameSpan = el("span", "mtm-name", tab.name);
      const x = el("span", "mtm-close", "\u00d7");
      x.title = "Remove tab";
      x.onclick = (e) => {
        e.stopPropagation();
        removeTab(i);
      };
      t.append(nameSpan, x);
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
    const add = el("div", "mtm-add", "+");
    add.title = "New tab";
    add.onclick = () => addTab();
    tabbar.append(add);
  };

  // ----- toolbar: import / export / clear / refresh -----
  const safeName = (s) => s.replace(/[^\w\- ]+/g, "_").trim() || "tab";
  const btn = (label, title, fn, extra = "") => {
    const b = el("button", "mtm-btn" + (extra ? " " + extra : ""), label);
    b.type = "button";
    b.title = title;
    b.onclick = fn;
    toolbar.append(b);
    return b;
  };

  btn("Duplicate tab", "Copy the active tab (all choices and the LoRA text) right after it", duplicateTab);

  btn("Export tab", "Save the active tab as a .json file", () => {
    const tab = state.tabs[state.active];
    download(`${safeName(tab.name)}.json`, JSON.stringify(tab, null, 2));
  });

  btn("Import tab(s)", "Load one or more .json files, each into a new tab", async () => {
    const files = await pickFiles(true);
    let added = 0;
    for (const f of files) {
      try {
        const data = JSON.parse(await f.text());
        const incoming = Array.isArray(data?.tabs) ? data.tabs : [data]; // also accepts an "Export all" file
        for (const t of incoming) {
          state.tabs.push(normalizeTab({ ...t, name: t?.name ?? f.name.replace(/\.[^.]+$/, "") }, state.tabs.length));
          added++;
        }
      } catch (e) {
        alert(`Could not read "${f.name}": expected a .json file exported from MultiTabModels.`);
      }
    }
    if (added) {
      state.active = state.tabs.length - 1;
      fillDefaults(state);
      save();
      renderTabs();
      renderGroups();
    }
  });

  btn("Export all", "Save all tabs as one .json file", () => {
    download("MultiTabModels.json", JSON.stringify({ format: "MultiTabModels", active: state.active, tabs: state.tabs }, null, 2));
  });

  btn("Import all", "Import tabs from an exported .json file: replaces all tabs, or is added after them if 'Append on import' is ticked", async () => {
    const [f] = await pickFiles(false);
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
      const firstNew = state.tabs.length;
      state.tabs.push(...normalizeState(data).tabs);
      state.active = firstNew;
    } else {
      if (!confirm("Replace ALL current tabs with the imported ones?")) return;
      state = normalizeState({ ...data, importAppend: false, loraHeight: state.loraHeight }); // layout is not taken from the file
    }
    fillDefaults(state);
    appendCb.checked = state.importAppend;
    save();
    renderTabs();
    renderGroups();
  });

  btn(
    "Clear all",
    "Close all tabs and reset to one default tab",
    () => {
      if (state.tabs.length > 1 && !confirm("Close all tabs and reset the node? This cannot be undone.")) return;
      state.tabs = [normalizeTab({ name: "Tab 1" }, 0)];
      state.active = 0;
      fillDefaults(state);
      save();
      renderTabs();
      renderGroups();
    },
    "mtm-danger"
  );

  btn("Refresh lists", "Re-read the model lists from ComfyUI (after adding or removing model files)", () => {
    loadSchema(true);
  });

  const appendLabel = el("label", "mtm-opt");
  appendLabel.title = "Import all: checked = add the imported tabs after the current ones, unchecked = replace all current tabs";
  const appendCb = document.createElement("input");
  appendCb.type = "checkbox";
  appendCb.checked = state.importAppend;
  appendCb.onchange = () => {
    state.importAppend = appendCb.checked;
    save();
  };
  appendLabel.append(appendCb, document.createTextNode("Append on import"));
  toolbar.append(appendLabel);

  // ----- file names and params sent from LoRA Manager go into the ACTIVE tab -----
  let flashTimer = null;
  const flash = () => {
    groupsEl.classList.add("mtm-flash");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => groupsEl.classList.remove("mtm-flash"), 1500);
  };

  // widgetName is what LoRA Manager sent ("ckpt_name" / "unet_name" / "clip_name" / "vae_name"); the file itself
  // decides when a checkpoint / diffusion model only exists in the other list. Returns the group key or null.
  const applyFile = (widgetName, value) => {
    if (!schema || typeof value !== "string" || !value) return null;
    const hit = resolveFile(widgetName, value);
    if (!hit) return null;
    const tab = state.tabs[state.active];
    tab[hit.kind][hit.input] = hit.value;
    save();
    renderGroups();
    flash();
    return hit.kind;
  };

  // LoRA Manager's recipes store clip skip as a positive count (1 = last layer, 2 = skip one); the editor and
  // CLIP Set Last Layer use negative layers (-1, -2). Accept either convention and enable the setting.
  const applyClipSkip = (value) => {
    let skip = value;
    if (value && typeof value === "object") {
      const raw = value.layer ?? value.value ?? value.clip_skip ?? value.clipSkip ?? value.stop_at_clip_layer;
      if (raw === undefined) {
        if (value.enabled !== false) return false;
        state.tabs[state.active].clipSkip = { ...state.tabs[state.active].clipSkip, enabled: false };
      } else {
        skip = Number(raw);
        if (raw === null || raw === "" || !Number.isFinite(skip)) return false;
        const layer = skip === 0 ? -1 : Math.max(-24, Math.min(-1, Math.round(skip < 0 ? skip : -skip)));
        state.tabs[state.active].clipSkip = { enabled: value.enabled !== false && skip !== 0, layer };
      }
    } else {
      const n = Number(value);
      if (value === null || value === "" || !Number.isFinite(n)) return false;
      const layer = n === 0 ? -1 : Math.max(-24, Math.min(-1, Math.round(n < 0 ? n : -n)));
      state.tabs[state.active].clipSkip = { enabled: n !== 0, layer };
    }
    save();
    renderGroups();
    flash();
    return true;
  };

  // One generation parameter (steps, cfg, sampler_name, scheduler) goes into the active tab's sampler settings.
  const applyParam = (widgetName, value) => {
    const field = PARAM_FIELDS[widgetName];
    const input = schema?.sampling?.find((i) => i.name === field);
    if (!input) return false;
    let next;
    if (input.kind === "combo") {
      if (typeof value !== "string" || !value.trim()) return false;
      const loose = input.values.filter((v) => looseName(v) === looseName(value)); // "Euler_Ancestral" -> euler_ancestral
      next = input.values.includes(value) ? value : loose.length === 1 ? loose[0] : value; // unknown names are kept and marked "(not found)"
    } else {
      const n = Number(value);
      if (value === null || value === "" || !Number.isFinite(n)) return false;
      next = normalizeNumber(input, n);
    }
    state.tabs[state.active].sampling[field] = next;
    save();
    renderGroups();
    flash();
    return true;
  };

  const applyLora = (text, mode) => {
    const tab = state.tabs[state.active];
    tab.lora = mode === "replace" ? String(text ?? "") : mergeLoraText(tab.lora ?? "", String(text ?? ""));
    save();
    renderGroups();
    flash();
  };

  // ----- public API -----
  const api_ = {
    root: outer,
    applyFile,
    applyClipSkip,
    applyParam,
    applyLora,
    onSchema() {
      fillDefaults(state);
      renderGroups();
      save();
    },
    reload() {
      state = normalizeState(node.properties?.[PROP]);
      appendCb.checked = state.importAppend;
      fillDefaults(state);
      renderTabs();
      renderGroups();
      syncSelection(); // keep the prompt input in step with the loaded tab, without marking the workflow as changed
    },
    dispose() {
      sizeObserver?.disconnect();
      closePopup();
      editors.delete(api_);
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
    },
  };
  editors.add(api_);

  renderTabs();
  fillDefaults(state);
  renderGroups();
  save();
  return api_;
}

app.registerExtension({
  name: "MultiTabText.Models",

  async setup() {
    loadSchema();
    installLoraManagerBridge();
  },

  async afterConfigureGraph() {
    scheduleRegistryRefresh();
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;
    injectCSS();

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      const selectionWidget = this.widgets?.find((w) => w.name === SELECTION_WIDGET);
      if (!selectionWidget) return r;
      hideWidget(selectionWidget); // edited through the tab editor instead
      try {
        const editor = createEditor(this, selectionWidget);
        this.addDOMWidget("editor", "mtm_editor", editor.root, {
          serialize: false,
          hideOnZoom: false,
          getMinHeight: () => 340,
        });
        this._mtm = editor;
        this.setSize([540, 800]);
        loadSchema();
        scheduleRegistryRefresh();
      } catch (err) {
        console.error("[MultiTabText] failed to create the MultiTabModels editor:", err);
      }
      return r;
    };

    // A saved workflow is loading: node.properties already holds the tabs, rebuild the editor from them.
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      this._mtm?.reload();
      scheduleRegistryRefresh();
      return r;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this._mtm?.dispose?.();
      scheduleRegistryRefresh();
      return onRemoved?.apply(this, arguments);
    };
  },
});
