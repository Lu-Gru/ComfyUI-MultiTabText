"""
MultiTabModels - a tabbed model loader with sampler settings.

Every tab holds one choice for each of these standard ComfyUI nodes:

  * Load Checkpoint       (CheckpointLoaderSimple) -> MODEL + CLIP + VAE from ONE file
  * Load Diffusion Model  (UNETLoader)             -> MODEL
  * Load CLIP             (CLIPLoader)             -> CLIP
  * Load VAE              (VAELoader)              -> VAE

plus the sampler settings of ComfyUI's KSampler: steps (INT), cfg (FLOAT), sampler_name and scheduler, and a free
text field per tab (typically LoRA syntax such as <lora:name:0.8>) that is output as LORA. Comments in that text
(lines starting with # or //, and /* ... */ blocks) are removed from the output.

The ACTIVE tab is used. The editor (web/multi_tab_models.js) keeps all tabs in the node's properties and puts only
the active tab's choices into the hidden `selection` input, so editing / renaming / reordering the other tabs never
makes ComfyUI reload any model.

Combining the loaders: the checkpoint supplies MODEL, CLIP and VAE; a separately chosen Diffusion Model, CLIP or VAE
REPLACES the matching output. A group left on "(none)" is not loaded (and does not replace anything).

Loaded models are remembered (one entry per group, see _load_cached): when only steps, cfg, the sampler, the scheduler
or the LORA text change, ComfyUI re-runs this node, but the models are NOT read from disk again. The memory is
released when ComfyUI unloads models ("Free model and node cache").

The loaders are ComfyUI's own classes (looked up in NODE_CLASS_MAPPINGS), so behaviour and options are exactly those
of the standard nodes of the installed ComfyUI version. The sampler_name / scheduler outputs are typed with
ComfyUI's current sampler / scheduler lists, so they connect straight to a KSampler's sampler_name / scheduler inputs.
"""

import importlib
import inspect
import json
import os

from .nodes import strip_comments  # the same comment rules as the MultiTabText node

