"""
Model detection and prompt templating for MultiTabText.

The editor text may use model placeholders and @if / @elseif / @else / @endif blocks:

    @if %model_type% == sdxl
    masterpiece, best quality, very aesthetic
    @elseif %model_type% == flux
    a highly detailed photo
    @else
    {{a photo|a painting}}
    @endif

Where the values come from (no sockets are added to the node): ComfyUI hands the whole API prompt to
nodes that declare the hidden `PROMPT` input. This module walks that graph from the node, finds the
checkpoint / diffusion model that is closest to it (usually the one feeding the sampler that consumes
the text) and exposes it as placeholders:

    %model% / %model_name%    file name without extension (same idea as SaveImageLM's %model%)
    %model_file%              file name with extension
    %model_path%              path as stored in the workflow (may contain folders)
    %model_type%              detected type: sdxl, pony, illustrious, noobai, sd15, sd3, sd35, flux,
                              flux2, flux3, anima, moss, krea2, wan, ... (or "unknown" / empty when
                              there is no model)
    %model_family%            broader family (pony -> sdxl, illustrious -> sdxl, ...)
    %model_type_label%        readable label ("SDXL", "Flux", "MOSS Image", ...)
    %model_base% / %base_model%  ComfyUI-Lora-Manager's base model string when it is installed,
                              otherwise the detected label
    %model_title% / %model_version%  Civitai model / version names from LoRA Manager (empty without it)
    %model_detected_by%       lora-manager | filename | clip-type | loader-hint | none
    %ckpt%, %unet%, %clip%, %vae%  the matching loader file names (without extension)
    %loras%                   LoRA names found in the workflow (comma separated)

If ComfyUI-Lora-Manager (https://github.com/willmiao/ComfyUI-Lora-Manager) is installed, its scanner
cache is asked first: the exact base model (the same data SaveImageLM's %model% support uses) wins over
the file name. A base model that is too new for the built-in table is lowercased into a type slug, so
LoRA Manager keeps driving %model_type% even before this package is updated. Without a LoRA Manager
entry, the file name and its folders are inspected, then the CLIP loader's `type`, and finally the
CLIP / VAE file names are combined for families whose text encoder and VAE identify them even when the
model file was renamed (MOSS Image, Anima). Nothing is downloaded or installed; when LoRA Manager is
missing only a console warning is printed, and only if one of its placeholders is actually used.

The helper functions are pure (except for the optional LoRA Manager lookup and warning output), so they
can be used and tested outside ComfyUI.
"""

import fnmatch
import functools
import json
import os
import re
import sys

