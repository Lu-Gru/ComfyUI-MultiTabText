"""
Media Frame - picks an image or video (or a folder of media files) and returns one frame as an image.

The node scans a file or folder for media files, lets `media_index` choose one of the matches, and
`frame_index` choose a frame inside it. Both indexes have ComfyUI's "control after generate" option
(fixed / increment / decrement / randomize), so the node can walk through frames or pick random ones
on every queue. A separate wrap option handles an index that is past the last file / frame. The
`skip_unknown_frame_count` switch makes the node pass over video files whose container does not store
an exact frame count and try the next scanned file, instead of decoding the whole file just to count.

For videos, `start_percent` and `end_percent` narrow the frame range. With the default full range
(0..100) `frame_index` and `frame_wrap` work as usual; with any other range the node returns a random
frame inside that part of the video and ignores `frame_index` for videos. Images are a single frame,
so they ignore the percentage range. A start past the end, or a value outside 0..100, is reported as
an error before anything is decoded.

`max_seconds_per_file` is an optional effort limit for one file. Opening, probing, counting frames and
decoding the frame are checked against it; when a file takes longer, the node skips it and tries the
next scanned file (wrapping only when `media_wrap` is on). 0 means no limit.

Distant video frames are reached with a seek when the file allows it, instead of decoding every frame
from the start. Short distances, files without usable timestamps and failed seeks fall back to the
linear decode, so the result stays correct.

Outputs:
  image        the frame as a ComfyUI IMAGE (1 x height x width x 3, RGB, floats 0..1)
  frame_rate   frames per second reported by the video (0.0 for a still image or unknown)
  frame_count  number of frames in the file (1 for a still image, used by the wrap-around check)
  media_file   full path of the video or image that supplied the frame, file name included
  file_name    file name without folders, with or without its suffix (`include_suffix`)
  errors       empty when all went well, otherwise the message

By default errors never stop the run: the other outputs are empty and `image` is a 1 x 1 black
placeholder, so the nodes after this one still run and the message can be shown by connecting
`errors`. Turn `stop_on_error` on to make the first problem stop the run instead.

Images are read as a single frame (frame 0) with Pillow, which also applies the EXIF orientation.
Videos are decoded with PyAV (`av`), which ships with current ComfyUI, and fall back to OpenCV (`cv2`)
when PyAV cannot open a file. All decoders are optional imports: this module can be imported without
them, and the node only reports a missing decoder when it actually runs.

Paths may be absolute or relative. Relative paths are looked up from ComfyUI's working directory
first and then from its input folder, like the other file nodes in this package. The path is checked
carefully: a missing path, a file that does not match the chosen type, an empty folder and a file
that cannot be decoded all give a clear error instead of an unrelated failure later.

Cache:
  on   ComfyUI caches the result while the node settings stay the same (the file is not read again),
       and the list of files found by a folder scan is remembered, so stepping through frames or a
       folder does not walk the folder again on every queue.
  off  IS_CHANGED returns NaN, so the node scans and reads the media again on every queue. This is
       useful when files are added, removed or replaced on disk while ComfyUI is running.
"""

import importlib
import math
import os
import random
import re
import time

# ---------------------------------------------------------------------------------------------------------------------
# Media file types
# ---------------------------------------------------------------------------------------------------------------------

ALL_MEDIA_TYPES = "All supported media"
ALL_VIDEO_TYPES = "All supported videos"
ALL_IMAGE_TYPES = "All supported images"
CUSTOM_MEDIA_TYPE = "Other (type the extensions below)"

COMMON_VIDEO_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".webm",
    ".mov",
    ".avi",
    ".mkv",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
    ".gif",
    ".ogv",
    ".3gp",
    ".ts",
    ".m2ts",
    ".mts",
    ".mxf",
    ".asf",
    ".vob",
)

COMMON_IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".tga",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pcx",
    ".ico",
    ".avif",
    ".jxl",
)

COMMON_MEDIA_EXTENSIONS = COMMON_VIDEO_EXTENSIONS + COMMON_IMAGE_EXTENSIONS

MEDIA_TYPE_FILTERS = {
    ALL_MEDIA_TYPES: COMMON_MEDIA_EXTENSIONS,
    ALL_VIDEO_TYPES: COMMON_VIDEO_EXTENSIONS,
    ALL_IMAGE_TYPES: COMMON_IMAGE_EXTENSIONS,
    "PNG (.png)": (".png",),
    "JPEG (.jpg, .jpeg)": (".jpg", ".jpeg", ".jpe"),
    "WebP (.webp)": (".webp",),
    "BMP (.bmp)": (".bmp",),
    "TIFF (.tif, .tiff)": (".tif", ".tiff"),
    "MP4 (.mp4, .m4v)": (".mp4", ".m4v"),
    "WebM (.webm)": (".webm",),
    "QuickTime (.mov)": (".mov",),
    "AVI (.avi)": (".avi",),
    "Matroska (.mkv)": (".mkv",),
    "MPEG (.mpg, .mpeg)": (".mpg", ".mpeg"),
    "Windows Media (.wmv, .asf)": (".wmv", ".asf"),
    "Flash (.flv)": (".flv",),
    "GIF (.gif)": (".gif",),
    "Ogg (.ogv)": (".ogv",),
    "3GPP (.3gp)": (".3gp",),
    "Transport stream (.ts, .m2ts, .mts)": (".ts", ".m2ts", ".mts"),
    CUSTOM_MEDIA_TYPE: None,
}
MEDIA_TYPE_CHOICES = list(MEDIA_TYPE_FILTERS.keys())


