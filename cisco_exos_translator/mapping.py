# mapping file: user-editable JSON of translation decisions
# defaults are derived from the IR; user edits override them on the next run

from __future__ import annotations

import json
from pathlib import Path

# user-editable sections
_SECTIONS = ("vlans", "ports", "lags")


# write a mapping dict as pretty JSON
def write_mapping(path: Path, mapping: dict) -> None:
    path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")


# load a user-edited mapping file; ValueError on bad JSON
def load_mapping(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in mapping file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"mapping file {path} must contain a JSON object")
    return data


# overlay user edits onto freshly derived defaults
# defaults fill entries missing from the user file (e.g. interfaces added to the
# Cisco config later); unknown user keys are ignored. Returns (merged, notes)
def merge_mapping(defaults: dict, user: dict) -> tuple[dict, list[str]]:
    notes: list[str] = []
    merged = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in defaults.items()
    }

    # uplinks is a small settings object, not a per-item map: user values win
    # wholesale, no per-key notes
    user_uplinks = user.get("uplinks")
    if isinstance(user_uplinks, dict):
        merged.setdefault("uplinks", {}).update(user_uplinks)

    for section in _SECTIONS:
        user_section = user.get(section, {})
        if not isinstance(user_section, dict):
            notes.append(f"mapping: section '{section}' is not an object; ignored")
            continue
        merged_section = merged.setdefault(section, {})

        for key, value in user_section.items():
            if key not in merged_section:
                notes.append(
                    f"mapping: unknown {section} entry '{key}' ignored "
                    f"(not in the current Cisco config)"
                )
                continue
            # lag entries are objects; merge per-field so a partial edit works
            if section == "lags":
                entry = dict(merged_section[key])
                entry.update(value or {})
                merged_section[key] = entry
            else:
                merged_section[key] = value

        for key in merged_section:
            if key not in user_section:
                notes.append(
                    f"mapping: no entry for {section} '{key}' in the mapping "
                    f"file; using the derived default"
                )

    return merged, notes