_DEBUG = os.environ.get("MULTITABTEXT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Warnings are printed once per reason, not once per queue, so a long batch does not flood the console.
_WARNED = set()


def _debug(message):
    if _DEBUG:
        print("[MultiTabText] " + message, flush=True)


def _warn_once(key, message):
    if key in _WARNED:
        return
    _WARNED.add(key)
    print("[MultiTabText] WARNING: " + message, flush=True)


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")

# Every known placeholder, with an empty value: unknown names are therefore easy to tell apart.
_EMPTY_VALUES = {
    "model": "",
    "model_name": "",
    "model_file": "",
    "model_path": "",
    "model_kind": "",
    "model_type": "",
    "model_family": "",
    "model_type_label": "",
    "model_base": "",
    "base_model": "",
    "model_title": "",
    "model_version": "",
    "model_detected_by": "",
    "ckpt": "",
    "unet": "",
    "clip": "",
    "vae": "",
    "loras": "",
}

_MODEL_VARS = {
    "model", "model_name", "model_file", "model_path", "model_kind", "model_type", "model_family",
    "model_type_label", "model_base", "base_model", "model_title", "model_version", "model_detected_by",
    "ckpt", "unet", "clip", "vae",
}

# These need ComfyUI-Lora-Manager for their exact value (they fall back / stay empty without it).
_LORA_MANAGER_VARS = {"model_base", "base_model", "model_title", "model_version"}


def uses_prompt_data(text: str) -> bool:
    """True when the text contains a placeholder or a conditional, i.e. its output may depend on the graph."""
    if not text:
        return False
    if _VAR_RE.search(text):
        return True
    low = text.casefold()
    return any(tag in low for tag in ("@if", "@elseif", "@elif", "@else", "@endif", "@end"))


class PromptContext:
    """Values for one run plus the bookkeeping needed for warnings."""

    def __init__(self, values=None):
        self.values = dict(_EMPTY_VALUES)
        if values:
            for key, value in values.items():
                key = str(key).casefold()
                if key in self.values:
                    self.values[key] = "" if value is None else str(value)
        self.available = False        # the workflow prompt was available
        self.model_found = False      # a checkpoint / diffusion model was found
        self.lm_available = False     # ComfyUI-Lora-Manager's checkpoint scanner is usable
        self.requested = set()        # placeholders that were actually used
        self.unknown = set()          # placeholders that do not exist (kept as literal text)

    def value(self, name: str) -> str:
        key = str(name).casefold()
        self.requested.add(key)
        if key in self.values:
            return self.values[key]
        self.unknown.add(key)
        return ""

    def fingerprint(self) -> str:
        """A stable string for IS_CHANGED: it only changes when the detected values change."""
        return json.dumps(
            {"values": self.values, "model": self.model_found, "lm": self.lm_available},
            sort_keys=True, ensure_ascii=False,
        )

    def warn(self):
        if self.unknown:
            names = ", ".join("%" + name + "%" for name in sorted(self.unknown))
            _warn_once("unknown:" + names, "unknown placeholder(s) " + names + " left as text")
        if self.requested & _MODEL_VARS:
            if not self.available:
                _warn_once("no-prompt", "the workflow prompt is not available here, so model placeholders are empty "
                                        "(this needs a ComfyUI version that passes the hidden PROMPT input)")
            elif not self.model_found:
                _warn_once("no-model", "no checkpoint or diffusion model was found in this workflow, so the model "
                                       "placeholders are empty")
        if self.requested & _LORA_MANAGER_VARS and not self.lm_available:
            _warn_once("no-lora-manager",
                       "ComfyUI-Lora-Manager was not found. %model_base% falls back to the detected type and "
                       "%model_title% / %model_version% stay empty. Install LoRA Manager for exact base model "
                       "data; nothing is downloaded automatically.")


_EMPTY_CONTEXT = PromptContext()


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

_MODEL_EXTENSIONS = (
    ".safetensors", ".sft", ".ckpt", ".pt", ".pt2", ".bin", ".pth", ".pkl", ".gguf", ".onnx", ".engine",
)


def _graph(prompt):
    if not isinstance(prompt, dict):
        return {}
    return {str(key): value for key, value in prompt.items() if isinstance(value, dict)}


def _link_source(value):
    """The source node id of a prompt link such as ["12", 0], or None."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        source = value[0]
        if isinstance(source, (str, int)) and not isinstance(source, bool):
            return str(source)
    return None


def _neighbours(graph):
    """Undirected adjacency: the text node is usually NOT downstream of the model loader, it is upstream of
    the sampler, so the distance to the loader has to be measured through the whole graph."""
    adjacency = {node_id: set() for node_id in graph}
    for node_id, node in graph.items():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            source = _link_source(value)
            if source is not None and source in adjacency:
                adjacency[node_id].add(source)
                adjacency[source].add(node_id)
    return adjacency


def _forward_adjacency(graph):
    """Directed adjacency (source -> consumer), used to find the sampler that consumes this text."""
    forward = {node_id: set() for node_id in graph}
    for node_id, node in graph.items():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            source = _link_source(value)
            if source is not None and source in forward:
                forward[source].add(node_id)
    return forward


def _bfs(start, adjacency):
    if start is None or start not in adjacency:
        return {}
    seen = {start: 0}
    queue = [start]
    head = 0
    while head < len(queue):
        node_id = queue[head]
        head += 1
        for neighbour in adjacency.get(node_id, ()):
            if neighbour not in seen:
                seen[neighbour] = seen[node_id] + 1
                queue.append(neighbour)
    return seen


def _distances(graph, start):
    return _bfs(start, _neighbours(graph))


# Inputs that carry a model (directly or inside a pipe) on the way to the sampler.
_MODEL_LINK_FIELDS = ("model", "guider", "basic_pipe", "model_pipe")


_MODELISH_FIELDS = ("model", "unet", "pipe", "guider", "network")


def _upstream_model_links(graph, node):
    """Model-carrying input links of one node, preferred field names first."""
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return []
    links = []
    for field in _MODEL_LINK_FIELDS:
        source = _link_source(inputs.get(field))
        if source is not None:
            links.append(source)
    for field in sorted(inputs):
        source = _link_source(inputs[field])
        if source is None or source in links:
            continue
        low = field.casefold()
        # Model merges / patches name their inputs model1, model2, base_model, ... and custom nodes may
        # use their own names. Follow them when the source really produces a MODEL.
        if any(word in low for word in _MODELISH_FIELDS) or _has_model_output(
                str((graph.get(source) or {}).get("class_type") or "")):
            links.append(source)
    return links


def _trace_model_loader(graph, start):
    """
    Follow `model` / `guider` / pipe links upstream from a sampler until a loader is found.
    A breadth-first search keeps the result deterministic and also gets through model merges / patches
    (whose inputs are called `model1`, `model2`, ...). Returns (node_id, node, model_info) or None.
    """
    if start is None or start not in graph:
        return None
    queue = [start]
    seen = set()
    head = 0
    while head < len(queue):
        node_id = queue[head]
        head += 1
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        info = _inspect_node(node)
        if info and info["models"]:
            for model in info["models"]:
                if model.get("primary", True):
                    return node_id, node, model
            return node_id, node, info["models"][0]
        for source in _upstream_model_links(graph, node):
            if source not in seen:
                queue.append(source)
    return None



def _as_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _stem(value) -> str:
    """File name without a known model extension (folders are dropped)."""
    name = os.path.basename(str(value).replace("\\", "/"))
    base, extension = os.path.splitext(name)
    return base if extension.casefold() in _MODEL_EXTENSIONS else name


def _basename(value) -> str:
    return os.path.basename(str(value).replace("\\", "/"))


def _looks_like_model_file(value) -> bool:
    text = _as_text(value)
    if not text or "\n" in text:
        return False
    return text.casefold().endswith(_MODEL_EXTENSIONS)


# Node classes that return a model but are not the generation model (or are not loaders at all).
_EXCLUDED_CLASS_HINTS = (
    "controlnet", "control_net", "upscale", "clipvision", "clip_vision", "stylemodel", "style_model",
    "gligen", "photomaker", "ipadapter", "instantid", "pulid", "lora", "hypernetwork", "sampler", "guider",
)

_CKPT_FIELDS = ("ckpt_name", "checkpoint_name", "checkpoint")
_UNET_FIELDS = ("unet_name", "diffusion_model_name", "gguf_name", "model_path")
_GENERIC_FIELDS = ("model_name", "model_filename", "model_file", "model")
_CLIP_FIELDS = ("clip_name", "clip_name1")
_VAE_FIELDS = ("vae_name",)


@functools.lru_cache(maxsize=128)
def _return_types(class_type: str):
    """ComfyUI's RETURN_TYPES for a node class (empty outside ComfyUI or for unknown classes)."""
    try:
        import importlib
        mapping = importlib.import_module("nodes").NODE_CLASS_MAPPINGS
        cls = mapping.get(class_type)
        types = getattr(cls, "RETURN_TYPES", None)
        return tuple(str(t) for t in types) if types else ()
    except Exception:
        return ()


def _has_model_output(class_type: str) -> bool:
    types = _return_types(class_type)
    if types:
        if "MODEL" not in types:
            return False
        # A model output alone is not enough: upscalers / control nets / CLIP vision also have one.
        return not any(t in ("UPSCALE_MODEL", "CONTROL_NET", "CLIP_VISION", "STYLE_MODEL", "GLIGEN", "PHOTOMAKER")
                       for t in types)
    low = class_type.casefold()
    return any(word in low for word in ("unet", "diffusion", "dit"))


def _selection(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_comments(text):
    """The editor's comment rules (imported lazily: nodes.py imports this module at start-up)."""
    try:
        from .nodes import strip_comments
    except Exception:
        try:
            from nodes import strip_comments
        except Exception:
            return text
    return strip_comments(text)


def _lora_tags(text):
    return [match.group(1).strip() for match in re.finditer(r"<lora:([^:>]+):", _strip_comments(text or ""))]


def _inspect_node(node):
    """
    Returns {"models": [{"kind", "field", "value", "primary"}], "clip", "vae", "clip_type", "loras"}
    for one prompt node, or None when the node holds nothing useful.
    """
    class_type = str(node.get("class_type") or "")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or not class_type:
        return None

    found = {"models": [], "clip": "", "vae": "", "clip_type": "", "loras": []}

    if class_type == "MultiTabModels":
        # This package's own loader keeps the active tab's choices in a JSON widget.
        selection = _selection(inputs.get("selection"))
        groups = {key: selection.get(key) if isinstance(selection.get(key), dict) else {}
                  for key in ("ckpt", "unet", "clip", "vae")}
        checkpoint = _as_text(groups["ckpt"].get("ckpt_name"))
        diffusion = _as_text(groups["unet"].get("unet_name"))
        found["clip"] = _as_text(groups["clip"].get("clip_name"))
        found["vae"] = _as_text(groups["vae"].get("vae_name"))
        found["clip_type"] = _as_text(groups["clip"].get("type"))
        if checkpoint:
            found["models"].append({"kind": "checkpoint", "field": "ckpt_name", "value": checkpoint,
                                    "primary": not bool(diffusion)})
        if diffusion:
            found["models"].append({"kind": "diffusion model", "field": "unet_name", "value": diffusion,
                                    "primary": True})
        found["loras"] = _lora_tags(selection.get("lora"))
        return found

    low = class_type.casefold()
    if not any(hint in low for hint in _EXCLUDED_CLASS_HINTS):
        for kind, fields in (("checkpoint", _CKPT_FIELDS), ("diffusion model", _UNET_FIELDS)):
            for field in fields:
                value = _as_text(inputs.get(field))
                if value:
                    found["models"].append({"kind": kind, "field": field, "value": value, "primary": True})
                    break
            if found["models"]:
                break
        if not found["models"] and _has_model_output(class_type):
            for field in _GENERIC_FIELDS:
                value = _as_text(inputs.get(field))
                if value and _looks_like_model_file(value):
                    found["models"].append({"kind": "diffusion model", "field": field, "value": value,
                                            "primary": True})
                    break

    for field in _CLIP_FIELDS:
        value = _as_text(inputs.get(field))
        if value:
            found["clip"] = value
            break
    for field in _VAE_FIELDS:
        value = _as_text(inputs.get(field))
        if value:
            found["vae"] = value
            break
    for field in ("type", "clip_type"):
        if "clip" in low or "dual" in low or "triple" in low:
            value = _as_text(inputs.get(field))
            if value:
                found["clip_type"] = value
                break
    for field in ("lora_name",):
        value = _as_text(inputs.get(field))
        if value:
            found["loras"].append(value)
    return found


def detect_models(prompt, node_id=None):
    """
    Pick the model that belongs to this node: the nearest checkpoint / diffusion model loader in the graph.
    Returns {"primary", "ckpt", "unet", "clip", "vae", "clip_type", "loras", "ambiguous"}.
    """
    result = {"primary": None, "ckpt": "", "unet": "", "clip": "", "vae": "", "clip_type": "", "loras": [],
              "ambiguous": False, "nodes": 0}
    graph = _graph(prompt)
    result["nodes"] = len(graph)
    if not graph:
        return result

    start = None if node_id is None else str(node_id)
    distances = _distances(graph, start)
    forward = _forward_adjacency(graph)
    forward_distances = _bfs(start, forward) if start in forward else {}
    order = {name: index for index, name in enumerate(graph)}

    candidates = []
    roles = {"clip": [], "vae": [], "clip_type": []}
    loras = []
    for name, node in graph.items():
        info = _inspect_node(node)
        if not info:
            continue
        distance = distances.get(name, 10 ** 6)
        position = order[name]
        for model in info["models"]:
            candidates.append({
                "kind": model["kind"], "field": model["field"], "value": model["value"],
                "primary": model.get("primary", True), "node_id": name,
                "class_type": str(node.get("class_type") or ""), "distance": distance, "order": position,
            })
        for key in roles:
            if info[key]:
                roles[key].append((distance, position, info[key]))
        loras.extend((distance, position, lora) for lora in info["loras"])

    def nearest(items):
        return min(items)[2] if items else ""

    result["clip"] = nearest(roles["clip"])
    result["vae"] = nearest(roles["vae"])
    result["clip_type"] = nearest(roles["clip_type"])

    ordered = sorted(loras)
    seen = set()
    for _, _, name in ordered:
        key = _stem(name).casefold()
        if key and key not in seen:
            seen.add(key)
            result["loras"].append(_stem(name))

    # Primary model: start at the sampler(s) this text feeds (or at the nearest node that consumes a model)
    # and follow the MODEL links upstream. That way a checkpoint that is only connected to the text encoder's
    # `clip` input (e.g. used for CLIP/VAE while a diffusion model supplies the model) is not picked by mistake.
    starts = [name for name in graph
              if name != start
              and name in forward_distances
              and isinstance(graph[name].get("inputs"), dict)
              and any(_link_source(graph[name]["inputs"].get(field)) is not None for field in _MODEL_LINK_FIELDS)]
    starts.sort(key=lambda name: (forward_distances.get(name, 10 ** 6), order[name]))

    traced = []
    for name in starts:
        hit = _trace_model_loader(graph, name)
        if hit:
            loader_id, loader_node, model = hit
            traced.append({
                "kind": model["kind"], "field": model["field"], "value": model["value"],
                "primary": model.get("primary", True), "node_id": loader_id,
                "class_type": str(loader_node.get("class_type") or ""),
                "distance": distances.get(loader_id, 10 ** 6), "order": order.get(loader_id),
                "via": name, "via_distance": forward_distances.get(name, 10 ** 6),
            })

    if traced:
        traced.sort(key=lambda item: (item["via_distance"], item["order"]))
        result["primary"] = traced[0]
        result["ambiguous"] = any(
            item["via_distance"] == traced[0]["via_distance"]
            and item["value"].casefold() != traced[0]["value"].casefold()
            for item in traced[1:]
        )
    else:
        # Fallback (text not connected to a sampler, or an unusual custom sampler): nearest loader in the graph.
        primary_pool = [item for item in candidates if item["primary"]] or candidates
        primary_pool.sort(key=lambda item: (item["distance"], item["order"]))
        if primary_pool:
            result["primary"] = primary_pool[0]
            result["ambiguous"] = (
                len(primary_pool) > 1
                and primary_pool[0]["distance"] == primary_pool[1]["distance"]
                and primary_pool[0]["value"].casefold() != primary_pool[1]["value"].casefold()
            )

    # %ckpt% / %unet% report the nearest loader of each kind, so an unrelated far-away loader does not win
    # just because it happens to appear first in the prompt.
    for kind, key in (("checkpoint", "ckpt"), ("diffusion model", "unet")):
        pool = [item for item in candidates if item["kind"] == kind]
        if pool:
            pool.sort(key=lambda item: (item["distance"], item["order"]))
            result[key] = pool[0]["value"]

    if _DEBUG:
        primary = result["primary"]
        _debug(
            "model detection: nodes={} start={} model={} ({}) ambiguous={} ckpt={} unet={} clip={} vae={} loras={}".format(
                result["nodes"], start, primary["value"] if primary else "-",
                primary["class_type"] if primary else "-", result["ambiguous"],
                result["ckpt"] or "-", result["unet"] or "-", result["clip"] or "-", result["vae"] or "-",
                ",".join(result["loras"]) or "-",
            )
        )
    return result


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


# Ordered: the first matching rule wins, so the specific families come before the generic ones.
_NAME_RULES = (
    # New architectures whose names may otherwise be caught by an older generic rule.
    (r"moss(?:[\s._-]?(?:image|img))?(?![a-z])", "moss", "moss", "MOSS Image"),
    (r"anima(?![a-z])", "anima", "anima", "Anima"),
    (r"flux[\s._-]?3(?!\d)", "flux3", "flux", "Flux 3 Video"),
    (r"flux[\s._-]?2|flux2", "flux2", "flux", "Flux 2"),
    (r"flux|f1[ds](?![a-z0-9])", "flux", "flux", "Flux"),
    (r"krea[\s._-]?2(?!\d)", "krea2", "krea2", "Krea 2"),
    # Pony V7 is AuraFlow. Keep the "pony" type (prompts still use pony tags), but expose the family.
    (r"pony[^/]*?v7(?![a-z0-9])", "pony", "auraflow", "Pony V7"),
    (r"pony", "pony", "sdxl", "Pony"),
    (r"illustrious", "illustrious", "sdxl", "Illustrious"),
    (r"noobai", "noobai", "sdxl", "NoobAI"),
    (r"animagine", "animagine", "sdxl", "Animagine"),
    (r"sd[\s._-]?3[\s._-]?5|sd35", "sd35", "sd35", "SD 3.5"),
    (r"sd[\s._-]?3|stable[\s._-]?diffusion[\s._-]?3", "sd3", "sd3", "SD 3"),
    (r"sdxl|sd[\s._-]?xl|xl(?![a-z])", "sdxl", "sdxl", "SDXL"),
    (r"sd[\s._-]?v?2[\s._-]?1|sd21", "sd21", "sd21", "SD 2.1"),
    (r"sd[\s._-]?v?1[\s._-]?5|sd15|v1[\s._-]?5|stable[\s._-]?diffusion[\s._-]?1", "sd15", "sd15", "SD 1.5"),
    (r"cascade", "cascade", "cascade", "Stable Cascade"),
    (r"hidream[\s._-]?o1", "hidream_o1", "hidream", "HiDream-O1"),
    (r"hidream", "hidream", "hidream", "HiDream"),
    (r"ltx", "ltxv", "ltxv", "LTX Video"),
    (r"wan[\s._-]?image", "wanimage", "wan", "Wan Image"),
    (r"wan(?:video|\d)|wanvideo|(?<![a-z])wan(?![a-z])", "wan", "wan", "Wan Video"),
    (r"hunyuan", "hunyuan", "hunyuan", "Hunyuan"),
    (r"qwen[\s._-]?2(?!\d)", "qwen2", "qwen", "Qwen 2"),
    (r"qwen", "qwen", "qwen", "Qwen Image"),
    (r"chroma", "chroma", "chroma", "Chroma"),
    (r"auraflow", "auraflow", "auraflow", "AuraFlow"),
    (r"kolors", "kolors", "kolors", "Kolors"),
    (r"lumina", "lumina", "lumina", "Lumina"),
    (r"mochi", "mochi", "mochi", "Mochi"),
    (r"cogvideo", "cogvideox", "cogvideox", "CogVideoX"),
    (r"pixart", "pixart", "pixart", "PixArt"),
    (r"playground", "playground", "sdxl", "Playground"),
    (r"stable[\s._-]?video|\bsvd\b", "svd", "svd", "SVD"),
    (r"z[\s._-]?image|zimage", "zimage", "zimage", "Z-Image"),
    (r"krea", "krea", "krea", "Krea"),
    # Recent LoRA Manager / Civitai base-model families.
    (r"nucleus", "nucleus", "nucleus", "Nucleus"),
    (r"boogu", "boogu", "boogu", "Boogu"),
    (r"ernie[\s._-]?turbo", "ernie_turbo", "ernie", "Ernie Turbo"),
    (r"ernie", "ernie", "ernie", "Ernie"),
    (r"grok", "grok", "grok", "Grok"),
    (r"happy[\s._-]?horse|happyhorse", "happyhorse", "happyhorse", "HappyHorse"),
    (r"ideogram", "ideogram", "ideogram", "Ideogram"),
    (r"ming[\s._-]?image|(?<![a-z])ming(?![a-z])", "ming", "ming", "Ming Image"),
    (r"minimax", "minimax", "minimax", "MiniMax"),
    (r"(?<![a-z])lens(?![a-z])", "lens", "lens", "Lens"),
    (r"(?<![a-z])mai(?![a-z])", "mai", "mai", "MAI"),
    (r"ace[\s._-]?audio|(?<![a-z])ace(?![a-z])", "ace", "ace", "ACE Audio"),
    # Other model families already supported by current ComfyUI CLIP loaders.
    (r"omnigen", "omnigen2", "omnigen2", "OmniGen2"),
    (r"(?<![a-z])ovis(?![a-z])", "ovis", "ovis", "Ovis"),
    (r"longcat", "longcat", "longcat", "LongCat Image"),
    (r"pixel[\s._-]?dit|pixeldit", "pixeldit", "pixeldit", "PixelDiT"),
    (r"joy[\s._-]?image|joyimage", "joyimage", "joyimage", "JoyImage"),
    (r"(?<![a-z])mage(?![a-z])", "mage", "mage", "Mage"),
    (r"yue[\s._-]?2(?!\d)", "yue2", "yue2", "Yue2"),
    (r"fibo", "fibo2", "fibo2", "Fibo 2"),
    (r"newbie", "newbie", "newbie", "Newbie"),
    (r"kandinsky", "kandinsky5", "kandinsky5", "Kandinsky 5"),
)

_NAME_RULES_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE), slug, family, label)
    for pattern, slug, family, label in _NAME_RULES
)

