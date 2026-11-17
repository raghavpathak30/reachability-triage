# A1b pre-registered expectations (scope-cut: uv workspaces and the ValueError fix)

Written by hand before any A1b parser code. Rules are from `.agent/plan.md` design decision 1 and DECISIONS §27.

## uv_ws_one
No package has `editable = "."`. `[manifest] members = ["app"]` is present, so the candidates are the packages named in `members` whose source has a string `editable` or `virtual`: only `app` (`editable = "backend"`). One candidate, so it is selected (decision 1, one candidate). The runtime closure from `app` follows `click` (no marker, pinned `8.1.7`) and `colorama` (edge marker `sys_platform == 'win32'` is false for the deployed Linux env, so `marker_skipped` with version `0.4.6`). `pytest` is only in `[manifest.dependency-groups] dev`, no edge from `app` reaches it, so it is absent. `app` is the root and never a row. Non-G5: `app_root == "backend"`, `member == "app"`, lock note `uv workspace member 'app' selected (editable = 'backend')`.

## uv_ws_two
No `"."` root; `members = ["api", "worker"]` gives two candidates. Without `--member` this is a `UvLockRootError` listing both and naming `--member` (decision 1, several candidates). Non-G5: member `worker` selects `worker`, closure is `requests | 2.32.3 | pinned` only, `app_root == "services/worker"`; member `api` gives only `click | 8.1.7 | pinned`, `app_root == "services/api"`; member `nope` names no candidate and raises `UvLockRootError` (unknown member).

## uv_ws_escape
`evil` is the sole member candidate with `editable = "../x"`. The path has a `..` component, so the member-path check (decision 2) refuses it with `UvLockRootError` naming member, path and reason. G5 expectation is `raises: UvLockRootError`.

## req_huge_digit
`hugever==` followed by 5000 nines. A version that is not a valid PEP 440 version on an `==` pin is `unsupported` (DECISIONS §27); a 5000-digit integer exceeds Python's 4300-digit int conversion limit and raises a bare `ValueError`, not `InvalidVersion`. Following the convention of `req_unpinned_kinds` `d1` (`1.0-custom`), the version field is `null` and the dist is kept (`hugever`). Neighbours `click==8.1.7` and `idna==3.10` stay `pinned`. If the step-7 run disagrees, that is reported, the expectation is not edited.
