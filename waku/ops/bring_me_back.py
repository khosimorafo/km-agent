"""`python -m waku bring-me-back [project]` — reconstruct a project's state from its map.

Reads a project harness's `project.toml` (the knowledge map) and answers the five
questions a returning founder asks: what was I trying to accomplish, where did I
leave it, what changed while I was away, which decisions are unresolved, and what
are the three most consequential next things.

The computation lives in `state()` — one source for two renderers: the CLI text
(`bring_me_back`) and the dashboard's Project tab (via `/api/data`). They can
never disagree because they both read the same dict.

Deterministic and stdlib-only: it stats the files the map points at, compares
their mtime against each entry's `verified` date, and ranks by status. No model,
no network, no key. See docs/project-harness.md.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

from waku.runtime.knowledge import load_map
from waku.tools.authority import authority_to_sandbox

CONFIG = "project.toml"


def _changed_since_verified(entry: dict, project: Path) -> bool:
    """True when the file an entry points at changed after its `verified` date
    (or the referenced file is missing — drift either way)."""
    p = (entry.get("path") or "").strip()
    if not p or p == "none":
        return False
    p = p.split("#", 1)[0].strip()   # a section anchor (file#section) → the file
    if not p:
        return False
    f = project / p
    if not f.exists():
        return True
    if not f.is_file():
        return False   # a directory (e.g. reviews/): existence is the only check
    verified = entry.get("verified")
    if not verified:
        return False
    try:
        d = date.fromisoformat(str(verified))
    except ValueError:
        return False
    return f.stat().st_mtime > datetime.combine(d, time.min).timestamp()


def _exists(entry: dict, project: Path) -> bool | None:
    """Whether the file an entry points at exists. None for a recorded claim
    (`none`) or a directory — existence is not a meaningful question there."""
    p = (entry.get("path") or "").strip()
    if not p or p == "none":
        return None
    p = p.split("#", 1)[0].strip()
    if not p:
        return None
    f = project / p
    if f.is_dir():
        return None
    return f.is_file()


def state(project: Path) -> dict:
    """The harness computation as data. Keys match the frontend contract exactly.

    Raises SystemExit (same message as before) when there is no project.toml."""
    project = Path(project)
    if not (project / CONFIG).is_file():
        raise SystemExit(
            f"no {CONFIG} in {project} — run `waku new-project <name>`, or point "
            "at a project harness.")

    cfg, entries = load_map(project)

    # Each map entry carries its drift status and whether its file exists.
    map_entries = [{**e, "drifted": _changed_since_verified(e, project),
                    "exists": _exists(e, project)} for e in entries]

    open_entries = [e for e in map_entries if e.get("status") == "open"]
    drifted_entries = [e for e in map_entries if e.get("drifted")]
    waiting_entries = [e for e in map_entries
                       if e.get("authority") == "proposal"
                       and e.get("status") in ("open", "deferred")]

    # Three most consequential, same ranking as the text renderer.
    next_items: list[dict] = []
    seen: set[str] = set()
    for action, group in (("resolve", open_entries),
                          ("re-verify", drifted_entries),
                          ("authorize", waiting_entries)):
        for e in group:
            if e.get("id") in seen:
                continue
            seen.add(e.get("id"))
            next_items.append({"action": action, "id": e.get("id", ""),
                               "note": e.get("note", "")})
            if len(next_items) == 3:
                break
        if len(next_items) == 3:
            break

    roles = []
    for r in cfg.get("roles", []):
        role = {**r}
        role["skills"] = [
            {"id": sid, "exists": (project / "skills" / r.get("id", "") / sid / "SKILL.md").is_file()}
            for sid in r.get("skills", [])
        ]
        role["sandbox"] = authority_to_sandbox(r.get("authority", "recommend"))
        roles.append(role)

    auth = cfg.get("authority") or {}
    return {
        "title": cfg.get("title") or cfg.get("name") or "project",
        "thesis": cfg.get("thesis") or "",
        "name": cfg.get("name") or "",
        "updated": cfg.get("updated") or "",
        "authority": {"default": auth.get("default", "recommend"),
                      "irreversible": auth.get("irreversible", [])},
        "map": map_entries,
        "open": [e.get("id") for e in open_entries],
        "waiting": [e.get("id") for e in waiting_entries],
        "drifted": [e.get("id") for e in drifted_entries],
        "next": next_items,
        "roles": roles,
        "watches": cfg.get("watches", []),
    }


def bring_me_back(project: Path) -> str:
    """The CLI text, built from state() — byte-identical to what it always was."""
    s = state(project)
    entries = s["map"]
    by_id = {e["id"]: e for e in entries}
    title = s["title"]
    thesis = s["thesis"].strip()

    open_ = [by_id[i] for i in s["open"]]
    drifted = [by_id[i] for i in s["drifted"]]
    waiting = [by_id[i] for i in s["waiting"]]

    out = [f"# {title} — bring me back", ""]
    if thesis:
        out += [f"**Thesis:** {thesis}", ""]

    out.append("## Where you left it")
    by_status: dict[str, list[dict]] = {}
    for e in entries:
        by_status.setdefault(str(e.get("status", "?")), []).append(e)
    for status in ("established", "open", "deferred", "hypothesis", "stale"):
        if status in by_status:
            names = ", ".join(e.get("id", "?") for e in by_status[status])
            out.append(f"- {status}: {names}")
    out.append("")

    out.append("## What changed while you were away")
    if drifted:
        for e in drifted:
            out.append(f"- `{e.get('path')}` changed since verified ({e.get('verified')})")
    else:
        out.append("- nothing drifted since last verified")
    out.append("")

    out.append("## Unresolved decisions")
    seen: set[str] = set()
    resolved_any = False
    for e in open_ + waiting:
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        resolved_any = True
        note = f": {e.get('note')}" if e.get("note") else ""
        out.append(f"- `{e.get('id')}` ({e.get('status')}){note}")
    if not resolved_any:
        out.append("- none")
    out.append("")

    out.append("## Three most consequential next things")
    if s["next"]:
        for i, item in enumerate(s["next"], 1):
            note = f" — {item.get('note')}" if item.get("note") else ""
            out.append(f"{i}. {item['action']} `{item['id']}`{note}")
    else:
        out.append("- nothing outstanding — the project is quiet")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    project = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    print(bring_me_back(project))


if __name__ == "__main__":
    main()