# LoRA Manager's `base_model` strings (Civitai names) -> (type, family, label).
_BASE_MODEL_RULES = (
    ("moss", "moss", "moss", "MOSS Image"),
    ("anima", "anima", "anima", "Anima"),
    ("flux 3", "flux3", "flux", "Flux 3 Video"),
    ("flux.2", "flux2", "flux", "Flux 2"),
    ("flux", "flux", "flux", "Flux"),
    ("krea 2", "krea2", "krea2", "Krea 2"),
    ("pony diffusion v7", "pony", "auraflow", "Pony V7"),
    ("pony v7", "pony", "auraflow", "Pony V7"),
    ("pony", "pony", "sdxl", "Pony"),
    ("illustrious", "illustrious", "sdxl", "Illustrious"),
    ("noobai", "noobai", "sdxl", "NoobAI"),
    ("animagine", "animagine", "sdxl", "Animagine"),
    ("sd 3.5", "sd35", "sd35", "SD 3.5"),
    ("sd 3", "sd3", "sd3", "SD 3"),
    ("sdxl", "sdxl", "sdxl", "SDXL"),
    ("sd 2", "sd21", "sd21", "SD 2.1"),
    ("sd 1", "sd15", "sd15", "SD 1.5"),
    ("stable cascade", "cascade", "cascade", "Stable Cascade"),
    ("hidream-o1", "hidream_o1", "hidream", "HiDream-O1"),
    ("hidream o1", "hidream_o1", "hidream", "HiDream-O1"),
    ("hidream", "hidream", "hidream", "HiDream"),
    ("ltx", "ltxv", "ltxv", "LTX Video"),
    ("wan image", "wanimage", "wan", "Wan Image"),
    ("wan", "wan", "wan", "Wan Video"),
    ("hunyuan", "hunyuan", "hunyuan", "Hunyuan"),
    ("qwen 2", "qwen2", "qwen", "Qwen 2"),
    ("qwen", "qwen", "qwen", "Qwen Image"),
    ("chroma", "chroma", "chroma", "Chroma"),
    ("auraflow", "auraflow", "auraflow", "AuraFlow"),
    ("kolors", "kolors", "kolors", "Kolors"),
    ("lumina", "lumina", "lumina", "Lumina"),
    ("mochi", "mochi", "mochi", "Mochi"),
    ("cogvideo", "cogvideox", "cogvideox", "CogVideoX"),
    ("pixart", "pixart", "pixart", "PixArt"),
    ("playground", "playground", "sdxl", "Playground"),
    ("svd", "svd", "svd", "SVD"),
    ("zimage", "zimage", "zimage", "Z-Image"),
    ("krea", "krea", "krea", "Krea"),
    # Recent LoRA Manager / Civitai base-model families.
    ("nucleus", "nucleus", "nucleus", "Nucleus"),
    ("boogu", "boogu", "boogu", "Boogu"),
    ("ernie turbo", "ernie_turbo", "ernie", "Ernie Turbo"),
    ("ernie", "ernie", "ernie", "Ernie"),
    ("grok", "grok", "grok", "Grok"),
    ("happyhorse", "happyhorse", "happyhorse", "HappyHorse"),
    ("happy horse", "happyhorse", "happyhorse", "HappyHorse"),
    ("ideogram", "ideogram", "ideogram", "Ideogram"),
    ("ming image", "ming", "ming", "Ming Image"),
    ("minimax", "minimax", "minimax", "MiniMax"),
    ("lens", "lens", "lens", "Lens"),
    ("mai", "mai", "mai", "MAI"),
    ("ace audio", "ace", "ace", "ACE Audio"),
)

