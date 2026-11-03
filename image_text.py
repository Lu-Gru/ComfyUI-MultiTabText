"""
Image Add Text - draws text onto every image of an IMAGE batch.

The node has no optional sockets: the text, the look of the font and the position are typed into its widgets.
It outputs the image with the text drawn on it, the text itself (so it can be passed on) and the alpha
channel of the result as a MASK.

  * text       multi-line text (line breaks are kept; long lines can be wrapped to `width`)
  * font       a font file found in ComfyUI's `fonts` folder, this node's `fonts` folder, or the system fonts
  * font_size  size in pixels
  * font_color / background_color / outline_color
               chosen from a palette of common colours (including transparency and 25 / 50 / 75 % variants),
               or `Custom` plus a colour typed into the matching `*_custom` field
  * x, y       top-left corner of the text box in pixels (negative values move it partly off the image)
  * width      width of the box in pixels; 0 = as wide as the text. A fixed width wraps long lines.
  * height     height of the box in pixels; 0 = as tall as the text. Text below a fixed height is cut off.
  * align      left / center / right inside the box
  * valign     top / middle / bottom inside the box
  * wrap       wrap the text to `width` (on by default)
  * output_alpha
               output the image as RGBA (4 channels) instead of RGB (3 channels). The `alpha` MASK output
               is always available.

Colours accept `#RGB`, `#RRGGBB`, `#RRGGBBAA`, a palette name, a CSS colour name or `rgb(...)` / `rgba(...)`.
The alpha value of a colour is used when the text, its outline or the background is drawn.

Examples:
  "White (#FFFFFF)" text with an outline in "Black (#000000)" is readable on most photos.
  "Transparent (#00000000)" background draws only the text; the original image stays visible.
  A background with alpha (for example "Black 50% (#00000080)") darkens the area behind the text.
"""

import importlib
import math
import os
import re
import sys

DEFAULT_FONT_LABEL = "Default (Pillow)"

# ---------------------------------------------------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------------------------------------------------

CUSTOM_COLOR_LABEL = "Custom (type a colour below)"

# label -> "#RRGGBB" / "#RRGGBBAA" (the label is what the drop-down shows, the hex is what is parsed)
_COLOR_TABLE = {
    "Transparent (#00000000)": "#00000000",
    "Black (#000000)": "#000000",
    "White (#FFFFFF)": "#FFFFFF",
    "Gray (#808080)": "#808080",
    "Light gray (#D3D3D3)": "#D3D3D3",
    "Dark gray (#404040)": "#404040",
    "Silver (#C0C0C0)": "#C0C0C0",
    "Red (#FF0000)": "#FF0000",
    "Dark red (#8B0000)": "#8B0000",
    "Crimson (#DC143C)": "#DC143C",
    "Orange (#FFA500)": "#FFA500",
    "Dark orange (#FF8C00)": "#FF8C00",
    "Gold (#FFD700)": "#FFD700",
    "Yellow (#FFFF00)": "#FFFF00",
    "Lime (#00FF00)": "#00FF00",
    "Green (#008000)": "#008000",
    "Forest green (#228B22)": "#228B22",
    "Mint (#98FF98)": "#98FF98",
    "Teal (#008080)": "#008080",
    "Turquoise (#40E0D0)": "#40E0D0",
    "Cyan (#00FFFF)": "#00FFFF",
    "Sky blue (#87CEEB)": "#87CEEB",
    "Blue (#0000FF)": "#0000FF",
    "Royal blue (#4169E1)": "#4169E1",
    "Navy (#000080)": "#000080",
    "Indigo (#4B0082)": "#4B0082",
    "Purple (#800080)": "#800080",
    "Violet (#EE82EE)": "#EE82EE",
    "Magenta (#FF00FF)": "#FF00FF",
    "Pink (#FFC0CB)": "#FFC0CB",
    "Salmon (#FA8072)": "#FA8072",
    "Coral (#FF7F50)": "#FF7F50",
    "Brown (#A52A2A)": "#A52A2A",
    "Beige (#F5F5DC)": "#F5F5DC",
    "Khaki (#F0E68C)": "#F0E68C",
    "Lavender (#E6E6FA)": "#E6E6FA",
    "Peach (#FFDAB9)": "#FFDAB9",
    "White 25% (#FFFFFF40)": "#FFFFFF40",
    "White 50% (#FFFFFF80)": "#FFFFFF80",
    "White 75% (#FFFFFFBF)": "#FFFFFFBF",
    "Black 25% (#00000040)": "#00000040",
    "Black 50% (#00000080)": "#00000080",
    "Black 75% (#000000BF)": "#000000BF",
}
_COLOR_CHOICES = list(_COLOR_TABLE.keys()) + [CUSTOM_COLOR_LABEL]

