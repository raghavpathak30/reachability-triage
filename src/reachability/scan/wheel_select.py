import re
from dataclasses import dataclass

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

# Target environment (explicit, not host-derived): CPython 3.13, manylinux, x86_64.
_MANYLINUX_X86_64 = re.compile(r"manylinux(?:1|2010|2014|_\d+_\d+)_x86_64")
_CP3_ABI3 = re.compile(r"cp3(\d+)")


@dataclass(frozen=True)
class Candidate:
    filename: str
    url: str
    sha256: str | None


def _platform_ok(platform: str) -> bool:
    return platform == "any" or platform == "linux_x86_64" or _MANYLINUX_X86_64.fullmatch(platform) is not None


def _tag_rank(interpreter: str, abi: str, platform: str) -> int | None:
    """Lower is better; None means the tag does not match the target environment."""
    if not _platform_ok(platform):
        return None
    if interpreter == "cp313" and abi in ("cp313", "abi3", "none"):
        return 1 if abi == "cp313" else 2
    m = _CP3_ABI3.fullmatch(interpreter)
    if m and abi == "abi3" and int(m.group(1)) <= 13:
        return 2
    if interpreter in ("py3", "py313") and abi == "none":
        return 0 if platform == "any" else 3
    return None


def rank_wheel(filename: str) -> int | None:
    """py3-none-any ranks 0; other matching tags rank higher; a non-match is None."""
    try:
        _, _, _, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None
    ranks = [r for t in tags if (r := _tag_rank(t.interpreter, t.abi, t.platform)) is not None]
    return min(ranks) if ranks else None


def choose_wheel(candidates: list[Candidate]) -> Candidate | None:
    ranked = [(rank_wheel(c.filename), c.filename, c) for c in candidates]
    ranked = [r for r in ranked if r[0] is not None]
    if not ranked:
        return None
    return min(ranked, key=lambda r: (r[0], r[1]))[2]