# CLIPLoader's `type` option -> (type, family, label).
_CLIP_TYPE_RULES = {
    "stable_diffusion": ("sd15", "sd15", "SD 1.5"),
    "sd1": ("sd15", "sd15", "SD 1.5"),
    "sdxl": ("sdxl", "sdxl", "SDXL"),
    "sd3": ("sd3", "sd3", "SD 3"),
    "stable_cascade": ("cascade", "cascade", "Stable Cascade"),
    "flux": ("flux", "flux", "Flux"),
    "flux2": ("flux2", "flux", "Flux 2"),
    "flux3": ("flux3", "flux", "Flux 3 Video"),
    "moss": ("moss", "moss", "MOSS Image"),
    "moss_image": ("moss", "moss", "MOSS Image"),
    "anima": ("anima", "anima", "Anima"),
    "krea2": ("krea2", "krea2", "Krea 2"),
    "krea_2": ("krea2", "krea2", "Krea 2"),
    "wan": ("wan", "wan", "Wan Video"),
    "wan_image": ("wanimage", "wan", "Wan Image"),
    "hunyuan_video": ("hunyuan", "hunyuan", "Hunyuan"),
    "hunyuan_video_15": ("hunyuan", "hunyuan", "Hunyuan"),
    "hunyuan_image": ("hunyuan", "hunyuan", "Hunyuan"),
    "ltxv": ("ltxv", "ltxv", "LTX Video"),
    "qwen_image": ("qwen", "qwen", "Qwen Image"),
    "qwen2": ("qwen2", "qwen", "Qwen 2"),
    "hidream": ("hidream", "hidream", "HiDream"),
    "hidream_o1": ("hidream_o1", "hidream", "HiDream-O1"),
    "chroma": ("chroma", "chroma", "Chroma"),
    "lumina2": ("lumina", "lumina", "Lumina"),
    "mochi": ("mochi", "mochi", "Mochi"),
    "cosmos": ("cosmos", "cosmos", "Cosmos"),
    "pixart": ("pixart", "pixart", "PixArt"),
    "aura": ("auraflow", "auraflow", "AuraFlow"),
    "boogu": ("boogu", "boogu", "Boogu"),
    "ernie": ("ernie", "ernie", "Ernie"),
    "nucleus": ("nucleus", "nucleus", "Nucleus"),
    "lens": ("lens", "lens", "Lens"),
    "mai": ("mai", "mai", "MAI"),
    "ideogram": ("ideogram", "ideogram", "Ideogram"),
    "ideogram4": ("ideogram", "ideogram", "Ideogram"),
    "ace": ("ace", "ace", "ACE Audio"),
    "omnigen2": ("omnigen2", "omnigen2", "OmniGen2"),
    "ovis": ("ovis", "ovis", "Ovis"),
    "longcat_image": ("longcat", "longcat", "LongCat Image"),
    "pixeldit": ("pixeldit", "pixeldit", "PixelDiT"),
    "joyimage": ("joyimage", "joyimage", "JoyImage"),
    "mage": ("mage", "mage", "Mage"),
    "minimax": ("minimax", "minimax", "MiniMax"),
    "yue2": ("yue2", "yue2", "Yue2"),
    "fibo2": ("fibo2", "fibo2", "Fibo 2"),
    "newbie": ("newbie", "newbie", "Newbie"),
    "kandinsky5": ("kandinsky5", "kandinsky5", "Kandinsky 5"),
    "kandinsky5_image": ("kandinsky5", "kandinsky5", "Kandinsky 5"),
}