_RGBA_FUNCTION = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/ ]\s*([\d.]+)\s*)?\)",
    re.IGNORECASE,
)


def parse_color(value, default=(255, 255, 255, 255), name="colour"):
    """A PIL RGBA tuple from a palette label, a hex value, a CSS name or rgb()/rgba()."""
    from PIL import ImageColor

    raw = str(value or "").strip()
    if not raw:
        return default
    raw = _COLOR_TABLE.get(raw, raw)
    if raw.lower() == "transparent":
        return (0, 0, 0, 0)

    match = _RGBA_FUNCTION.fullmatch(raw)
    if match:
        r, g, b = (max(0, min(255, int(part))) for part in match.group(1, 2, 3))
        alpha_raw = match.group(4)
        if alpha_raw is None:
            a = 255
        else:
            alpha = float(alpha_raw)
            a = round(alpha * 255) if alpha <= 1.0 else round(alpha)
            a = max(0, min(255, int(a)))
        return (r, g, b, a)

    try:
        return tuple(ImageColor.getcolor(raw, "RGBA"))
    except ValueError:
        raise ValueError(
            "[Image Add Text] invalid %s %r. Use a palette entry, #RGB, #RRGGBB, #RRGGBBAA, "
            "a colour name such as 'white' or rgb(255, 0, 0) / rgba(255, 0, 0, 0.5)." % (name, value)
        ) from None


def _resolve_color(choice, custom, default, name):
    if str(choice or "").strip() == CUSTOM_COLOR_LABEL:
        return parse_color(custom, default=default, name=name + "_custom")
    return parse_color(choice, default=default, name=name)


# ---------------------------------------------------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------------------------------------------------

_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")
_FONT_CACHE = None  # (labels, {label: path}) - built once, the font folders rarely change while ComfyUI runs
_PREFERRED_FONTS = ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf")


def _font_folder():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def _scan_font_folder(folder):
    """(file name, full path) for every font file below `folder`."""
    found = []
    if not os.path.isdir(folder):
        return found
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if filename.lower().endswith(_FONT_EXTENSIONS):
                found.append((filename, os.path.join(dirpath, filename)))
    return found


def _comfy_fonts():
    """Fonts registered by ComfyUI (`models/fonts`, `custom_nodes/.../fonts`, ...)."""
    try:
        folder_paths = importlib.import_module("folder_paths")
    except Exception:
        return []
    try:
        names = list(folder_paths.get_filename_list("fonts"))
    except Exception:
        names = []
    found = []
    for name in names:
        try:
            path = folder_paths.get_full_path("fonts", name)
        except Exception:
            path = None
        if path and os.path.isfile(path):
            found.append((str(name).replace("\\", "/"), path))
    if found:
        return found
    # Older ComfyUI versions have no "fonts" folder type. Walk the folders if they exist.
    try:
        folders = list(folder_paths.get_folder_paths("fonts"))
    except Exception:
        folders = []
    for folder in folders:
        found.extend(_scan_font_folder(folder))
    return found