_DEBUG = os.environ.get("MULTITABTEXT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# group key -> (ComfyUI node class name, name of the input that selects the file)
LOADERS = {
    "ckpt": ("CheckpointLoaderSimple", "ckpt_name"),
    "unet": ("UNETLoader", "unet_name"),
    "clip": ("CLIPLoader", "clip_name"),
    "vae": ("VAELoader", "vae_name"),
}
GROUP_KEYS = tuple(LOADERS) + ("sampling",)

# Where ComfyUI keeps the files of each group (used to notice a file that was replaced on disk).
_FOLDERS = {"ckpt": ("checkpoints",), "unet": ("diffusion_models", "unet"), "clip": ("text_encoders", "clip"), "vae": ("vae",)}

# group key -> (cache key, outputs). One entry per group, so at most the models of the current selection are held.
_CACHE = {}
_CACHE_ENABLED = os.environ.get("MULTITABMODELS_NO_CACHE", "").strip().lower() not in ("1", "true", "yes", "on")

DEFAULT_STEPS = 20
DEFAULT_CFG = 8.0


def _comfy_node_class(class_name: str):
    """The ComfyUI node class registered under `class_name` (resolved lazily, so this module imports anywhere)."""
    mapping = importlib.import_module("nodes").NODE_CLASS_MAPPINGS
    cls = mapping.get(class_name)
    if cls is None:
        raise RuntimeError(f"[MultiTabModels] ComfyUI node '{class_name}' is not available in this ComfyUI version")
    return cls


def _outputs(result) -> tuple:
    """All outputs of a node call (legacy tuple / list, or a V3 NodeOutput) as a tuple."""
    if hasattr(result, "result") and not isinstance(result, (tuple, list)):
        result = result.result
    if isinstance(result, (tuple, list)):
        return tuple(result)
    return (result,)


def _call_loader(class_name: str, values: dict) -> tuple:
    """Run the standard ComfyUI node with the tab's values. Only parameters the node really has are passed."""
    cls = _comfy_node_class(class_name)
    fn = getattr(cls(), cls.FUNCTION)
    params = inspect.signature(fn).parameters
    if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
        values = {k: v for k, v in values.items() if k in params}
    return _outputs(fn(**values))


def clear_cache() -> None:
    _CACHE.clear()


def _file_signature(group: str, name: str):
    """(path, mtime, size) of the chosen file, so a file replaced on disk is loaded again. None if it cannot be found."""
    try:
        folder_paths = importlib.import_module("folder_paths")
        for folder in _FOLDERS.get(group, ()):
            try:
                path = folder_paths.get_full_path(folder, name)
            except Exception:
                path = None
            if path:
                stat = os.stat(path)
                return (path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        pass
    return None


def _load_cached(group: str, class_name: str, file_input: str, values: dict) -> tuple:
    """Load through ComfyUI's loader, but reuse the previous result when the very same file and options are asked again."""
    if not _CACHE_ENABLED:
        return _call_loader(class_name, values)
    key = (class_name, tuple(sorted(values.items())), _file_signature(group, values.get(file_input, "")))
    hit = _CACHE.get(group)
    if hit is not None and hit[0] == key:
        return hit[1]
    outputs = _call_loader(class_name, values)
    _CACHE[group] = (key, outputs)
    return outputs


def _install_free_hook() -> None:
    """When ComfyUI unloads all models (the "Free model and node cache" button), forget ours too so the memory can go."""
    try:
        model_management = importlib.import_module("comfy.model_management")
        original = getattr(model_management, "unload_all_models", None)
        if original is None or getattr(original, "_multitabmodels_hook", False):
            return

        def unload_all_models(*args, **kwargs):
            clear_cache()
            return original(*args, **kwargs)

        unload_all_models._multitabmodels_hook = True
        model_management.unload_all_models = unload_all_models
    except Exception:
        pass  # outside ComfyUI, or a version without it: the cache is then simply never cleared automatically


def _comfy_lists():
    """The sampler and scheduler names ComfyUI offers right now (KSampler's lists)."""
    try:
        ksampler = importlib.import_module("comfy.samplers").KSampler
        return list(ksampler.SAMPLERS), list(ksampler.SCHEDULERS)
    except Exception:
        return ["euler"], ["simple"]  # outside ComfyUI (tests): harmless placeholders


def _number(value, convert, default):
    try:
        return convert(float(value))
    except (TypeError, ValueError):
        return default


def parse_selection(raw) -> dict:
    """
    {"ckpt": {...}, "unet": {...}, "clip": {...}, "vae": {...}, "sampling": {...}, "lora": "text"}.
    Loader groups hold strings only; sampling keeps numbers as numbers. Anything invalid -> empty groups.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = {}
    for key in GROUP_KEYS:
        group = data.get(key)
        group = group if isinstance(group, dict) else {}
        plain = {str(k): v for k, v in group.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        out[key] = plain if key == "sampling" else {k: str(v) for k, v in plain.items()}
    lora = data.get("lora")
    out["lora"] = lora if isinstance(lora, str) else ""
    return out


class MultiTabModels:
    @classmethod
    def INPUT_TYPES(cls):
        cls._refresh_output_types()  # ComfyUI asks for the inputs whenever it (re)builds the node definitions
        # `selection` is filled in by the tab editor (hidden widget): the active tab's choices as JSON.
        return {"required": {"selection": ("STRING", {"default": "{}", "multiline": False})}}

    @classmethod
    def _refresh_output_types(cls):
        samplers, schedulers = _comfy_lists()  # lists, so the outputs connect to KSampler's combo inputs
        cls.RETURN_TYPES = ("MODEL", "CLIP", "VAE", "INT", "FLOAT", samplers, schedulers, "STRING")

    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "INT", "FLOAT", ["euler"], ["simple"], "STRING")  # replaced by _refresh_output_types
    # New outputs are only ever appended, so links made with older versions keep pointing at the same outputs.
    RETURN_NAMES = ("MODEL", "CLIP", "VAE", "steps", "cfg", "sampler_name", "scheduler", "LORA")
    FUNCTION = "load"
    CATEGORY = "MultiTabText"

    def load(self, selection="{}"):
        sel = parse_selection(selection)
        samplers, schedulers = _comfy_lists()

        # 1. checkpoint: model + clip + vae from one file
        ckpt = (None, None, None)
        ckpt_class, ckpt_input = LOADERS["ckpt"]
        if sel["ckpt"].get(ckpt_input):
            ckpt = (_load_cached("ckpt", ckpt_class, ckpt_input, sel["ckpt"]) + (None, None, None))[:3]

        # 2. separate loaders replace the matching checkpoint output
        chosen = []
        for index, key in enumerate(("unet", "clip", "vae")):
            class_name, file_input = LOADERS[key]
            loaded = None
            if sel[key].get(file_input):
                loaded = _load_cached(key, class_name, file_input, sel[key])[0]
            chosen.append(loaded if loaded is not None else ckpt[index])

        # 3. sampler settings
        sampling = sel["sampling"]
        steps = _number(sampling.get("steps"), lambda v: int(round(v)), DEFAULT_STEPS)
        cfg = _number(sampling.get("cfg"), float, DEFAULT_CFG)
        sampler_name = str(sampling.get("sampler_name") or (samplers[0] if samplers else ""))
        scheduler = str(sampling.get("scheduler") or (schedulers[0] if schedulers else ""))

        if _DEBUG:
            print(
                f"[MultiTabModels] ckpt={sel['ckpt'].get(ckpt_input) or '(none)'} "
                f"unet={sel['unet'].get('unet_name') or '(none)'} clip={sel['clip'].get('clip_name') or '(none)'} "
                f"vae={sel['vae'].get('vae_name') or '(none)'} steps={steps} cfg={cfg} "
                f"sampler={sampler_name} scheduler={scheduler}",
                flush=True,
            )
        lora = strip_comments(sel["lora"])  # commented-out LoRAs / notes are not part of the output
        return (chosen[0], chosen[1], chosen[2], steps, cfg, sampler_name, scheduler, lora)


MultiTabModels._refresh_output_types()
_install_free_hook()

NODE_CLASS_MAPPINGS = {"MultiTabModels": MultiTabModels}
NODE_DISPLAY_NAME_MAPPINGS = {"MultiTabModels": "MultiTabModels"}