_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")


def _match_name(value):
    text = str(value or "").replace("\\", "/")
    # Split camelCase so names such as `AnimaBase`, `MOSSImage` and `qwen2Image` behave like
    # their separated spellings without matching unrelated words such as `animagine` or `mossy`.
    text = _CAMEL_BOUNDARY.sub(r"\1 \2", text)
    text = _CAMEL_ACRONYM.sub(r"\1 \2", text)
    for pattern, slug, family, label in _NAME_RULES_COMPILED:
        if pattern.search(text):
            return slug, family, label
    return None


def _match_base_model(value):
    low = str(value or "").casefold().strip()
    if not low or low in ("unknown", "other", "n/a", "none"):
        return None
    for needle, slug, family, label in _BASE_MODEL_RULES:
        if needle in low:
            return slug, family, label
    return None


def _base_model_fallback(value):
    """
    A LoRA Manager base model that is too new for `_BASE_MODEL_RULES` still gives a usable type,
    for example "Seedream 4.0" -> ("seedream_4_0", "seedream_4_0", "Seedream 4.0").
    """
    label = str(value or "").strip()
    family_slug = _slug(label)
    if not family_slug or label.casefold() in ("unknown", "other", "n/a", "none"):
        return None
    return family_slug, family_slug, label


def _match_clip_type(value):
    low = _slug(str(value or ""))
    if not low:
        return None
    if low in _CLIP_TYPE_RULES:
        return _CLIP_TYPE_RULES[low]
    return None