def _system_font_folders():
    folders = []
    if os.name == "nt":
        windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        folders.append(os.path.join(windows, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            folders.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    else:
        folders.extend(
            [
                "/usr/share/fonts",
                "/usr/local/share/fonts",
                os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts"),
            ]
        )
        if sys.platform == "darwin":
            folders.extend(
                [
                    "/System/Library/Fonts",
                    "/Library/Fonts",
                    os.path.expanduser("~/Library/Fonts"),
                ]
            )
    return folders


def _font_catalog():
    """(`labels`, `{label: path}`) for the font drop-down. Scanned once."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE

    mapping = {}
    labels = [DEFAULT_FONT_LABEL]  # always selectable, used when nothing else is found

    def add(label, path):
        key = label.casefold()
        if key in mapping or label == DEFAULT_FONT_LABEL:
            return
        mapping[key] = path
        labels.append(label)

    for label, path in _comfy_fonts():  # ComfyUI's own fonts win over system fonts with the same name
        add(label, path)
    for label, path in _scan_font_folder(_font_folder()):  # fonts shipped next to this node
        add(label, path)
    for folder in _system_font_folders():
        for label, path in _scan_font_folder(folder):
            add(label, path)

    # Case-insensitive lookups, while the drop-down keeps the original spelling.
    lookup = {label.casefold(): path for label, path in mapping.items()}
    labels = [labels[0]] + sorted(labels[1:], key=str.casefold)
    _FONT_CACHE = (labels, lookup)
    return _FONT_CACHE


def font_choices():
    return list(_font_catalog()[0])


def default_font_choice():
    labels = _font_catalog()[0]
    by_lower = {label.casefold(): label for label in labels}
    for name in _PREFERRED_FONTS:
        label = by_lower.get(name.casefold())
        if label:
            return label
    return labels[1] if len(labels) > 1 else DEFAULT_FONT_LABEL


def load_font(selection, size):
    """A FreeType font for `selection` at `size`, with a Pillow default when the file cannot be loaded."""
    from PIL import ImageFont

    size = max(1, int(size))
    raw = str(selection or "").strip()
    path = _font_catalog()[1].get(raw.casefold())
    if path is None:  # allow a full path typed by hand (for example from an older workflow)
        candidate = os.path.expandvars(os.path.expanduser(raw.strip('"').strip("'")))
        if candidate and os.path.isfile(candidate) and candidate.lower().endswith(_FONT_EXTENSIONS):
            path = candidate

    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as error:
            print(f"[Image Add Text] could not load font {path!r} ({error}); using the Pillow default font.", flush=True)

    try:
        return ImageFont.load_default(size=size)  # Pillow 10.1+
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------------------------------------------------
# Text layout and drawing
# ---------------------------------------------------------------------------------------------------------------------


def wrap_line(line, font, max_width):
    """Split one line so that every part fits into `max_width` pixels, keeping spaces where possible."""
    if max_width <= 0 or not line or font.getlength(line) <= max_width:
        return [line]

    parts = []
    rest = line
    while rest:
        if font.getlength(rest) <= max_width:
            parts.append(rest)
            break
        # Largest prefix that still fits.
        low, high = 1, len(rest)
        while low < high:
            middle = (low + high + 1) // 2
            if font.getlength(rest[:middle]) <= max_width:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        space = rest.rfind(" ", 0, cut)
        if space > 0:
            parts.append(rest[:space])
            rest = rest[space + 1:].lstrip(" ")
        else:  # a single word wider than the box: break it between characters
            parts.append(rest[:cut])
            rest = rest[cut:]
    return parts or [""]


def prepare_lines(text, font, max_width, wrap):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    lines = []
    for paragraph in text.split("\n"):
        if wrap and max_width > 0 and paragraph:
            lines.extend(wrap_line(paragraph, font, max_width))
        else:
            lines.append(paragraph)
    return lines


def _build_text_image(lines, font, font_rgba, outline_rgba, outline_width, align):
    """Render the text on a transparent image and crop it to the visible pixels (stroke included)."""
    from PIL import Image, ImageDraw

    if not lines or all(not line for line in lines):
        return None
    ascent, descent = font.getmetrics()
    line_height = max(1, ascent + descent)
    widths = [font.getlength(line) for line in lines]
    block_width = max(widths) if widths else 0
    padding = max(2, int(outline_width) + 2)

    layer = Image.new(
        "RGBA",
        (max(1, int(math.ceil(block_width)) + 2 * padding), max(1, line_height * len(lines) + 2 * padding)),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(layer)
    for index, line in enumerate(lines):
        if not line:
            continue
        if align == "center":
            x = padding + (block_width - widths[index]) / 2
        elif align == "right":
            x = padding + block_width - widths[index]
        else:
            x = padding
        # "la" keeps the baselines of all lines aligned, regardless of which letters a line contains.
        draw.text(
            (x, padding + index * line_height),
            line,
            font=font,
            fill=font_rgba,
            anchor="la",
            stroke_width=int(outline_width),
            stroke_fill=outline_rgba,
        )

    box = layer.getbbox()
    if not box:
        return None  # nothing visible (empty text, or a fully transparent text colour)
    return layer.crop(box)


def _align_offset(align, box_width, content_width):
    if align == "center":
        return (box_width - content_width) / 2
    if align == "right":
        return box_width - content_width
    return 0


_UNSET = object()


def layout_text(text, font, image_width, x, width, wrap, font_color, outline_color, outline_width, align):
    """Wrap the text and render it once into a cropped RGBA image (or None when nothing is visible)."""
    wrap_width = width if width > 0 else max(1, image_width - max(0, int(x)))
    lines = prepare_lines(text, font, wrap_width, wrap)
    return _build_text_image(lines, font, font_color, outline_color, outline_width, align)


def render_rgba(base, text, font, font_color, background_color, outline_color, outline_width,
                x, y, width, height, align, wrap, valign="top", content=_UNSET):
    """Draw on one PIL image (`RGB`, `RGBA` or `L`) and return the result as `RGBA`."""
    from PIL import Image, ImageDraw

    rgba = base.convert("RGBA")
    image_width, _ = rgba.size

    if content is _UNSET:
        content = layout_text(text, font, image_width, x, width, wrap, font_color, outline_color,
                              outline_width, align)

    box_width = int(width) if width > 0 else (content.width if content else 0)
    box_height = int(height) if height > 0 else (content.height if content else 0)

    overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if box_width > 0 and box_height > 0 and background_color[3] > 0:
        draw.rectangle(
            [int(x), int(y), int(x) + box_width - 1, int(y) + box_height - 1],
            fill=tuple(background_color),
        )

    if content is not None:
        text_x = int(round(x + _align_offset(align, box_width, content.width)))
        vertical_offset = 0
        if height > 0:
            if valign == "middle":
                vertical_offset = (box_height - content.height) / 2
            elif valign == "bottom":
                vertical_offset = box_height - content.height
        text_y = int(round(y + vertical_offset))

        if height > 0:  # a fixed height is a hard limit: clip the parts of the text outside the box
            clip_top = max(0, int(y) - text_y)
            clip_bottom = min(content.height, int(y) + box_height - text_y)
            if clip_bottom <= clip_top:
                content = None
            elif clip_top > 0 or clip_bottom < content.height:
                content = content.crop((0, clip_top, content.width, clip_bottom))
                text_y += clip_top

        if content is not None:
            text_layer = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
            text_layer.paste(content, (text_x, text_y))  # paste without a mask keeps the text's own alpha
            overlay = Image.alpha_composite(overlay, text_layer)

    return Image.alpha_composite(rgba, overlay)


# ---------------------------------------------------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------------------------------------------------


class ImageAddText:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (
                    "IMAGE",
                    {
                        "tooltip": "The image(s) the text is drawn on. Every image of a batch gets the same text.",
                    },
                ),
                "text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": "The text to draw. Line breaks are kept; long lines are wrapped to `width` when `wrap` is on.",
                    },
                ),
                "font": (
                    font_choices(),
                    {
                        "default": default_font_choice(),
                        "tooltip": "Font file. Put your own fonts into the `fonts` folder of this node or into ComfyUI's fonts folder, then restart ComfyUI.",
                    },
                ),
                "font_size": (
                    "INT",
                    {
                        "default": 48,
                        "min": 1,
                        "max": 2048,
                        "step": 1,
                        "tooltip": "Font size in pixels.",
                    },
                ),
                "font_color": (
                    _COLOR_CHOICES,
                    {
                        "default": "White (#FFFFFF)",
                        "tooltip": "Colour of the text. Choose `Custom` to use the value of `font_color_custom`.",
                    },
                ),
                "font_color_custom": (
                    "STRING",
                    {
                        "default": "#FFFFFF",
                        "tooltip": "Used when font_color is `Custom`: #RGB, #RRGGBB, #RRGGBBAA, a colour name or rgba(...).",
                    },
                ),
                "x": (
                    "INT",
                    {
                        "default": 0,
                        "min": -16384,
                        "max": 16384,
                        "step": 1,
                        "tooltip": "Left edge of the text box in pixels. Negative values move the text partly off the image.",
                    },
                ),
                "y": (
                    "INT",
                    {
                        "default": 0,
                        "min": -16384,
                        "max": 16384,
                        "step": 1,
                        "tooltip": "Top edge of the text box in pixels. Negative values move the text partly off the image.",
                    },
                ),
                "width": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 16384,
                        "step": 1,
                        "tooltip": "Width of the text box in pixels. 0 = as wide as the text. A fixed width wraps long lines.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 16384,
                        "step": 1,
                        "tooltip": "Height of the text box in pixels. 0 = as tall as the text. Text below a fixed height is cut off.",
                    },
                ),
                "background_color": (
                    _COLOR_CHOICES,
                    {
                        "default": "Transparent (#00000000)",
                        "tooltip": "Background of the text box. `Transparent` draws no background, so the image stays visible.",
                    },
                ),
                "background_color_custom": (
                    "STRING",
                    {
                        "default": "#00000000",
                        "tooltip": "Used when background_color is `Custom`: #RGB, #RRGGBB, #RRGGBBAA, a colour name or rgba(...).",
                    },
                ),
                "outline_color": (
                    _COLOR_CHOICES,
                    {
                        "default": "Black (#000000)",
                        "tooltip": "Colour of the outline around the letters. `Transparent` gives no visible outline.",
                    },
                ),
                "outline_color_custom": (
                    "STRING",
                    {
                        "default": "#000000",
                        "tooltip": "Used when outline_color is `Custom`: #RGB, #RRGGBB, #RRGGBBAA, a colour name or rgba(...).",
                    },
                ),
                "outline_width": (
                    "INT",
                    {
                        "default": 2,
                        "min": 0,
                        "max": 256,
                        "step": 1,
                        "tooltip": "Width of the outline in pixels. 0 = no outline.",
                    },
                ),
                "align": (
                    ["left", "center", "right"],
                    {
                        "default": "left",
                        "tooltip": "Horizontal alignment of the lines inside the text box.",
                    },
                ),
                "valign": (
                    ["top", "middle", "bottom"],
                    {
                        "default": "top",
                        "tooltip": "Vertical alignment of the text block inside the text box. With height 0 the box hugs the text, so all three look the same.",
                    },
                ),
                "wrap": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "wrap to width",
                        "label_off": "no wrapping",
                        "tooltip": "On: long lines are wrapped to `width`. Off: lines are drawn in full, even past the box.",
                    },
                ),
                "output_alpha": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "RGBA output",
                        "label_off": "RGB output",
                        "tooltip": "On: the image output has 4 channels (alpha included). Off: 3 channels. The alpha MASK output is always there.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "MASK")
    RETURN_NAMES = ("image", "text", "alpha")
    OUTPUT_TOOLTIPS = (
        "The image(s) with the text drawn on them.",
        "The text that was drawn.",
        "Alpha channel of the result (1 = opaque, 0 = transparent), for example for ImageCompositeMasked.",
    )
    FUNCTION = "run"
    CATEGORY = "MultiTabText"

    def run(self, image, text, font, font_size, font_color, font_color_custom, x, y, width, height,
            background_color, background_color_custom, outline_color, outline_color_custom, outline_width,
            align="left", valign="top", wrap=True, output_alpha=True):
        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("[Image Add Text] numpy is required (it ships with ComfyUI).") from None
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("[Image Add Text] Pillow is required (it ships with ComfyUI).") from None

        if image is None:
            raise ValueError("[Image Add Text] no image input")

        font_rgba = _resolve_color(font_color, font_color_custom, (255, 255, 255, 255), "font_color")
        background_rgba = _resolve_color(background_color, background_color_custom, (0, 0, 0, 0), "background_color")
        outline_rgba = _resolve_color(outline_color, outline_color_custom, (0, 0, 0, 255), "outline_color")

        selected_font = load_font(font, font_size)
        outline_width = max(0, int(outline_width))

        array = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
        if array.ndim == 3:
            array = array[None, ...]
        if array.ndim != 4 or array.shape[0] == 0:
            raise ValueError(f"[Image Add Text] expected an IMAGE batch, got an array with shape {array.shape}")
        array = np.clip(array.astype(np.float32), 0.0, 1.0)
        image_width = int(array.shape[2])
        content = layout_text(
            text,
            selected_font,
            image_width,
            int(x),
            int(width),
            bool(wrap),
            font_rgba,
            outline_rgba,
            outline_width,
            str(align or "left"),
        )

        images = []
        alphas = []
        for index in range(array.shape[0]):
            frame = (array[index] * 255.0).round().astype(np.uint8)
            channels = frame.shape[2]
            if channels == 1:
                base = Image.fromarray(frame[:, :, 0], "L")
            elif channels == 2:
                base = Image.fromarray(frame[:, :, 0], "L")
            elif channels == 3:
                base = Image.fromarray(frame, "RGB")
            else:
                base = Image.fromarray(frame[:, :, :4], "RGBA")

            result = render_rgba(
                base,
                text,
                selected_font,
                font_rgba,
                background_rgba,
                outline_rgba,
                outline_width,
                int(x),
                int(y),
                int(width),
                int(height),
                str(align or "left"),
                bool(wrap),
                str(valign or "top"),
                content=content,
            )
            alphas.append(np.asarray(result.getchannel("A"), dtype=np.float32) / 255.0)
            images.append(np.asarray(result if output_alpha else result.convert("RGB"), dtype=np.uint8))

        try:
            import torch

            out_images = torch.from_numpy(np.stack(images, axis=0).astype(np.float32) / 255.0)
            if hasattr(image, "device"):
                out_images = out_images.to(image.device)
            out_alpha = torch.from_numpy(np.stack(alphas, axis=0).astype(np.float32))
            if hasattr(image, "device"):
                out_alpha = out_alpha.to(image.device)
        except ImportError:  # useful when the drawing helpers are used outside ComfyUI
            out_images = np.stack(images, axis=0).astype(np.float32) / 255.0
            out_alpha = np.stack(alphas, axis=0).astype(np.float32)

        return (out_images, str(text or ""), out_alpha)


NODE_CLASS_MAPPINGS = {"MultiTabTextImageAddText": ImageAddText}
NODE_DISPLAY_NAME_MAPPINGS = {"MultiTabTextImageAddText": "Image Add Text (MultiTabText)"}
