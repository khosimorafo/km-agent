"""DETERMINISTIC EVAL — bring-me-back reconstructs project state from the map.

Hermetic: a tmp project.toml + files, no model, no network. What's pinned: the
thesis surfaces, open decisions rank first, and a file that changed since its
`verified` date is flagged as drift.
"""

from __future__ import annotations

import pytest

from waku.ops.bring_me_back import bring_me_back, state

TOML = '''\
name = "qamata"
title = "Qamata"
owner = "Proofs Africa"
thesis = "Compute for agents and their actions."
created = "2026-09-14"
updated = "2026-09-14"

[authority]
default = "recommend"
irreversible = []

[[map]]
id = "qamata.doctrine"
kind = "doctrine"
path = "PLAN.md"
authority = "source-of-truth"
status = "established"
owner = "product-owner"
verified = "2020-01-01"

[[map]]
id = "qamata.arch.isolation"
kind = "architecture"
path = "none"
authority = "proposal"
status = "open"
owner = "architect"
note = "VM-per-run vs gVisor"
'''


def _project(tmp_path):
    p = tmp_path / "qamata"
    p.mkdir()
    (p / "project.toml").write_text(TOML, encoding="utf-8")
    (p / "PLAN.md").write_text("# plan", encoding="utf-8")
    return p


def test_reconstructs_thesis_and_open_decision(tmp_path):
    out = bring_me_back(_project(tmp_path))
    assert "# Qamata — bring me back" in out
    assert "Compute for agents and their actions." in out
    # the open decision is the #1 most consequential thing, with its action
    assert "qamata.arch.isolation" in out
    assert "VM-per-run vs gVisor" in out
    assert "resolve `qamata.arch.isolation`" in out


def test_flags_files_drifted_since_verified(tmp_path):
    # PLAN.md verified 2020-01-01 but written "now" → drift is flagged
    out = bring_me_back(_project(tmp_path))
    assert "PLAN.md" in out and "changed since verified" in out


def test_section_anchor_path_resolves_to_the_file(tmp_path):
    from waku.ops.bring_me_back import _changed_since_verified

    p = _project(tmp_path)
    # a `file#section` path must check the FILE's mtime, not the anchor string
    assert _changed_since_verified({"path": "PLAN.md#some-section", "verified": "2999-01-01"}, p) is False
    assert _changed_since_verified({"path": "PLAN.md#some-section", "verified": "2020-01-01"}, p) is True


def test_missing_project_toml_is_honest(tmp_path):
    with pytest.raises(SystemExit) as exc:
        bring_me_back(tmp_path)
    assert "no project.toml" in str(exc.value)


def test_state_has_the_required_keys(tmp_path):
    s = state(_project(tmp_path))
    for key in ("title", "thesis", "name", "updated", "authority", "map",
                "open", "waiting", "drifted", "next", "roles", "watches"):
        assert key in s, f"missing key: {key}"
    assert s["title"] == "Qamata" and s["thesis"] == "Compute for agents and their actions."
    assert s["authority"] == {"default": "recommend", "irreversible": []}
    assert s["open"] == ["qamata.arch.isolation"]
    assert "qamata.doctrine" in s["drifted"]       # PLAN.md verified 2020-01-01
    assert s["waiting"] == ["qamata.arch.isolation"]  # an open proposal awaits the owner too
    assert s["next"][0]["action"] == "resolve"
    assert s["next"][0]["id"] == "qamata.arch.isolation"


def test_state_next_matches_the_text_renderer(tmp_path):
    p = _project(tmp_path)
    text = bring_me_back(p)
    for item in state(p)["next"]:
        assert f"{item['action']} `{item['id']}`" in text


def test_state_role_missing_skill_has_exists_false(tmp_path):
    p = _project(tmp_path)
    (p / "project.toml").write_text(TOML + '''
[[roles]]
id = "architect"
name = "Software Architect"
lens = "SOFTWARE ARCHITECT"
runtime = "claude"
model = "fable-5.1"
authority = "recommend"
skills = ["write-adr"]
''', encoding="utf-8")
    assert state(p)["roles"][0]["skills"] == [{"id": "write-adr", "exists": False}]


def test_state_sandbox_mapping_per_rung(tmp_path):
    p = _project(tmp_path)
    (p / "project.toml").write_text(TOML + '''
[[roles]]
id = "architect"
runtime = "claude"
authority = "recommend"
skills = []
[[roles]]
id = "engineer"
runtime = "codex"
authority = "sandbox"
skills = []
''', encoding="utf-8")
    by_id = {r["id"]: r for r in state(p)["roles"]}
    assert by_id["architect"]["sandbox"] == "read-only"
    assert by_id["engineer"]["sandbox"] == "workspace-write"


def test_state_malformed_verified_does_not_raise(tmp_path):
    p = _project(tmp_path)
    (p / "project.toml").write_text(
        TOML.replace('verified = "2020-01-01"', 'verified = "not-a-date"'), encoding="utf-8")
    assert "qamata.doctrine" in [e["id"] for e in state(p)["map"]]
