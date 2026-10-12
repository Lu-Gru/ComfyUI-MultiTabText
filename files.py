"""
Two file nodes:

  TxtReadFile  - reads a text file and outputs its content.
  TxtWriteFile - writes (or appends) its text input to a file and outputs what it wrote. See the class docstring.

TxtReadFile

The node has no inputs: type (or paste) the path of the file into its `path` field.

  * Quotes around the path are ignored, so a path copied with "Copy as path" works as it is.
  * `~` and environment variables (%USERPROFILE%, $HOME ...) are expanded.
  * A relative path is looked up from ComfyUI's working directory, then in ComfyUI's input folder.
  * Encodings: UTF-8 (with or without BOM), UTF-16 / UTF-32 with a BOM, otherwise Windows-1252. Line endings are
    normalised to "\\n".
  * The node re-runs when the file changes (its modification time / size are part of IS_CHANGED), and is reused
    from ComfyUI's cache otherwise.

Safety: the node reads any file the ComfyUI process may read. If ComfyUI is reachable by other people, restrict it
with the environment variable TXTREADFILE_ALLOWED_DIRS (folders separated by ";" on Windows / ":" elsewhere): only
files inside those folders can be read then. TXTREADFILE_MAX_MB (default 64) limits the size of a file.
"""

import codecs
import importlib
import os

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),  # checked before UTF-16 LE: its BOM starts with the same two bytes
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _input_dir():
    try:
        return importlib.import_module("folder_paths").get_input_directory()
    except Exception:
        return None  # outside ComfyUI


def clean_path(raw) -> str:
    """The path as typed, without surrounding spaces / quotes, with ~ and environment variables expanded."""
    path = str(raw or "").strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1].strip()
    return os.path.expandvars(os.path.expanduser(path))


def resolve_path(raw) -> str:
    path = clean_path(raw)
    if not path:
        raise ValueError("[TxtReadFile] no file path given")
    if os.path.isabs(path):
        return os.path.normpath(path)
    candidates = [os.path.abspath(path)]  # relative to ComfyUI's working directory
    input_dir = _input_dir()
    if input_dir:
        candidates.append(os.path.abspath(os.path.join(input_dir, path)))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]  # not found anywhere: reported with this path


def _allowed_dirs(variable="TXTREADFILE_ALLOWED_DIRS"):
    raw = os.environ.get(variable, "")
    return [os.path.realpath(d.strip()) for d in raw.split(os.pathsep) if d.strip()]


def _check_allowed(path: str, variable="TXTREADFILE_ALLOWED_DIRS") -> None:
    allowed = _allowed_dirs(variable)
    if not allowed:
        return
    real = os.path.realpath(path)
    for folder in allowed:
        try:
            if os.path.commonpath([real, folder]) == folder:
                return
        except ValueError:  # another drive
            continue
    tag = "TxtWriteFile" if variable.startswith("TXTWRITEFILE") else "TxtReadFile"
    raise PermissionError(f"[{tag}] '{path}' is outside the folders allowed by {variable}")


def _max_bytes() -> int:
    try:
        return int(float(os.environ.get("TXTREADFILE_MAX_MB", "64")) * 1024 * 1024)
    except ValueError:
        return 64 * 1024 * 1024


def decode_text(data: bytes) -> str:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # the usual encoding of older Windows text files


def read_text_file(raw_path) -> str:
    path = resolve_path(raw_path)
    _check_allowed(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"[TxtReadFile] file not found: {path}")
    if not os.path.isfile(path):
        raise IsADirectoryError(f"[TxtReadFile] not a file: {path}")
    size = os.path.getsize(path)
    if size > _max_bytes():
        raise ValueError(f"[TxtReadFile] file is too large ({size / 1048576:.1f} MB, limit set by TXTREADFILE_MAX_MB): {path}")
    with open(path, "rb") as handle:
        data = handle.read()
    return decode_text(data).replace("\r\n", "\n").replace("\r", "\n")


