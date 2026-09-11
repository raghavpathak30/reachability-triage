from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import TriageRequest


class AcquisitionError(RuntimeError):
    pass


def acquire_source(request: TriageRequest, workdir: Path) -> Path:
    """
    Resolve a TriageRequest's package+version target into an extracted
    source tree under `workdir` and return the extracted root, for
    build_repo_index (U1, src/reachability/triage/index_adapter.py) to
    consume directly.

    Wheel-only, non-negotiable: acquisition always runs
    `pip download --only-binary=:all:`, never plain `pip download`. A
    plain `pip download` against an sdist-only distribution can invoke
    that distribution's own PEP 517 build backend to produce metadata —
    i.e. execute attacker-controlled code on this host, while acquiring
    the very package being triaged for an unrelated vulnerability. Phase
    2 has no sandboxing (Docker is still NOT BUILT) to contain that risk.
    A package with no wheel for the running platform/Python is therefore
    treated as an unresolvable target, never as a build attempt to fall
    back on — this constraint is never relaxed, including to make a test
    pass.

    A `repo_url` request fails fast with AcquisitionError("not supported
    yet") before any directory is created or any network call is made.
    Git-clone acquisition and its SSRF hardening are deferred to Phase 5
    (DECISIONS.md:97-98) and are not implemented here, even partially.

    `workdir` is caller-owned: this function creates only a "download"
    and an "extracted" subdirectory beneath it, does no caching across
    calls, and does not delete anything on success or failure. Cleaning
    up `workdir` itself is the caller's responsibility.

    Known limitation, not fixed here: pip emits the identical
    "Could not find a version that satisfies the requirement" /
    "No matching distribution found" text both for a package+version that
    has no wheel and for one that does not exist at all (e.g. a typo).
    Both cases are classified here as "no wheel available for X==Y" --
    still a fail-loud, named AcquisitionError in either case, never a
    silent failure or a build fallback, but the message may misname a
    plain typo as a missing-wheel condition. Left as a residual gap
    rather than a deeper pip-output parse, since both underlying causes
    resolve to the same correct action (reject, do not build).

    Residual assumption, not empirically verified in this environment:
    that --only-binary=:all: never invokes the target package's own PEP
    517 build backend even when no wheel exists. This follows from
    documented pip behavior, but no genuinely sdist-only package could be
    found in this environment's package index to observe it directly
    (see .agent/exploration.md's 8 failed probe attempts) -- the
    "no wheel available" test below exercises this function's own
    response to that pip failure via a mocked subprocess call, not a live
    observation of pip itself refusing to build.
    """
    if request.repo_url is not None:
        raise AcquisitionError("not supported yet")

    spec = f"{request.package}=={request.version}"

    workdir.mkdir(parents=True, exist_ok=True)
    download_dir = workdir / "download"
    download_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--no-deps",
        "--dest",
        str(download_dir),
        spec,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise AcquisitionError(f"pip download failed to start for {spec}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AcquisitionError(
            f"pip download timed out after 120s for {spec}: {exc}"
        ) from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if (
            "Could not find a version that satisfies the requirement" in stderr
            or "No matching distribution found" in stderr
        ):
            raise AcquisitionError(f"no wheel available for {spec}: {stderr}")
        raise AcquisitionError(
            f"pip download failed for {spec} (exit code {proc.returncode}): {stderr}"
        )

    wheels = sorted(download_dir.glob("*.whl"))
    if not wheels:
        raise AcquisitionError(
            f"no wheel available for {spec}: pip download exited 0 but produced no .whl file"
        )
    if len(wheels) > 1:
        raise AcquisitionError(
            f"pip download for {spec} produced multiple wheel files, expected exactly one: "
            f"{[w.name for w in wheels]}"
        )

    wheel_path = wheels[0]
    extract_dir = workdir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(wheel_path) as zf:
            zf.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        raise AcquisitionError(
            f"wheel extraction failed for {wheel_path.name} ({spec}): {exc}"
        ) from exc

    return extract_dir