# These CLIPLoader types are too common to be a useful hint on their own. Anima, for example, uses
# `stable_diffusion` for its Qwen3 encoder, so a renamed Anima file would otherwise look like SD 1.5.
_GENERIC_CLIP_TYPES = {"stable_diffusion", "sd1"}

# Split-loader fingerprints for models whose text encoder / VAE pair identifies the family even when
# the diffusion model file was renamed (e.g. `model.safetensors`).
_MOSS_CLIP = re.compile(r"qwen[\s._-]?3[._-]?5(?!\d)")
_FLUX2_VAE = re.compile(r"flux[\s._-]?2[\s._-]?vae")
_ANIMA_CLIP = re.compile(r"qwen[\s._-]?3[\s._-]?(?:0?[._-]?6b|06b)")
_KREA2_CLIP = re.compile(r"qwen[\s._-]?3[\s._-]?vl[\s._-]?4b")
_QWEN_IMAGE_VAE = re.compile(r"qwen[\s._-]?image[\s._-]?vae")


def _match_loader_hint(clip_value, vae_value):
    """Combined CLIP + VAE file names -> (type, family, label), or None."""
    clip = str(clip_value or "").replace("\\", "/").casefold()
    vae = str(vae_value or "").replace("\\", "/").casefold()
    # MOSS Image: Qwen3.5 9B text encoder + FLUX.2 small (or full) VAE.
    if _MOSS_CLIP.search(clip) and _FLUX2_VAE.search(vae):
        return "moss", "moss", "MOSS Image"
    # Anima: Qwen3 0.6B text encoder + Qwen-Image VAE.
    if _ANIMA_CLIP.search(clip) and _QWEN_IMAGE_VAE.search(vae):
        return "anima", "anima", "Anima"
    # Krea 2: Qwen3-VL 4B text encoder + Qwen-Image VAE.
    if _KREA2_CLIP.search(clip) and _QWEN_IMAGE_VAE.search(vae):
        return "krea2", "krea2", "Krea 2"
    return None


def _resolve_type(file_value, clip_type, base_model, clip_value="", vae_value=""):
    """(slug, family, label) and how it was found."""
    if base_model:
        hit = _match_base_model(base_model)
        if hit:
            return hit, "lora-manager"
        # LoRA Manager may already know a family this table does not: keep using its value.
        hit = _base_model_fallback(base_model)
        if hit:
            return hit, "lora-manager"
    hit = _match_name(file_value)
    if hit:
        return hit, "filename"
    # A meaningful CLIP type is a strong hint. Generic types such as `stable_diffusion` are checked
    # last, after the combined CLIP / VAE fingerprint, because several new families reuse them.
    if clip_type and _slug(clip_type) not in _GENERIC_CLIP_TYPES:
        hit = _match_clip_type(clip_type)
        if hit:
            return hit, "clip-type"
    hit = _match_loader_hint(clip_value, vae_value)
    if hit:
        return hit, "loader-hint"
    if clip_type:
        hit = _match_clip_type(clip_type)
        if hit:
            return hit, "clip-type"
    return ("unknown", "unknown", "Unknown"), "none"


# ---------------------------------------------------------------------------
# Optional ComfyUI-Lora-Manager support (same data SaveImageLM uses for %model%)
# ---------------------------------------------------------------------------

def _service_registry():
    """LoRA Manager's ServiceRegistry, or None when the package is not loaded."""
    fallback = None
    for name, module in list(sys.modules.items()):
        low = name.casefold()
        # Do not inspect every module in the process: Torch's lazy `torch.classes` namespace raises
        # when an unknown attribute is read. LoRA Manager's modules always carry one of these names.
        if not (name.endswith(".services.service_registry") or name.endswith(".services")
                or "service_registry" in low or "lora" in low or "manager" in low):
            continue
        try:
            registry = getattr(module, "ServiceRegistry", None)
            getter = getattr(registry, "get_service_sync", None) if registry is not None else None
        except Exception:
            continue
        if not callable(getter):
            continue
        # Prefer the known locations, but do not depend on how the custom-node folder was named.
        if name.endswith(".services.service_registry") or name.endswith(".services") or "service_registry" in low:
            return registry
        if fallback is None:
            fallback = registry
    return fallback


def _lora_manager_scanners():
    registry = _service_registry()
    if registry is None:
        return []
    scanners = []
    for service_name in ("checkpoint_scanner", "model_scanner", "unet_scanner"):
        try:
            scanner = registry.get_service_sync(service_name)
        except Exception:
            scanner = None
        if scanner is not None and scanner not in scanners:
            scanners.append(scanner)
    return scanners


def _scanner_entries(scanner):
    """ModelScanner keeps its model list in `_cache.raw_data` (what SaveImageLM reads too)."""
    cache = getattr(scanner, "_cache", None)
    entries = getattr(cache, "raw_data", None)
    return entries if isinstance(entries, list) else []


