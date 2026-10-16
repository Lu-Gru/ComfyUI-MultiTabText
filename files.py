"""
Three file nodes:

  TxtReadFile  - reads a text file and outputs its content (and an `errors` text; it never stops the run).
  TxtWriteFile - writes (or appends) its text input to a file and outputs what it wrote and the full path.
  ListFiles    - lists the files of a folder (optionally only some suffixes) and outputs one of them or all of them.

TxtReadFile

The node has no inputs: type (or paste) the path of the file into its `path` field.

  * Quotes around the path are ignored, so a path copied with "Copy as path" works as it is.
  * `~` and environment variables (%USERPROFILE%, $HOME ...) are expanded.
  * A relative path is looked up from ComfyUI's working directory, then in ComfyUI's input folder.
  * Encodings: UTF-8 (with or without BOM), UTF-16 / UTF-32 with a BOM, otherwise Windows-1252. Line endings are
    normalised to "\\n".
  * Nothing is cached: the file is read again on every run, so the newest content is always used.
  * Errors never stop the run: `text` is empty and the `errors` output holds the message (empty when all is well).

Safety: the node reads any file the ComfyUI process may read. If ComfyUI is reachable by other people, restrict it
with the environment variable TXTREADFILE_ALLOWED_DIRS (folders separated by ";" on Windows / ":" elsewhere): only
files inside those folders can be read then. TXTREADFILE_MAX_MB (default 64) limits the size of a file.
"""

import codecs
import importlib
import os
import re

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


def _check_allowed(path: str, variable="TXTREADFILE_ALLOWED_DIRS", tag=None) -> None:
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
    tag = tag or ("TxtWriteFile" if variable.startswith("TXTWRITEFILE") else "TxtReadFile")
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


def error_message(tag: str, error: Exception) -> str:
    """A readable one-line message, always starting with the node's tag."""
    text = str(error).strip()
    if text.startswith(f"[{tag}]"):
        return text
    return f"[{tag}] {type(error).__name__}: {text}"


class TxtReadFile:
    @classmethod
    def INPUT_TYPES(cls):
        # No sockets: just a text field for the path.
        return {"required": {"path": ("STRING", {"default": "", "multiline": False, "placeholder": "C:\\path\\to\\file.txt"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "errors")
    FUNCTION = "read"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # NaN never equals itself: ComfyUI runs the node on every queue, so the file is always read again.
        return float("nan")

    def read(self, path=""):
        try:
            return (read_text_file(path), "")
        except Exception as error:  # every problem ends up in the `errors` output instead of stopping the run
            message = error_message("TxtReadFile", error)
            print(message, flush=True)  # also visible in the console, in case `errors` is not connected
            return ("", message)


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
    """Write `text` to the file and return it (see write_text_file_at for the full path as well)."""
    return write_text_file_at(raw_path, text, overwrite, new_line)[0]


def write_text_file_at(raw_path, text, overwrite=False, new_line=True):
    """
    Write `text` to the file. Returns (text, full path of the file).
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
    return text, path


class TxtWriteFile:
    """
    Saves its text input to a file.

      path       where to save. A relative path is placed in ComfyUI's output folder (missing folders are created).
      overwrite  on: the file content is replaced by the text. off: the text is appended to the end of the file.
      new_line   (append mode) on: a line break is written before the appended text, so it starts on a new line.
                 Nothing is added when the file is new or empty. Ignored in overwrite mode.

    Outputs: `text` is the text that was written (without the added line break), so it can be passed on;
    `full_path` is the full path of the file that was written to.

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

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "full_path")
    FUNCTION = "write"
    CATEGORY = "MultiTabText"
    OUTPUT_NODE = True  # runs even when its output is not used (it exists for its side effect)

    @classmethod
    def IS_CHANGED(cls, overwrite=False, **kwargs):
        # NaN never equals itself, so ComfyUI runs the node every time: right for appending.
        # Overwriting is repeatable: let ComfyUI skip it while nothing changed.
        return "" if overwrite else float("nan")

    def write(self, text="", path="", overwrite=False, new_line=True):
        return write_text_file_at(path, text, bool(overwrite), bool(new_line))


# ---------------------------------------------------------------------------------------------------------------------
# ListFiles
# ---------------------------------------------------------------------------------------------------------------------
MODE_SINGLE = "single file"
MODE_LIST = "list of files"


def parse_suffixes(raw) -> tuple:
    """
    ".png, .jpg" -> (".png", ".jpg"). Separate several suffixes with comma, semicolon or new line.
    Case does not matter. A bare word is an extension ("png" = ".png"); "*.png" = ".png"; longer endings such
    as "_final.png" are used as they are. No suffix at all means every file.
    """
    suffixes = []
    for part in re.split(r"[,;\n]", str(raw or "")):
        item = part.strip().strip("\"'").lstrip("*").lower()
        if not item:
            continue
        if item.isalnum():
            item = "." + item
        suffixes.append(item)
    return tuple(suffixes)


def _natural_key(name: str):
    # "img2" before "img10"; the parts alternate text / number, so the tuples always compare like with like
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)] + [name]