class TxtReadFile:
    @classmethod
    def INPUT_TYPES(cls):
        # No sockets: just a text field for the path.
        return {"required": {"path": ("STRING", {"default": "", "multiline": False, "placeholder": "C:\\path\\to\\file.txt"})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "read"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, path="", **kwargs):
        # Same file, same modification time and size -> same result: ComfyUI may reuse its cached output.
        # A missing / unreadable file always re-runs, so the error is shown again.
        try:
            resolved = resolve_path(path)
            stat = os.stat(resolved)
            return f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}"
        except Exception:
            return float("nan")

    def read(self, path=""):
        return (read_text_file(path),)


def _output_dir():
    try:
        return importlib.import_module("folder_paths").get_output_directory()
    except Exception:
        return None  # outside ComfyUI


def resolve_write_path(raw) -> str:
    """Absolute paths are used as they are; a relative path is placed inside ComfyUI's output folder."""
    path = clean_path(raw)
    if not path:
        raise ValueError("[TxtWriteFile] no file path given")
    if path[-1] in ("/", "\\") or os.path.isdir(path):
        raise IsADirectoryError(f"[TxtWriteFile] the path is a folder, not a file: {path}")
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.abspath(os.path.join(_output_dir() or os.getcwd(), path))


def write_text_file(raw_path, text, overwrite=False, new_line=True) -> str:
    """
    Write `text` to the file and return it.
      overwrite: the file content is replaced (written to a temporary file first, so a failure never leaves a
                 half-written file). Otherwise `text` is appended to the end of the file.
      new_line:  only when appending to a file that already has content: a line break is written before `text`.
    The file is created (with any missing folders) when it does not exist. Text is written as UTF-8, line breaks as given.
    """
    path = resolve_write_path(raw_path)
    _check_allowed(path, "TXTWRITEFILE_ALLOWED_DIRS")
    text = "" if text is None else str(text)
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if overwrite:
            temporary = f"{path}.{os.getpid()}.tmp"
            try:
                with open(temporary, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
        elif text:  # appending nothing changes nothing (not even a line break)
            has_content = os.path.exists(path) and os.path.getsize(path) > 0
            with open(path, "a", encoding="utf-8", newline="") as handle:
                handle.write(("\n" if new_line and has_content else "") + text)
    except OSError as error:
        raise OSError(f"[TxtWriteFile] could not write '{path}': {error}") from error
    return text


class TxtWriteFile:
    """
    Saves its text input to a file.

      path       where to save. A relative path is placed in ComfyUI's output folder (missing folders are created).
      overwrite  on: the file content is replaced by the text. off: the text is appended to the end of the file.
      new_line   (append mode) on: a line break is written before the appended text, so it starts on a new line.
                 Nothing is added when the file is new or empty. Ignored in overwrite mode.

    The output is the text that was written (without the added line break), so it can be passed on.

    The node always runs when it is part of the queued workflow, even if nothing is connected to its output. In
    append mode it runs on every queue (each run appends); in overwrite mode it runs when the text, the path or the
    options change.

    Safety: it can write any file the ComfyUI process may write. If other people can reach your ComfyUI, restrict it
    with TXTWRITEFILE_ALLOWED_DIRS (folders separated by ";" on Windows / ":" elsewhere).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True}),  # a socket: connect the text to save
                "path": ("STRING", {"default": "", "multiline": False, "placeholder": "C:\\path\\to\\file.txt"}),
                "overwrite": ("BOOLEAN", {"default": False, "label_on": "overwrite", "label_off": "append"}),
                "new_line": ("BOOLEAN", {"default": True, "label_on": "new line before text", "label_off": "no new line"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "write"
    CATEGORY = "MultiTabText"
    OUTPUT_NODE = True  # runs even when its output is not used (it exists for its side effect)

    @classmethod
    def IS_CHANGED(cls, overwrite=False, **kwargs):
        # NaN never equals itself, so ComfyUI runs the node every time: right for appending.
        # Overwriting is repeatable: let ComfyUI skip it while nothing changed.
        return "" if overwrite else float("nan")

    def write(self, text="", path="", overwrite=False, new_line=True):
        return (write_text_file(path, text, bool(overwrite), bool(new_line)),)


NODE_CLASS_MAPPINGS = {"TxtReadFile": TxtReadFile, "TxtWriteFile": TxtWriteFile}
NODE_DISPLAY_NAME_MAPPINGS = {"TxtReadFile": "TxtReadFile", "TxtWriteFile": "TxtWriteFile"}
