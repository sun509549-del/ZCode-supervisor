"""Canonical benchmark mode taxonomy."""

from __future__ import annotations

from typing import Any

CANONICAL_MODES = (
    "codex_only",
    "zcode_nested_codex",
    "zcode_direct_launcher",
    "zcode_direct_harness",
)

MODE_DETAILS = {
    "codex_only": "codex_only",
    "zcode_nested_codex": "nested_codex",
    "zcode_direct_launcher": "direct_launcher",
    "zcode_direct_harness": "direct_harness",
}

DISPLAY_MODES = {
    "codex_only": "codex_only",
    "zcode_nested_codex": "zcode_delegated",
    "zcode_direct_launcher": "zcode_delegated",
    "zcode_direct_harness": "zcode_delegated",
}

HUMAN_DISPLAY_MODES = {
    "codex_only": "Codex-only",
    "zcode_delegated": "ZCode delegated",
}

MODE_ALIASES = {
    "zcode_nested_codex": ("zcode_delegated",),
    "zcode_direct_launcher": ("zcode_delegated",),
    "zcode_direct_harness": ("zcode_delegated",),
}


def normalize_mode(canonical_mode: str, mode_detail: str | None = None) -> dict[str, Any]:
    if canonical_mode not in CANONICAL_MODES:
        raise ValueError(f"unknown canonical mode: {canonical_mode}")
    display = DISPLAY_MODES[canonical_mode]
    return {
        "canonical_mode": canonical_mode,
        "display_mode": display,
        "human_display_mode": HUMAN_DISPLAY_MODES[display],
        "mode_detail": mode_detail or MODE_DETAILS[canonical_mode],
        "mode_aliases": list(MODE_ALIASES.get(canonical_mode, ())),
    }