def _lora_manager_lookup(file_value):
    """
    The scanner entry for a model file: {"base_model", "file_name", "sha256", "civitai", ...} or None.
    Returns (entry, scanner_available).
    """
    scanners = _lora_manager_scanners()
    if not scanners:
        return None, False
    wanted = str(file_value or "").replace("\\", "/")
    wanted_low = wanted.casefold()
    name_low = os.path.basename(wanted_low)
    stem_low = _stem(wanted).casefold()
    fallback = None
    for scanner in scanners:
        for entry in _scanner_entries(scanner):
            if not isinstance(entry, dict):
                continue
            file_name = str(entry.get("file_name") or "").casefold()
            file_path = str(entry.get("file_path") or "").replace("\\", "/").casefold()
            entry_name = os.path.basename(file_path)
            entry_stem = _stem(file_path).casefold()
            if file_path and (file_path == wanted_low or file_path.endswith("/" + wanted_low)):
                return entry, True
            if stem_low and stem_low in (file_name, entry_stem, _stem(entry_name).casefold()):
                if fallback is None:
                    fallback = entry
            elif name_low and name_low == entry_name:
                if fallback is None:
                    fallback = entry
    return fallback, True


# ---------------------------------------------------------------------------
# Building the context
# ---------------------------------------------------------------------------

def build_context(prompt, node_id=None) -> PromptContext:
    """Collect every placeholder value for this run. Never raises."""
    context = PromptContext()
    context.available = isinstance(prompt, dict) and bool(prompt)
    if not context.available:
        return context

    try:
        detection = detect_models(prompt, node_id)
    except Exception as exc:  # a graph we cannot read must never stop the node
        _debug("model detection failed: {!r}".format(exc))
        return context

    primary = detection["primary"]
    values = {}
    if primary:
        file_value = primary["value"]
        entry, lm_available = _lora_manager_lookup(file_value)
        context.lm_available = lm_available
        base_model = str((entry or {}).get("base_model") or "")
        if entry:
            _debug("LoRA Manager entry: file={!r} base_model={!r}".format(file_value, base_model))
        (type_slug, family, label), source = _resolve_type(
            file_value, detection["clip_type"], base_model, detection["clip"], detection["vae"])

        # Civitai model / version names (only LoRA Manager knows them).
        title = str((entry or {}).get("model_name") or "")
        version = str((entry or {}).get("version_name") or "")
        civitai = (entry or {}).get("civitai")
        if isinstance(civitai, dict):
            model_info = civitai.get("model")
            if not title and isinstance(model_info, dict):
                title = str(model_info.get("name") or "")
            if not version:
                version = str(civitai.get("name") or "")

        values.update({
            "model": _stem(file_value),
            "model_name": _stem(file_value),
            "model_file": _basename(file_value),
            "model_path": file_value,
            "model_kind": primary["kind"],
            "model_type": type_slug,
            "model_family": family,
            "model_type_label": label,
            "model_base": base_model or label,
            "base_model": base_model or label,
            "model_title": title,
            "model_version": version,
            "model_detected_by": source,
        })
        context.model_found = True
    else:
        # Still tell the warning logic whether LoRA Manager exists, even when no model could be found.
        context.lm_available = bool(_lora_manager_scanners())

    values["ckpt"] = _stem(detection["ckpt"]) if detection["ckpt"] else ""
    values["unet"] = _stem(detection["unet"]) if detection["unet"] else ""
    values["clip"] = _stem(detection["clip"]) if detection["clip"] else ""
    values["vae"] = _stem(detection["vae"]) if detection["vae"] else ""
    values["loras"] = ", ".join(detection["loras"])

    context.values.update(values)
    return context


# ---------------------------------------------------------------------------
# Replacing placeholders
# ---------------------------------------------------------------------------

def substitute_variables(text: str, context) -> str:
    """Replace %name% placeholders. Unknown ones are left exactly as they are (and reported once)."""
    if not text or "%" not in text:
        return text
    context = context or _EMPTY_CONTEXT

    def replace(match):
        name = match.group(1)
        if name.casefold() in context.values:
            return context.value(name)
        context.unknown.add(name.casefold())
        return match.group(0)

    return _VAR_RE.sub(replace, text)


# ---------------------------------------------------------------------------
# @if / @elseif / @else / @endif
# ---------------------------------------------------------------------------

_DIRECTIVE_RE = re.compile(r"^[ \t]*@([A-Za-z]+)\b(.*)$")

_WORD_OPERATORS = {"is", "contains", "starts", "ends", "in", "matches"}
_SYMBOL_OPERATORS = {"==", "=", "!=", "<>", "<", ">", "<=", ">=", "&&", "||", "!", "(", ")"}
_FALSY = {"", "0", "false", "no", "off", "none", "null", "unknown", "undefined"}

_TOKEN_RE = re.compile(
    r"""
        (?P<space>\s+)
      | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
      | (?P<var>%[A-Za-z_][A-Za-z0-9_]*%)
      | (?P<op>==|!=|<>|>=|<=|&&|\|\||[()<>=!])
      | (?P<word>[^\s()<>=!&|]+)
    """,
    re.VERBOSE,
)


class ConditionError(Exception):
    pass


def _tokenize(condition):
    tokens = []
    position = 0
    for match in _TOKEN_RE.finditer(condition):
        if match.start() != position:
            raise ConditionError("unexpected {!r}".format(condition[position:match.start()]))
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        tokens.append((kind, match.group()))
    if position != len(condition):
        raise ConditionError("unexpected {!r}".format(condition[position:]))
    return tokens


def _text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _normal(value):
    return _text(value).strip().casefold()


def _truthy(value):
    if isinstance(value, bool):
        return value
    return _normal(value) not in _FALSY


