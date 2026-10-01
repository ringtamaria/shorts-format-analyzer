"""Channel lists for the playlistItems collection route.

  config/channels.yaml          real list, git-ignored (written by the operator)
  config/channels.example.yaml  format only, committed

When channels.yaml is missing, or has no entry for the genre, callers fall
back to the search.list route.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_RE_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


@dataclass(frozen=True)
class Channel:
    id: str
    name: str = ""


def load_channels(path: Path | str, genre: str) -> list[Channel]:
    """Channels for ``genre``; [] when the file or the genre entry is missing."""
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries = (data.get("genres") or {}).get(genre) or []
    out: list[Channel] = []
    for e in entries:
        cid, name = (e, "") if isinstance(e, str) else (str(e.get("id", "")), str(e.get("name", "")))
        cid = cid.strip()
        if not _RE_CHANNEL_ID.match(cid):
            raise ValueError(f"{p}: invalid channel id {cid!r} under genre {genre!r} (expected UC + 22 chars)")
        out.append(Channel(cid, name))
    return out