def parse_extensions(raw) -> tuple:
    """Extensions from user text: ".mp4, mkv" -> (".mp4", ".mkv"). Empty text means no extensions."""
    extensions = []
    for part in re.split(r"[,;\n]", str(raw or "")):
        item = part.strip().strip("\"'").lstrip("*").lower()
        if not item:
            continue
        if item.isalnum():
            item = "." + item
        elif not item.startswith("."):
            item = "." + item
        extensions.append(item)
    return tuple(extensions)


def selected_extensions(media_type, custom_extensions) -> tuple:
    if media_type == CUSTOM_MEDIA_TYPE:
        return parse_extensions(custom_extensions) or COMMON_MEDIA_EXTENSIONS
    return MEDIA_TYPE_FILTERS.get(media_type) or COMMON_MEDIA_EXTENSIONS


def filter_description(media_type, custom_extensions) -> str:
    if media_type == CUSTOM_MEDIA_TYPE:
        extensions = parse_extensions(custom_extensions)
        if extensions:
            return ", ".join(extensions)
        return "supported"
    return media_type


def matches_extension(filename, extensions) -> bool:
    name = str(filename).lower()
    return any(name.endswith(extension) for extension in extensions)


def is_image_file(filename) -> bool:
    """True when the file name has one of the image extensions (so it is read as a still image)."""
    return matches_extension(filename, COMMON_IMAGE_EXTENSIONS)


# ---------------------------------------------------------------------------------------------------------------------
# Paths and scanning
# ---------------------------------------------------------------------------------------------------------------------


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


def path_candidates(raw) -> list:
    path = clean_path(raw)
    if not path:
        raise ValueError(
            "[Media Frame] no path given. Type the path of an image or video file, or of a folder that contains them."
        )
    if os.path.isabs(path):
        return [os.path.normpath(path)]
    candidates = [os.path.abspath(path)]  # relative to ComfyUI's working directory
    input_dir = _input_dir()
    if input_dir:
        candidates.append(os.path.abspath(os.path.join(input_dir, path)))  # then ComfyUI's input folder
    return candidates