class _ConditionParser:
    def __init__(self, tokens, context):
        self.tokens = tokens
        self.index = 0
        self.context = context

    def peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return (None, None)

    def accept_symbol(self, *symbols):
        kind, value = self.peek()
        if kind == "op" and value in symbols:
            self.index += 1
            return value
        return None

    def accept_word(self, *words):
        kind, value = self.peek()
        if kind == "word" and value.casefold() in words:
            self.index += 1
            return value.casefold()
        return None

    def parse(self):
        result = self.parse_or()
        if self.index < len(self.tokens):
            raise ConditionError("unexpected {!r}".format(self.tokens[self.index][1]))
        return bool(result)

    def parse_or(self):
        result = bool(self.parse_and())
        while self.accept_symbol("||") or self.accept_word("or"):
            result = bool(self.parse_and()) or result
        return result

    def parse_and(self):
        result = bool(self.parse_not())
        while self.accept_symbol("&&") or self.accept_word("and"):
            result = bool(self.parse_not()) and result
        return result

    def parse_not(self):
        if self.accept_symbol("!") or self.accept_word("not"):
            return not self.parse_not()
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_value()
        kind, raw = self.peek()
        operator = None
        if kind == "op" and raw in _SYMBOL_OPERATORS - {"&&", "||", "!", "(", ")"}:
            self.index += 1
            operator = raw
        elif kind == "word":
            word = raw.casefold()
            negate = False
            if word == "not":
                self.index += 1
                kind2, raw2 = self.peek()
                if not (kind2 == "word" and raw2.casefold() in _WORD_OPERATORS):
                    raise ConditionError("'not' must be followed by contains, in, matches, starts with or ends with")
                word = raw2.casefold()
                self.index += 1
                negate = True
            elif word in _WORD_OPERATORS:
                self.index += 1
            else:
                raise ConditionError("unexpected {!r}".format(raw))
            if word in ("starts", "ends"):
                if not self.accept_word("with"):
                    raise ConditionError("'{}' must be followed by 'with'".format(word))
                word += " with"
            if word == "is" and self.accept_word("not"):
                operator = "is not"
            else:
                operator = ("not " if negate else "") + word
        if operator is None:
            return _truthy(left)

        if operator in ("in", "not in"):
            right = self.parse_value_list()
        else:
            right = self.parse_value()
        return self.compare(operator, left, right)

    def compare(self, operator, left, right):
        left_text = _normal(left)
        if operator in ("==", "=", "is"):
            return left_text == _normal(right)
        if operator in ("!=", "<>", "is not"):
            return left_text != _normal(right)
        if operator in ("contains", "not contains"):
            found = _normal(right) in left_text if _normal(right) else False
            return not found if operator.startswith("not ") else found
        if operator in ("starts with", "not starts with"):
            found = left_text.startswith(_normal(right))
            return not found if operator.startswith("not ") else found
        if operator in ("ends with", "not ends with"):
            found = left_text.endswith(_normal(right))
            return not found if operator.startswith("not ") else found
        if operator in ("matches", "not matches"):
            try:
                found = fnmatch.fnmatchcase(left_text, _normal(right))
            except Exception:
                found = False
            return not found if operator.startswith("not ") else found
        if operator in ("in", "not in"):
            found = any(left_text == _normal(choice) for choice in right)
            return not found if operator.startswith("not ") else found
        if operator in ("<", ">", "<=", ">="):
            try:
                left_number, right_number = float(left_text), float(_normal(right))
            except ValueError:
                left_number, right_number = left_text, _normal(right)
            if operator == "<":
                return left_number < right_number
            if operator == ">":
                return left_number > right_number
            if operator == "<=":
                return left_number <= right_number
            return left_number >= right_number
        raise ConditionError("unknown operator {!r}".format(operator))

    def parse_value(self):
        kind, raw = self.peek()
        if kind == "str":
            self.index += 1
            body = raw[1:-1]
            body = re.sub(r"\\(.)", r"\1", body)
            return substitute_variables(body, self.context)
        if kind == "var":
            self.index += 1
            return self.context.value(raw[1:-1])
        if kind == "word":
            self.index += 1
            return raw
        if kind == "op" and raw == "(":
            self.index += 1
            result = self.parse_or()
            if not self.accept_symbol(")"):
                raise ConditionError("missing ')'")
            return result
        raise ConditionError("expected a value, got {!r}".format(raw))

    def parse_value_list(self):
        """The right side of `in`: everything up to the end (or ')'), split on commas / |."""
        items = []
        depth = 0
        while self.index < len(self.tokens):
            kind, raw = self.peek()
            if kind == "op" and raw == "(":
                depth += 1
                self.index += 1
                continue
            if kind == "op" and raw == ")":
                if depth == 0:
                    break
                depth -= 1
                self.index += 1
                continue
            self.index += 1
            if kind == "var":
                items.append(self.context.value(raw[1:-1]))
            elif kind == "str":
                items.append(re.sub(r"\\(.)", r"\1", raw[1:-1]))
            else:
                items.append(raw)
        text = " ".join(items)
        return [part.strip() for part in re.split(r"[,|]", text) if part.strip()]


def evaluate_condition(condition: str, context) -> bool:
    tokens = _tokenize(condition or "")
    if not tokens:
        raise ConditionError("empty condition")
    return _ConditionParser(tokens, context or _EMPTY_CONTEXT).parse()


def _evaluate_safely(condition, context, line_number):
    try:
        return evaluate_condition(condition, context)
    except Exception as exc:
        _warn_once(
            "condition:" + condition.strip(),
            "could not read the condition {!r} on line {} ({}). Keeping that branch so no text is lost.".format(
                condition.strip(), line_number, exc),
        )
        return True


def apply_conditionals(text: str, context=None) -> str:
    """
    Evaluate @if / @elseif (@elif) / @else / @endif blocks. Directive lines are never part of the output;
    an unclosed block runs to the end of the text (with a warning).
    """
    if not text or "@" not in text:
        return text
    context = context or _EMPTY_CONTEXT
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]

    output = []
    stack = []  # each frame: parent (outer branches active), active, taken, line

    def active():
        return all(frame["active"] for frame in stack)

    for number, line in enumerate(text.split("\n"), 1):
        match = _DIRECTIVE_RE.match(line)
        if not match:
            if active():
                output.append(line)
            continue

        name = match.group(1).casefold()
        rest = match.group(2).strip()

        if name == "else" and rest.casefold().startswith("if "):
            name, rest = "elseif", rest[3:].strip()
        elif name in ("elif",):
            name = "elseif"
        elif name in ("end",):
            name = "endif"

        if name == "if":
            parent = active()
            condition = _evaluate_safely(rest, context, number) if parent else False
            stack.append({"parent": parent, "active": parent and bool(condition),
                          "taken": bool(condition) if parent else False, "line": number})
            continue

        if name in ("elseif", "else"):
            if not stack:
                _warn_once("stray:" + name, "found @{} without a matching @if (line {}); the line is ignored".format(
                    name, number))
                continue
            frame = stack[-1]
            if name == "elseif":
                if frame["taken"] or not frame["parent"]:
                    frame["active"] = False
                else:
                    condition = _evaluate_safely(rest, context, number)
                    frame["active"] = bool(condition)
                    frame["taken"] = bool(condition)
            else:
                frame["active"] = frame["parent"] and not frame["taken"]
                frame["taken"] = frame["taken"] or frame["parent"]
            continue

        if name == "endif":
            if not stack:
                _warn_once("stray:endif", "found @endif without a matching @if (line {}); the line is ignored".format(
                    number))
                continue
            stack.pop()
            continue

        # Not a directive we know: keep it as ordinary text (it may be part of the prompt).
        if active():
            output.append(line)

    if stack:
        _warn_once("unclosed", "@if on line {} has no @endif; its block runs to the end of the tab".format(
            stack[0]["line"]))

    return "\n".join(output)
