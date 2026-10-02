#!/usr/bin/env python3
"""Phase A: how many Shorts in a genre have captions at all?

For each genre:
  1. search.list (100 units) -> up to 50 candidate video ids
  2. videos.list (1 unit)    -> metadata; keep only real Shorts (<= 180 s)
  3. Free estimate: contentDetails.caption flag (owner-uploaded captions only)
  4. Sampled estimate: captions.list (50 units EACH) on --asr-sample videos,
     which also reveals auto-generated (ASR) tracks

Verdict per genre, based on the sampled captions.list rate only. The owner
flag is shown for reference but never used for the verdict (--asr-sample 0
prints no verdict):
  GO       >= 60% of sampled videos have a track
  CAUTION  >= 30%
  NG       otherwise

Examples
  python scripts/check_captions.py --genres "レシピ 料理" "コスメ" --asr-sample 10
  python scripts/check_captions.py --genres "レシピ 料理" --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sfa.cli import bootstrap, graceful_quota_stop  # noqa: E402
from sfa.quota import COST, QuotaExhausted  # noqa: E402
from sfa.store import Store  # noqa: E402
from sfa.youtube import Video, YouTubeClient  # noqa: E402

GO, CAUTION = 0.60, 0.30


def verdict(rate: float) -> str:
    return "GO" if rate >= GO else "CAUTION" if rate >= CAUTION else "NG"


def fetch_shorts(client: YouTubeClient, store: Store, genre: str, *, order: str, published_after: str | None,
                 refresh: bool) -> list[Video]:
    key = store.search_key(genre, order, None, published_after)
    cached = None if refresh else store.get_search(key)
    if cached is None:
        ids, next_token = client.search_video_ids(genre, max_results=50, order=order, published_after=published_after)
        store.put_search(key, ids, next_token)
        print(f"  search.list -> {len(ids)} ids")
    else:
        ids = cached[0]
        print(f"  search.list (cache) -> {len(ids)} ids")

    videos = {v.video_id: v for vid in ids if (v := store.get_video(vid)) is not None}
    missing = [vid for vid in ids if vid not in videos]
    if missing:
        fetched = client.list_videos(missing)
        store.put_videos(fetched, genre)
        videos.update({v.video_id: v for v in fetched})
        print(f"  videos.list -> {len(fetched)} fetched, {len(ids) - len(missing)} cached")
    return [videos[vid] for vid in ids if vid in videos and videos[vid].is_short]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genres", nargs="+", required=True, help="search queries, one per genre")
    # Default 10, never 0 by default. contentDetails.caption only reflects captions the OWNER uploaded.
    # On 2026-10-01 it was false for all 50 Japanese recipe Shorts although every sampled video had
    # auto-generated captions. A verdict from that flag would mark every genre NG.
    ap.add_argument("--asr-sample", "--sample", dest="sample", type=int, default=10,
                    help="videos per genre to check with captions.list (50 units each). "
                         "0 is accepted but gives no verdict")
    ap.add_argument("--order", default="viewCount", choices=["viewCount", "relevance", "date"])
    ap.add_argument("--published-after", default=None, help="RFC3339, e.g. 2026-06-01T00:00:00Z")
    ap.add_argument("--refresh", action="store_true", help="ignore cached search results")
    ap.add_argument("--dry-run", action="store_true", help="show the quota cost and exit")
    args = ap.parse_args(argv)

    per_genre = COST["search.list"] + COST["videos.list"] + COST["captions.list"] * args.sample
    print(f"[plan] up to {per_genre} units per genre x {len(args.genres)} genres = {per_genre * len(args.genres)} units (less with cache)")

    if args.sample <= 0:
        print("[stop] --asr-sample 0: the owner-caption flag (contentDetails.caption) alone cannot judge a genre.")
        print("       Japanese Shorts almost never have owner captions; auto-generated ones only show up in captions.list.")
        print("       Re-run with --asr-sample 10 (default).")
        return 0

    settings, quota, store, client = bootstrap(need_api_key=not args.dry_run)
    print(quota.status_line())
    if args.dry_run:
        return 0
    assert client is not None

    results: list[tuple[str, str, float, float, int, int]] = []
    try:
        for genre in args.genres:
            print(f"\n== {genre} ==")
            shorts = fetch_shorts(client, store, genre, order=args.order,
                                  published_after=args.published_after, refresh=args.refresh)
            if not shorts:
                print("  no Shorts found")
                results.append((genre, "NG", 0.0, 0.0, 0, 0))
                continue
            flag_rate = sum(v.has_caption_flag for v in shorts) / len(shorts)
            print(f"  shorts: {len(shorts)}, owner-caption flag: {flag_rate:.0%}")

            sample = shorts[: args.sample]
            with_track = 0
            asr_only = 0
            for v in sample:
                tracks = store.get_caption_check(v.video_id)
                if tracks is None:
                    tracks = client.list_caption_tracks(v.video_id)
                    store.put_caption_check(v.video_id, tracks)
                if tracks:
                    with_track += 1
                    if all(t.get("trackKind") == "asr" for t in tracks):
                        asr_only += 1
            sampled_rate = with_track / len(sample) if sample else 0.0  # never judged from flag_rate
            v_ = verdict(sampled_rate)
            print(f"  sampled {len(sample)}: with track {with_track} (asr-only {asr_only}) -> {sampled_rate:.0%}  => {v_}")
            print(f"  {quota.status_line()}")
            results.append((genre, v_, sampled_rate, flag_rate, len(shorts), len(sample)))
    except QuotaExhausted as e:
        _print_table(results)
        return graceful_quota_stop(e)

    _print_table(results)
    print(quota.status_line())
    return 0


def _print_table(results: list[tuple[str, str, float, float, int, int]]) -> None:
    if not results:
        return
    print("\n| genre | verdict | sampled track rate | owner flag rate | shorts | sampled |")
    print("|---|---|---|---|---|---|")
    for g, v, sr, fr, n, s in results:
        print(f"| {g} | {v} | {sr:.0%} | {fr:.0%} | {n} | {s} |")


if __name__ == "__main__":
    sys.exit(main())