def resolve_path(raw) -> str:
    candidates = path_candidates(raw)
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    checked = " and ".join(repr(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"[Media Frame] path not found: checked {checked}. "
        "Check the spelling, or use an absolute path. Relative paths are looked up from ComfyUI's working "
        "directory and then its input folder."
    )


def natural_key(value: str):
    """Sort text with numbers in a human order: clip2 before clip10."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)] + [value.casefold()]


# ---------------------------------------------------------------------------------------------------------------------
# Scan cache
# ---------------------------------------------------------------------------------------------------------------------
# The cache only holds the result of a folder walk (the matching file paths). It is used when the node's
# `cache` switch is on, so stepping through frames or files does not walk the whole folder every queue.
# Turning `cache` off always scans again and refreshes the entry for a later cached run.

SCAN_CACHE_LIMIT = 64
_SCAN_CACHE = {}


def clear_scan_cache() -> None:
    """Forget every remembered folder scan."""
    _SCAN_CACHE.clear()


def _scan_key(path, extensions, recursive):
    return (path, tuple(extensions), bool(recursive))


def _remember_scan(key, found) -> None:
    _SCAN_CACHE.pop(key, None)  # re-insert so the newest entry is last
    _SCAN_CACHE[key] = tuple(found)
    while len(_SCAN_CACHE) > SCAN_CACHE_LIMIT:
        _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)), None)


def scan_media(raw_path, media_type, custom_extensions="", recursive=True, use_cache=True) -> list:
    """
    Every matching media file (video or image) as a full path. A file path is checked against the selected type.

    `use_cache` reuses a previous scan of the same path, filter and recursion setting. The cache is only
    meant for the node's `cache` switch: pass False to force a fresh scan (which also updates the cache).
    """
    path = resolve_path(raw_path)
    extensions = selected_extensions(media_type, custom_extensions)
    description = filter_description(media_type, custom_extensions)
    key = _scan_key(path, extensions, recursive)

    if use_cache:
        cached = _SCAN_CACHE.get(key)
        if cached is not None:
            return list(cached)

    if os.path.isfile(path):
        if not matches_extension(os.path.basename(path), extensions):
            raise ValueError(
                f"[Media Frame] {path} does not match the media_type filter ({description}). "
                f"Change media_type, or use the '{CUSTOM_MEDIA_TYPE}' option and type the file's extension."
            )
        found = [path]
    elif os.path.isdir(path):
        found = []
        try:
            if recursive:
                for dirpath, dirnames, filenames in os.walk(path):
                    dirnames[:] = sorted((name for name in dirnames if not name.startswith(".")), key=str.casefold)
                    for filename in sorted(filenames, key=str.casefold):
                        if filename.startswith("."):
                            continue
                        if matches_extension(filename, extensions):
                            full = os.path.join(dirpath, filename)
                            if os.path.isfile(full):
                                found.append(full)
            else:
                with os.scandir(path) as entries:
                    for entry in entries:
                        if entry.name.startswith(".") or not entry.is_file():
                            continue
                        if matches_extension(entry.name, extensions):
                            found.append(entry.path)
        except OSError as error:
            raise OSError(f"[Media Frame] could not read folder {path}: {error}") from None
    else:
        raise ValueError(f"[Media Frame] path is not a file or a folder: {path}")

    if not found:
        where = "and its subfolders" if recursive else "only in that folder"
        hint = (
            "Check media_type."
            if recursive
            else "Check media_type, or switch recursive on to search subfolders."
        )
        if media_type == ALL_MEDIA_TYPES:
            what = "supported media"
        elif media_type == ALL_VIDEO_TYPES:
            what = "supported video"
        elif media_type == ALL_IMAGE_TYPES:
            what = "image"
        else:
            what = description
        raise LookupError(
            f"[Media Frame] no {what} files were found in {path} ({where}). {hint}"
        )

    if recursive:
        found.sort(key=lambda item: natural_key(os.path.relpath(item, path)))
    else:
        found.sort(key=lambda item: natural_key(os.path.basename(item)))
    _remember_scan(key, found)
    return found


def select_index(items, index, wrap, name, kind="item"):
    """The item at `index`, wrapped around the list when `wrap` is on."""
    count = len(items)
    index = int(index)
    if wrap:
        return items[index % count], index % count
    if not 0 <= index < count:
        noun = kind if count == 1 else kind + "s"
        raise IndexError(
            f"[Media Frame] {name} {index} is out of range: {count} {noun} found, "
            f"valid indexes are 0 to {count - 1}. Switch the matching wrap option on to wrap the index around."
        )
    return items[index], index


def select_media(media, index, wrap, skip_unknown_count):
    """
    The media file to read and its probed information.

    With `skip_unknown_count` on, files whose container does not store an exact frame count are passed
    over and the next scanned file is tried, so the node does not have to decode a whole file just to
    count its frames. The search starts at `index`; it wraps around only when `wrap` is on. Images always
    have an exact frame count (1), so they are never skipped.
    """
    if not skip_unknown_count:
        media_path, _index = select_index(media, index, wrap, "media_index", "file")
        return media_path, probe_media(media_path)

    count = len(media)
    index = int(index)
    if not wrap:
        select_index(media, index, False, "media_index", "file")  # raises the usual out-of-range message
    start = index % count if wrap else index
    checked = count if wrap else count - start

    for step in range(checked):
        candidate = media[(start + step) % count]
        info = probe_media(candidate)
        if info.exact_count and info.frame_count > 0:
            return candidate, info
        print(
            f"[Media Frame] skipping {candidate}: the file does not store its frame count.",
            flush=True,
        )

    noun = "file" if checked == 1 else "files"
    raise RuntimeError(
        f"[Media Frame] none of the {checked} {noun} checked stores its frame count. "
        "Switch 'skip files without a stored frame count' off to count the frames by decoding them, "
        "or choose another path or media_index."
    )


# ---------------------------------------------------------------------------------------------------------------------
# Media decoding (still images with Pillow, videos with PyAV and OpenCV as a fallback)
# ---------------------------------------------------------------------------------------------------------------------


class MediaInfo:
    __slots__ = ("fps", "frame_count", "exact_count", "backend")

    def __init__(self, fps, frame_count, exact_count, backend):
        self.fps = fps
        self.frame_count = frame_count
        self.exact_count = exact_count
        self.backend = backend


class MediaTimeout(RuntimeError):
    """One media file took longer than the allowed effort time."""


def media_deadline(max_seconds):
    """`max_seconds` from now as a monotonic deadline, or None when there is no limit."""
    try:
        seconds = float(max_seconds or 0.0)
    except (TypeError, ValueError):
        raise ValueError(
            f"[Media Frame] max_seconds_per_file must be a number of seconds, not {max_seconds!r}."
        ) from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(
            f"[Media Frame] max_seconds_per_file must be 0 (no limit) or a positive number, not {max_seconds!r}."
        )
    return None if seconds == 0 else time.monotonic() + seconds


def check_deadline(deadline, path, stage):
    """Raise MediaTimeout when this file has used up its effort time."""
    if deadline is not None and time.monotonic() >= deadline:
        raise MediaTimeout(f"{stage} {os.path.basename(path)} took too long")


def _av_rate(stream) -> float:
    """The best frame rate a PyAV stream reports, or 0.0 when it has none."""
    for rate in (stream.average_rate, stream.guessed_rate, stream.base_rate):
        if rate:
            return float(rate)
    return 0.0


def _av_stream(container):
    streams = container.streams.video
    if not streams:
        raise ValueError("the file has no video stream")
    return streams[0]


def _probe_av(path, deadline=None):
    import av

    check_deadline(deadline, path, "opening")
    with av.open(path) as container:
        stream = _av_stream(container)
        fps = _av_rate(stream)

        frame_count = int(stream.frames or 0)
        exact = frame_count > 0
        duration = 0.0
        if stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration = float(container.duration) / 1_000_000.0  # av.time_base is microseconds

        if not exact and duration > 0 and fps > 0:
            frame_count = int(round(duration * fps))
        if fps <= 0 and frame_count > 0 and duration > 0:
            fps = frame_count / duration
        check_deadline(deadline, path, "probing")
        return MediaInfo(max(0.0, fps), max(0, frame_count), exact, "PyAV")


def _probe_cv2(path, deadline=None):
    import cv2

    check_deadline(deadline, path, "opening")
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise ValueError("OpenCV could not open the file")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    check_deadline(deadline, path, "probing")
    return MediaInfo(max(0.0, fps), max(0, frame_count), frame_count > 0, "OpenCV")


def probe_video(path, deadline=None) -> MediaInfo:
    """Frame rate and frame count of a video, using PyAV first and OpenCV when PyAV cannot open the file."""
    errors = []
    try:
        return _probe_av(path, deadline)
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"PyAV: {error}")
    try:
        return _probe_cv2(path, deadline)
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"OpenCV: {error}")

    missing = all("No module named" in message for message in errors)
    if missing:
        raise RuntimeError(
            "[Media Frame] no video decoder is installed. Install PyAV (pip install av) or OpenCV "
            "(pip install opencv-python) in the Python environment that runs ComfyUI. "
            + " | ".join(errors)
        ) from None
    raise RuntimeError(f"[Media Frame] could not read video information from {path}. " + " | ".join(errors)) from None


def probe_media(path, deadline=None) -> MediaInfo:
    """Information about an image or a video. A still image is one frame with a frame rate of 0.0."""
    check_deadline(deadline, path, "reading")
    if is_image_file(path):
        return MediaInfo(0.0, 1, True, "image")
    return probe_video(path, deadline)


def count_frames_av(path, deadline=None) -> int:
    import av

    check_deadline(deadline, path, "opening")
    with av.open(path) as container:
        stream = _av_stream(container)
        count = 0
        for _frame in container.decode(stream):
            count += 1
            if count % 30 == 0:
                check_deadline(deadline, path, "counting frames in")
        return count


def count_frames_cv2(path, deadline=None) -> int:
    import cv2

    check_deadline(deadline, path, "opening")
    capture = cv2.VideoCapture(path)
    count = 0
    try:
        if not capture.isOpened():
            raise ValueError("OpenCV could not open the file")
        while True:
            ok, _frame = capture.read()
            if not ok:
                break
            count += 1
            if count % 30 == 0:
                check_deadline(deadline, path, "counting frames in")
    finally:
        capture.release()
    return count


def count_frames(path, deadline=None) -> int:
    """Count every frame by decoding the file (only needed when the container does not store the count)."""
    errors = []
    for function in (count_frames_av, count_frames_cv2):
        try:
            return int(function(path, deadline))
        except MediaTimeout:
            raise
        except Exception as error:
            errors.append(f"{function.__name__.replace('count_frames_', '')}: {error}")
    raise RuntimeError(f"[Media Frame] could not count the frames in {path}. " + " | ".join(errors)) from None


# Seeking only helps once a frame is far enough into the file; short walks are faster to decode linearly.
AV_SEEK_MIN_INDEX = 60


def _decode_linear(container, stream, path, index, deadline):
    for current, frame in enumerate(container.decode(stream)):
        check_deadline(deadline, path, "decoding")
        if current == index:
            return frame.to_ndarray(format="rgb24")
    raise IndexError(f"frame {index} was not found")


def read_frame_av(path, index, deadline=None):
    """
    One PyAV frame. A distant frame is reached by seeking to its time first, so the decoder does not have
    to walk the whole file; the exact frame is then found from the decoded timestamps. Short distances and
    files whose seek or timestamps fail fall back to decoding from the start.
    """
    import av

    check_deadline(deadline, path, "opening")
    with av.open(path) as container:
        stream = _av_stream(container)
        rate = _av_rate(stream)
        if index >= AV_SEEK_MIN_INDEX and rate > 0 and stream.time_base is not None:
            target_time = index / rate
            try:
                # backward seek lands on the keyframe at or before the target time
                container.seek(
                    max(0, int(target_time / float(stream.time_base))),
                    stream=stream,
                    backward=True,
                    any_frame=False,
                )
                check_deadline(deadline, path, "seeking in")
                for frame in container.decode(stream):
                    check_deadline(deadline, path, "decoding")
                    if frame.time is None:
                        break  # timestamps are unusable: fall back to the linear decode
                    if frame.time + (0.5 / rate) >= target_time:
                        return frame.to_ndarray(format="rgb24")
            except MediaTimeout:
                raise
            except Exception:
                pass  # seek or timestamps did not work: reopen and decode linearly
        else:
            return _decode_linear(container, stream, path, index, deadline)

    # Linear fallback: reopen the file because the failed seek may have moved the decoder.
    with av.open(path) as container:
        return _decode_linear(container, _av_stream(container), path, index, deadline)


def read_frame_cv2(path, index, deadline=None):
    import cv2

    check_deadline(deadline, path, "opening")
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise ValueError("OpenCV could not open the file")
        if index > 0:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        check_deadline(deadline, path, "decoding")
        if not ok or frame is None:
            # Some codecs ignore the seek request. Read from the start instead.
            capture.release()
            capture = cv2.VideoCapture(path)
            for _ in range(index + 1):
                ok, frame = capture.read()
                if _ % 30 == 0:
                    check_deadline(deadline, path, "decoding")
                if not ok or frame is None:
                    break
        if not ok or frame is None:
            raise IndexError(f"frame {index} was not found")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        capture.release()


def read_frame(path, index, deadline=None):
    """One RGB uint8 frame (height x width x 3), trying PyAV first and OpenCV afterwards."""
    errors = []
    try:
        return read_frame_av(path, index, deadline)
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"PyAV: {error}")
    try:
        return read_frame_cv2(path, index, deadline)
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"OpenCV: {error}")
    raise RuntimeError(
        f"[Media Frame] could not read frame {index} from {path}. " + " | ".join(errors)
    ) from None


def read_image(path, deadline=None):
    """One RGB uint8 still image (height x width x 3), using Pillow first and PyAV / OpenCV afterwards."""
    errors = []
    check_deadline(deadline, path, "reading")
    try:
        from PIL import Image, ImageOps
        import numpy as np

        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            result = np.asarray(image.convert("RGB"), dtype=np.uint8)
        check_deadline(deadline, path, "reading")
        return result
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"Pillow: {error}")
    try:
        return read_frame(path, 0, deadline)
    except MediaTimeout:
        raise
    except Exception as error:
        errors.append(f"PyAV/OpenCV: {error}")
    raise RuntimeError(f"[Media Frame] could not read image {path}. " + " | ".join(errors)) from None


def read_media_frame(path, index, deadline=None):
    """One RGB uint8 frame from a video, or the single frame of a still image."""
    if is_image_file(path):
        if int(index) != 0:
            raise IndexError(f"[Media Frame] image {path} has only one frame (frame 0).")
        return read_image(path, deadline)
    return read_frame(path, index, deadline)


def error_message(error) -> str:
    """A readable one-line message, always starting with the node's tag."""
    text = str(error).strip()
    if text.startswith("[Media Frame]"):
        return text
    return f"[Media Frame] {type(error).__name__}: {text}"


def image_from_rgb(rgb):
    """An RGB uint8 frame as a ComfyUI IMAGE tensor (or a numpy array outside ComfyUI)."""
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("[Media Frame] numpy is required (it ships with ComfyUI).") from None

    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.ndim == 2:  # some decoders return a grayscale frame
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    elif rgb.ndim == 3 and rgb.shape[2] == 4:
        rgb = rgb[:, :, :3]
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise RuntimeError(f"[Media Frame] the decoder returned an unexpected frame shape: {rgb.shape}")

    floats = rgb.astype(np.float32) / 255.0
    try:
        import torch

        return torch.from_numpy(floats).unsqueeze(0)
    except ImportError:  # lets the helpers be used outside ComfyUI
        return floats[None, ...]


def empty_image():
    """A 1 x 1 black IMAGE, used when an error is reported instead of a frame."""
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("[Media Frame] numpy is required (it ships with ComfyUI).") from None
    return image_from_rgb(np.zeros((1, 1, 3), dtype=np.uint8))


def output_file_name(path, include_suffix=True) -> str:
    """The file name without folders, with or without its extension."""
    name = os.path.basename(path)
    return name if include_suffix else os.path.splitext(name)[0]


# ---------------------------------------------------------------------------------------------------------------------
# Frame percentage range
# ---------------------------------------------------------------------------------------------------------------------

DEFAULT_START_PERCENT = 0.0
DEFAULT_END_PERCENT = 100.0


def range_is_narrowed(start_percent, end_percent) -> bool:
    """True when the percentage range is not the full 0..100 range (so videos pick a random frame)."""
    try:
        return float(start_percent) != DEFAULT_START_PERCENT or float(end_percent) != DEFAULT_END_PERCENT
    except (TypeError, ValueError):
        return True  # let check_percent_range() report the real problem


def check_percent_range(start_percent, end_percent):
    """Validate the percentage fields and return them as floats. Raises ValueError with a clear message."""
    values = []
    for name, value in (("start_percent", start_percent), ("end_percent", end_percent)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"[Media Frame] {name} must be a number between 0 and 100, not {value!r}."
            ) from None
        if not math.isfinite(number):
            raise ValueError(f"[Media Frame] {name} must be a number between 0 and 100, not {value!r}.")
        if not 0.0 <= number <= 100.0:
            raise ValueError(f"[Media Frame] {name} is {number:g}, but it must be between 0 and 100.")
        values.append(number)
    start, end = values
    if start > end:
        raise ValueError(
            f"[Media Frame] start_percent ({start:g}) is past end_percent ({end:g}). "
            "Set start_percent to a value less than or equal to end_percent, or raise end_percent."
        )
    return start, end


def percent_frame_window(frame_count, start_percent, end_percent):
    """The inclusive first and last frame of a video for a validated percentage range."""
    count = int(frame_count)
    if count <= 0:
        raise ValueError("[Media Frame] cannot choose a frame from a file without frames.")
    start, end = check_percent_range(start_percent, end_percent)
    first = min(count - 1, int(math.floor(start / 100.0 * count)))
    last = min(count - 1, int(math.ceil(end / 100.0 * count)) - 1)
    if last < first:  # a collapsed range (for example 50% to 50%) still means one frame
        last = first
    return first, last


def candidate_media(media, index, wrap):
    """The files to try, starting at `index`. Earlier files are only tried when `wrap` is on."""
    count = len(media)
    if wrap:
        start = int(index) % count
        return [media[(start + step) % count] for step in range(count)]
    select_index(media, index, False, "media_index", "file")  # raises the usual out-of-range message
    return list(media[int(index):])


def selected_frame(path, info, frame_index, frame_wrap, start_percent, end_percent, deadline=None):
    """Read the desired frame from one probed file. Returns (rgb, frame_count, frame_rate)."""
    frame_count = int(info.frame_count) if info.exact_count and info.frame_count > 0 else 0
    if frame_count <= 0:
        print(f"[Media Frame] the file does not store its frame count; counting frames in {path} ...", flush=True)
        frame_count = count_frames(path, deadline)
    if frame_count <= 0:
        raise RuntimeError(f"[Media Frame] could not find any frames in {path}.")

    if not is_image_file(path) and (start_percent != DEFAULT_START_PERCENT or end_percent != DEFAULT_END_PERCENT):
        first, last = percent_frame_window(frame_count, start_percent, end_percent)
        frame = random.randint(first, last)
        print(
            f"[Media Frame] random frame {frame} of {frame_count} "
            f"(range {start_percent:g}%..{end_percent:g}% = frames {first}..{last})",
            flush=True,
        )
    else:
        frame, _frame_index = select_index(
            range(frame_count), frame_index, bool(frame_wrap), "frame_index", "frame"
        )

    rgb = read_media_frame(path, int(frame), deadline)
    frame_rate = float(info.fps or 0.0)
    if not math.isfinite(frame_rate) or frame_rate < 0:
        frame_rate = 0.0
    return rgb, frame_count, frame_rate


# ---------------------------------------------------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------------------------------------------------


class MediaFrame:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": r"C:\media\clip.mp4  or  C:\media\still.png  or  C:\media\folder",
                        "tooltip": "Type the media file to use (an image or a video), or a folder that contains them. Relative paths are looked up from ComfyUI's working folder and then its input folder. The node checks that the path exists and that it is a file or folder before it reads anything.",
                    },
                ),
                "include_suffix": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "file name with suffix",
                        "label_off": "file name without suffix",
                        "tooltip": "Only affects the file_name output. On: the name keeps its extension, for example clip.mp4. Off: the extension is removed, for example clip. The media_file output is always the full path, and this setting does not change which file is read.",
                    },
                ),
                "stop_on_error": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "stop the run on error",
                        "label_off": "report error only",
                        "tooltip": "Off: a problem is written to the errors output, image is a 1 x 1 black placeholder and the workflow keeps running. On: the first problem stops the run with the same message, and ComfyUI reports it as an error (the errors output is then not produced).",
                    },
                ),
                "media_type": (
                    MEDIA_TYPE_CHOICES,
                    {
                        "default": ALL_MEDIA_TYPES,
                        "tooltip": "Which file types to look for. Images and videos are supported, and a folder is scanned for the chosen types. A file path is also checked against this filter, so a PNG needs a filter that includes .png.",
                    },
                ),
                "media_type_custom": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": ".mp4, .mkv, .png, .jpg   (empty = all supported media)",
                        "tooltip": "Only used when media_type is 'Other'. Type the extensions to look for, separated by commas, semicolons or new lines. For example: .mp4, .mkv, .png, .jpg. Leave it empty to use every supported media type.",
                    },
                ),
                "recursive": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "scan subfolders",
                        "label_off": "this folder only",
                        "tooltip": "On: search the folder and all of its subfolders for media files. Off: search only the folder you typed. Hidden folders and files (names starting with a dot) are always skipped.",
                    },
                ),
                "cache": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "cache result",
                        "label_off": "read again every run",
                        "tooltip": "On: ComfyUI reuses the result while these settings stay the same, and the list of files found by a folder scan is remembered so the folder is not walked again for every frame. Off: scan the folder and read the media again on every queue, which is useful when files are added, removed or replaced on disk while ComfyUI is running.",
                    },
                ),
                "media_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": -1000000000,
                        "max": 1000000000,
                        "step": 1,
                        "control_after_generate": True,
                        "tooltip": "Which media file to use when the path is a folder with several matches. 0 is the first file, in natural order (clip2 comes before clip10). The control after generate can keep it fixed, add one, subtract one, or choose a random file after every run.",
                    },
                ),
                "media_wrap": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "wrap media index",
                        "label_off": "error if out of range",
                        "tooltip": "On: a media index past the last file wraps around to the first one, and a negative index counts from the end. Off: an out-of-range index stops the run with a message that shows how many media files were found.",
                    },
                ),
                "frame_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": -1000000000,
                        "max": 1000000000,
                        "step": 1,
                        "control_after_generate": True,
                        "tooltip": "Which frame to return, starting at 0 (the first frame). The control after generate can keep it fixed, add one, subtract one, or choose a random frame after every run.",
                    },
                ),
                "frame_wrap": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "wrap frame index",
                        "label_off": "error if out of range",
                        "tooltip": "On: a frame index past the last frame wraps around to the first one, and a negative index counts from the end. Off: an out-of-range index stops the run and tells you how many frames the file has. A still image has one frame, frame 0.",
                    },
                ),
                "start_percent": (
                    "FLOAT",
                    {
                        "default": DEFAULT_START_PERCENT,
                        "min": 0.0,
                        "max": 100.0,
                        "step": 0.1,
                        "tooltip": "Videos only. Start of the frame range as a percentage of the video: 0 is the first frame. With start_percent 0 and end_percent 100 the normal frame_index / frame_wrap controls are used; with any other range a random frame inside the range is returned and frame_index is ignored for videos. Images ignore this range. It must not be greater than end_percent.",
                    },
                ),
                "end_percent": (
                    "FLOAT",
                    {
                        "default": DEFAULT_END_PERCENT,
                        "min": 0.0,
                        "max": 100.0,
                        "step": 0.1,
                        "tooltip": "Videos only. End of the frame range as a percentage of the video: 100 is the last frame. With start_percent 0 and end_percent 100 the normal frame_index / frame_wrap controls are used; with any other range a random frame inside the range is returned and frame_index is ignored for videos. Images ignore this range. It must not be less than start_percent.",
                    },
                ),
                "max_seconds_per_file": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 3600.0,
                        "step": 0.5,
                        "tooltip": "Maximum effort time for one file, in seconds. Opening, probing, counting frames and decoding the frame are checked against it; once it is exceeded the node skips the file and tries the next matching file (wrapping only when media_wrap is on), so one slow or damaged video does not stall the queue. 0 = no limit (default). This is per file, not for the whole run.",
                    },
                ),
                "skip_unknown_frame_count": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "skip files without a stored frame count",
                        "label_off": "count frames when the count is missing",
                        "tooltip": "On: a file whose container does not store its frame count is ignored, and the next matching media file is tried instead (the search starts at media_index and wraps around only when media_wrap is on). This avoids decoding a whole video just to count its frames. Off: such a video is decoded once to count its frames, as before. Images always have a known count and are never skipped. A file that cannot be opened at all is still reported as a decoder error.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "FLOAT", "INT", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("image", "frame_rate", "frame_count", "media_file", "file_name", "errors")
    OUTPUT_TOOLTIPS = (
        "The chosen video frame or still image as an image.",
        "Frame rate reported by the video, in frames per second. It is 0.0 for a still image or when the file does not contain this information.",
        "Number of frames in the chosen file. This is the number used by the frame wrap-around check; a still image has one frame.",
        "Full path of the video or image that supplied the frame, including the file name.",
        "File name of the video or image that supplied the frame, without folders. It includes the extension when include_suffix is on and is only the name when it is off.",
        "Empty when the frame was read. Otherwise the error message; image is then a 1 x 1 black placeholder and the other outputs are empty (frame_rate 0.0, frame_count 0). With stop_on_error on, the problem stops the run instead.",
    )
    FUNCTION = "get_frame"
    CATEGORY = "MultiTabText"

    @classmethod
    def IS_CHANGED(cls, cache=True, start_percent=DEFAULT_START_PERCENT, end_percent=DEFAULT_END_PERCENT, **kwargs):
        # Caching on: a stable value lets ComfyUI reuse the result while the settings are unchanged.
        # Caching off: NaN never equals itself, so ComfyUI runs the node again on every queue.
        # A narrowed percentage range picks a random video frame, so the node has to run again to re-roll it.
        if not cache or range_is_narrowed(start_percent, end_percent):
            return float("nan")
        return ""

    def get_frame(self, path="", media_type=ALL_MEDIA_TYPES, media_type_custom="", recursive=True,
                  cache=True, media_index=0, media_wrap=True, frame_index=0, frame_wrap=True,
                  start_percent=DEFAULT_START_PERCENT, end_percent=DEFAULT_END_PERCENT,
                  max_seconds_per_file=0.0, skip_unknown_frame_count=False, include_suffix=True,
                  stop_on_error=False):
        try:
            return self._get_frame(
                path, media_type, media_type_custom, recursive, cache, media_index, media_wrap,
                frame_index, frame_wrap, start_percent, end_percent, max_seconds_per_file,
                skip_unknown_frame_count, include_suffix,
            )
        except Exception as error:  # every problem ends up in the `errors` output instead of stopping the run
            message = error_message(error)
            print(message, flush=True)  # also visible in the console, in case `errors` is not connected
            if stop_on_error:
                raise  # keep the original traceback; ComfyUI reports it as a failed run
            try:
                image = empty_image()
            except Exception:
                raise error  # without numpy/torch there is no image to return: keep the original failure
            return (image, 0.0, 0, "", "", message)

    def _get_frame(self, path="", media_type=ALL_MEDIA_TYPES, media_type_custom="", recursive=True,
                   cache=True, media_index=0, media_wrap=True, frame_index=0, frame_wrap=True,
                   start_percent=DEFAULT_START_PERCENT, end_percent=DEFAULT_END_PERCENT,
                   max_seconds_per_file=0.0, skip_unknown_frame_count=False, include_suffix=True):
        start_percent, end_percent = check_percent_range(start_percent, end_percent)
        media_deadline(max_seconds_per_file)  # validates the effort limit before anything is scanned
        media = scan_media(path, media_type, media_type_custom, bool(recursive), use_cache=bool(cache))
        candidates = candidate_media(media, media_index, bool(media_wrap))

        timed_out = []
        skipped_unknown = []
        for media_path in candidates:
            deadline = media_deadline(max_seconds_per_file)
            try:
                info = probe_media(media_path, deadline)
                if skip_unknown_frame_count and not info.exact_count:
                    print(
                        f"[Media Frame] skipping {media_path}: the file does not store its frame count.",
                        flush=True,
                    )
                    skipped_unknown.append(media_path)
                    continue
                rgb, frame_count, frame_rate = selected_frame(
                    media_path, info, frame_index, frame_wrap, start_percent, end_percent, deadline
                )
                break
            except MediaTimeout as error:
                timed_out.append(media_path)
                print(
                    f"[Media Frame] skipping {media_path}: {error} "
                    f"(max_seconds_per_file={float(max_seconds_per_file):g} s).",
                    flush=True,
                )
        else:
            if timed_out:
                noun = "file" if len(candidates) == 1 else "files"
                raise RuntimeError(
                    f"[Media Frame] no media could be read within {float(max_seconds_per_file):g} s per file "
                    f"({len(timed_out)} of {len(candidates)} {noun} timed out"
                    + (f", {len(skipped_unknown)} skipped for a missing frame count" if skipped_unknown else "")
                    + "). Raise max_seconds_per_file, switch skip_unknown_frame_count off, or choose another "
                    "path or media_index."
                )
            noun = "file" if len(candidates) == 1 else "files"
            raise RuntimeError(
                f"[Media Frame] none of the {len(candidates)} {noun} checked stores its frame count. "
                "Switch 'skip files without a stored frame count' off to count the frames by decoding them, "
                "or choose another path or media_index."
            )

        image = image_from_rgb(rgb)
        media_file = os.path.abspath(media_path)
        return (image, frame_rate, int(frame_count), media_file,
                output_file_name(media_path, bool(include_suffix)), "")


NODE_CLASS_MAPPINGS = {"MultiTabTextMediaFrame": MediaFrame}
NODE_DISPLAY_NAME_MAPPINGS = {"MultiTabTextMediaFrame": "Media Frame (MultiTabText)"}
