#!/usr/bin/env python3
"""One-request check of the Supadata response before a batch run. Costs 1 credit.

Uses a video whose transcript is already cached (fetched by the local backend
from YouTube's auto-generated captions) and compares:
  * does mode=native return auto-generated captions at all?
  * do segment start times match the cached ones (ms -> s conversion)?
  * does the last segment end near the video duration?
  * is the TEXT identical to the cached YouTube ASR, character by character?
      identical -> Supadata passes YouTube's ASR through (misrecognitions stay)
      different -> Supadata processes it itself (quality may differ; input for the mode decision)
Does NOT write to the transcript cache.

  python scripts/probe_supadata.py            # first cached ja video
  python scripts/probe_supadata.py --video ID
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sfa.config import load_settings  # noqa: E402
from sfa.quota import CreditTracker, QuotaExhausted  # noqa: E402
from sfa.store import Store  # noqa: E402
from sfa.transcript import TranscriptBlocked, TranscriptUnavailable  # noqa: E402
from sfa.transcript.hosted import HostedBackend  # noqa: E402


def _norm(s: str) -> str:
    """Whitespace and line breaks differ between sources; compare the characters only."""
    return re.sub(r"\s+", "", s)


def compare_text(supa: str, cached: str) -> None:
    a, b = _norm(supa), _norm(cached)
    print("\n== text comparison with cached YouTube ASR (whitespace ignored) ==")
    if not b:
        print("  no cached text for this video; cannot compare")
        return
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    print(f"  chars: supadata={len(a)} cached={len(b)}  similarity={ratio:.3f}  identical={a == b}")
    if a == b:
        print("  [RESULT] IDENTICAL: native passes YouTube's ASR through. Misrecognitions are not improved.")
        return
    print("  [RESULT] DIFFERENT: first differences (supadata | cached):")
    shown = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        print(f"    {op:7} 「{a[i1:i2][:20]}」 | 「{b[j1:j2][:20]}」  (near: …{a[max(0, i1 - 8):i1]})")
        shown += 1
        if shown >= 8:
            break
    if ratio > 0.95:
        print("  mostly identical: likely the same ASR with small formatting differences")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default=None)
    args = ap.parse_args()
    s = load_settings()
    store = Store(s.db_path)
    vid = args.video or store.conn.execute(
        "SELECT video_id FROM transcripts WHERE status='ok' AND language LIKE 'ja%' ORDER BY fetched_at LIMIT 1"
    ).fetchone()[0]
    row = store.get_transcript_row(vid)
    cached = json.loads(row["segments"]) if row and row["segments"] else []
    video = store.get_video(vid, ignore_ttl=True)
    credits = CreditTracker(s.supadata_credits_path, s.supadata_monthly_credits)
    print(credits.status_line())
    try:
        tr = HostedBackend(s.supadata_api_key, credits, lang=s.transcript_lang).fetch(vid)
    except ValueError as e:
        print(f"[error] {e}")
        return 1
    except QuotaExhausted as e:
        print(f"[stop] {e}")
        return 0
    except TranscriptUnavailable as e:
        print(f"[RESULT] UNAVAILABLE for {vid}: {e}")
        if cached:
            print("[RESULT] YouTube auto captions exist for this video (cached), so mode=native likely excludes them."
                  " Do NOT run the batch; decide on mode first.")
        print(credits.status_line())
        return 2
    except TranscriptBlocked as e:
        print(f"[RESULT] STOPPED: {e}")
        print(credits.status_line())
        return 2
    print(f"video={vid} lang={tr.language} segments={len(tr.segments)} duration_sec={video.duration_sec if video else '?'}")
    print(f"last segment ends at {tr.segments[-1].end:.2f}s")
    print("\n  supadata start  | cached start | text (supadata)")
    for i in range(min(6, len(tr.segments))):
        c = f"{cached[i]['start']:.2f}" if i < len(cached) else "-"
        print(f"  {tr.segments[i].start:14.2f}  | {c:>12} | {tr.segments[i].text[:30]}")
    compare_text(tr.text, " ".join(r["text"] for r in cached))
    print()
    print(credits.status_line())
    print("Check the Supadata dashboard shows exactly 1 credit used.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
