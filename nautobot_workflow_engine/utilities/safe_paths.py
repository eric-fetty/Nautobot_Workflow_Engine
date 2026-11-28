"""Join repository-relative paths without letting them leave the repository."""

import os
from pathlib import Path


class UnsafePathError(ValueError):
    """A relative path that would point outside its base directory."""


def contained_path(base, relative):
    """Return ``base / relative`` as a resolved :class:`~pathlib.Path` that stays inside ``base``.

    Absolute paths, ``..`` segments, and symlinks that point elsewhere are refused with
    :class:`UnsafePathError`. The base directory itself is refused too: callers always want a file.
    """
    text = str(relative or "")
    if not text or "\x00" in text:
        raise UnsafePathError("The path is empty or not valid.")
    root = os.path.realpath(os.fspath(base))
    candidate = os.path.realpath(os.path.join(root, text))
    if not candidate.startswith(root + os.sep):
        raise UnsafePathError(f"{text} is outside the repository.")
    return Path(candidate)