def resolve_directory(raw) -> str:
    path = clean_path(raw)
    if not path:
        raise ValueError("[ListFiles] no folder path given")
    if os.path.isabs(path):
        return os.path.normpath(path)
    candidates = [os.path.abspath(path)]  # relative to ComfyUI's working directory, then its input folder
    input_dir = _input_dir()
    if input_dir:
        candidates.append(os.path.abspath(os.path.join(input_dir, path)))
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def list_matching_files(raw_path, raw_suffix="") -> list:
    """Full paths of the files directly inside the folder, in natural order. Sub folders and hidden files are skipped."""
    folder = resolve_directory(raw_path)
    _check_allowed(folder, tag="ListFiles")  # the same folders as TxtReadFile may be listed (TXTREADFILE_ALLOWED_DIRS)
    if not os.path.exists(folder):
        raise FileNotFoundError(f"[ListFiles] folder not found: {folder}")
    if not os.path.isdir(folder):
        raise NotADirectoryError(f"[ListFiles] not a folder: {folder}")
    suffixes = parse_suffixes(raw_suffix)
    names = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_file():
                continue
            if suffixes and not entry.name.lower().endswith(suffixes):
                continue
            names.append(entry.name)
    names.sort(key=_natural_key)
    return [os.path.join(folder, name) for name in names]


def select_files(files: list, mode: str, index: int, wrap: bool) -> list:
    """All files in list mode, otherwise the one at `index` (wrapped around the number of files when `wrap` is on)."""
    if mode == MODE_LIST:
        return list(files)
    index = int(index)
    if wrap:
        index %= len(files)
    elif not 0 <= index < len(files):
        raise IndexError(
            f"[ListFiles] index {index} is out of range: {len(files)} file(s) found, valid indexes are 0 to {len(files) - 1} "
            f"(switch on wrap to wrap the index around)"
        )
    return [files[index]]


class ListFiles:
    """
    Lists the files of a folder and outputs one of them or all of them.

      path    the folder. Relative paths are looked up from ComfyUI's working directory, then its input folder.
      suffix  only files ending like this (".png", or several: ".png, .jpg", or longer endings such as "_final.png").
              Empty = every file. Case does not matter. Sub folders and hidden files (".name") are never listed.
      mode    "single file": the file at `index`.  "list of files": every matching file.
      index   which of the found files (0 = the first, files are in natural order: img2 before img10).
              Has "control after generate" in ComfyUI, so it can count up by itself on every queue.
      wrap    on: an index past the last file wraps around to the start (index % number of files).
              off: an index out of range is reported in `errors`.

    Outputs (full paths):
      files   the selected file(s) as a ComfyUI LIST: with "list of files" the nodes after it run once per file.
      text    the same file(s) as one text, one path per line.
      count   how many files matched the suffix (not only the selected ones).
      errors  empty when all is well, otherwise the message; the run is never stopped.
    The folder is listed again on every run.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {"default": "", "multiline": False, "placeholder": "C:\\path\\to\\folder"}),
                "suffix": ("STRING", {"default": "", "multiline": False, "placeholder": ".png, .jpg   (empty = all files)"}),
                "mode": ([MODE_SINGLE, MODE_LIST], {"default": MODE_SINGLE}),
                "index": ("INT", {"default": 0, "min": 0, "max": 1000000000, "step": 1, "control_after_generate": True}),
                "wrap": ("BOOLEAN", {"default": True, "label_on": "wrap index around", "label_off": "no wrap (error if too big)"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "STRING")
    RETURN_NAMES = ("files", "text", "count", "errors")
    OUTPUT_IS_LIST = (True, False, False, False)
    FUNCTION = "list_files"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # the folder can change at any time: list it again on every queue

    def list_files(self, path="", suffix="", mode=MODE_SINGLE, index=0, wrap=True):
        count = 0
        try:
            found = list_matching_files(path, suffix)
            count = len(found)
            if not found:
                suffixes = parse_suffixes(suffix)
                what = f" ending with {', '.join(suffixes)}" if suffixes else ""
                raise LookupError(f"[ListFiles] no files{what} found in {resolve_directory(path)}")
            chosen = select_files(found, mode, index, bool(wrap))
            return (chosen, "\n".join(chosen), count, "")
        except Exception as error:  # every problem ends up in the `errors` output instead of stopping the run
            message = error_message("ListFiles", error)
            print(message, flush=True)
            # list mode: an empty list; single mode: one empty path, so the nodes after it still run once
            return ([] if mode == MODE_LIST else [""], "", count, message)


NODE_CLASS_MAPPINGS = {"TxtReadFile": TxtReadFile, "TxtWriteFile": TxtWriteFile, "ListFiles": ListFiles}
NODE_DISPLAY_NAME_MAPPINGS = {"TxtReadFile": "TxtReadFile", "TxtWriteFile": "TxtWriteFile", "ListFiles": "ListFiles"}
