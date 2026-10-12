import os

from .nodes import BUILD, NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .models import NODE_CLASS_MAPPINGS as _MODEL_NODES, NODE_DISPLAY_NAME_MAPPINGS as _MODEL_NAMES
from .files import NODE_CLASS_MAPPINGS as _FILE_NODES, NODE_DISPLAY_NAME_MAPPINGS as _FILE_NAMES

NODE_CLASS_MAPPINGS = {**NODE_CLASS_MAPPINGS, **_MODEL_NODES, **_FILE_NODES}
NODE_DISPLAY_NAME_MAPPINGS = {**NODE_DISPLAY_NAME_MAPPINGS, **_MODEL_NAMES, **_FILE_NAMES}

# Tells ComfyUI to serve ./web/*.js as frontend extensions.
WEB_DIRECTORY = "./web"

_here = os.path.dirname(os.path.abspath(__file__))
_js = os.path.join(_here, "web", "multi_tab_text.js")
_js_models = os.path.join(_here, "web", "multi_tab_models.js")
if os.path.isfile(_js) and not os.path.isfile(_js_models):
    print("[MultiTabText] WARNING: web/multi_tab_models.js is MISSING. The MultiTabModels editor will not appear.")
if os.path.isfile(_js):
    print("[MultiTabText] Python side loaded (build " + BUILD + ") from " + _here)
else:
    print("[MultiTabText] WARNING: web/multi_tab_text.js is MISSING. The tab editor will not appear. "
          "Keep the folder structure: MultiTabText/__init__.py, nodes.py, web/multi_tab_text.js")

# An older copy of this package (for example the former "comfyui_random_text_tabs" folder) registers the
# same node names and can silently replace this version. Warn if one is found next to this folder.
try:
    _parent = os.path.dirname(_here)
    for _name in os.listdir(_parent):
        _other = os.path.join(_parent, _name)
        if os.path.abspath(_other) == _here or not os.path.isdir(_other):
            continue
        _old_js = os.path.isfile(os.path.join(_other, "web", "random_text_tabs.js"))
        _old_py = False
        _nodes = os.path.join(_other, "nodes.py")
        if os.path.isfile(_nodes):
            with open(_nodes, "r", encoding="utf-8", errors="ignore") as _f:
                _old_py = "class RandomTextTabs" in _f.read(60000)
        if _old_js or _old_py:
            print("[MultiTabText] WARNING: another copy of these nodes is installed at " + _other +
                  " . It registers the same node names; delete it and restart ComfyUI.")
except Exception:
    pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
