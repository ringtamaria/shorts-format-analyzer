#!/usr/bin/env python3
"""End-to-end: one genre -> one Markdown report.

  python scripts/build_report.py --genre "レシピ 料理" --n 100
  -> out/report_<genre>_<YYYY-MM-DD>.md

Steps
  1. search.list pages (100 units each) until --n Shorts are known (cache first)
  2. videos.list for unknown ids (1 unit / 50)
  3. transcripts via TRANSCRIPT_BACKEND (cached; never re-fetched)
  4. features -> clusters -> names -> report

Quota: stops *normally* when the daily budget is reached. If some videos were
already collected, a partial report is written and marked as such.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sfa.cli import bootstrap, genre_slug, graceful_quota_stop  # noqa: E402
from sfa.features import extract_features  # noqa: E402
from sfa.formats import extract_formats  # noqa: E402
from sfa.quota import QuotaExhausted  # noqa: E402
from sfa.report import ReportMeta, render_report  # noqa: E402
from sfa.store import Store  # noqa: E402
from sfa.transcript import TranscriptService, make_backend  # noqa: E402
from sfa.youtube import Video, YouTubeClient  # noqa: E402


def collect_shorts(client: YouTubeClient, store: Store, genre: str, n: int, *, order: str,
                   published_after: str | None, refresh: bool, max_pages: int = 6) -> tuple[list[Video], bool]:
    """Returns (shorts sorted by views desc, hit_quota)."""
    seen: dict[str, Video] = {}
    page_token: str | None = None
    hit_quota = False
    try:
        for page in range(max_pages):
            key = store.search_key(genre, order, page_token, published_after)
            cached = None if refresh else store.get_search(key)
            if cached is None:
                ids, next_token = client.search_video_ids(genre, max_results=50, page_token=page_token,
                                                          order=order, published_after=published_after)
                store.put_search(key, ids, next_token)
                print(f"  page {page + 1}: search.list -> {len(ids)} ids")
            else:
                ids, next_token = cached
                print(f"  page {page + 1}: search.list (cache) -> {len(ids)} ids")
            missing = [i for i in ids if i not in seen and store.get_video(i) is None]
            if missing:
                fetched = client.list_videos(missing)
                store.put_videos(fetched, genre)
                print(f"  page {page + 1}: videos.list -> {len(fetched)}")
            for i in ids:
                if i in seen:
                    continue
                v = store.get_video(i)
                if v is not None and v.is_short:
                    seen[i] = v
            print(f"  shorts so far: {len(seen)}/{n}  {client.quota.status_line()}")
            if len(seen) >= n or not next_token:
                break
            page_token = next_token
    except QuotaExhausted as e:
        hit_quota = True
        graceful_quota_stop(e)
    shorts = sorted(seen.values(), key=lambda v: -v.view_count)[:n]
    return shorts, hit_quota


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genre", required=True, help="search query for the genre")
    ap.add_argument("--n", type=int, default=100, help="target number of Shorts")
    ap.add_argument("--order", default="viewCount", choices=["viewCount", "relevance", "date"])
    ap.add_argument("--published-after", default=None, help="RFC3339, e.g. 2026-06-01T00:00:00Z")
    ap.add_argument("--refresh", action="store_true", help="ignore cached search pages")
    ap.add_argument("--max-transcripts", type=int, default=None, help="cap transcript fetches this run")
    ap.add_argument("--no-llm", action="store_true", help="rule-based names only")
    ap.add_argument("--k", type=int, default=None, help="fixed number of clusters")
    ap.add_argument("--out", default=None, help="output path (default out/report_<genre>_<date>.md)")
    args = ap.parse_args(argv)

    settings, quota, store, client = bootstrap(need_api_key=True)
    assert client is not None
    print(quota.status_line())
    if not quota.can_afford("search.list"):
        return graceful_quota_stop(QuotaExhausted("search.list", 100, quota))

    print(f"\n== collect: {args.genre} (target {args.n}) ==")
    shorts, hit_quota = collect_shorts(client, store, args.genre, args.n, order=args.order,
                                       published_after=args.published_after, refresh=args.refresh)
    if not shorts:
        print("No Shorts collected; nothing to report.")
        return 0

    print(f"\n== transcripts: backend={settings.transcript_backend} ==")
    try:
        svc = TranscriptService(make_backend(settings), store)
    except NotImplementedError as e:
        print(f"[error] {e}")
        return 1
    transcripts = {}
    fetched_now = 0
    for i, v in enumerate(shorts, 1):
        if args.max_transcripts is not None and fetched_now >= args.max_transcripts and store.get_transcript_row(v.video_id) is None:
            continue
        before = svc.stats["fetched"]
        try:
            tr = svc.get_transcript(v.video_id)
        except NotImplementedError as e:
            print(f"[error] {e}")
            return 1
        except RuntimeError as e:  # IpBlocked etc.: stop fetching, keep what we have
            print(f"[warn] transcript backend stopped: {e}")
            break
        fetched_now += svc.stats["fetched"] - before
        if tr is not None:
            transcripts[v.video_id] = tr
        if i % 10 == 0 or i == len(shorts):
            print(f"  {i}/{len(shorts)}  ok={len(transcripts)} stats={svc.stats}")

    print("\n== features / formats ==")
    feats = [extract_features(v, transcripts.get(v.video_id)) for v in shorts]
    n_tr = sum(f.has_transcript for f in feats)
    print(f"  videos={len(feats)} with_transcript={n_tr}")
    use_llm = not args.no_llm
    clusters, without, llm_error = extract_formats(
        feats, genre=args.genre, llm_model=settings.llm_model, anthropic_api_key=settings.anthropic_api_key or None,
        use_llm=use_llm, k=args.k,
    )
    if llm_error:
        print(f"  [warn] LLM naming failed, using rule-based names: {llm_error}")
    for c in clusters:
        print(f"  {c.cluster_id + 1}. {c.name} ({c.size}, {c.naming_source})")
    if n_tr == 0:
        print("  no transcripts for this genre: report will contain metadata-only findings")

    meta = ReportMeta(
        genre=args.genre, report_date=date.today(), n_requested=args.n, n_videos=len(feats),
        n_with_transcript=n_tr, transcript_backend=settings.transcript_backend,
        quota_used=quota.used, quota_budget=quota.budget, partial=hit_quota, llm_error=llm_error,
        naming_model=settings.llm_model if use_llm else None,
        notes=[f"検索条件: order={args.order}" + (f", publishedAfter={args.published_after}" if args.published_after else "")],
    )
    md = render_report(meta, feats, clusters, without)
    out = Path(args.out) if args.out else settings.out_dir / f"report_{genre_slug(args.genre)}_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    (out.with_suffix(".features.json")).write_text(
        json.dumps({"features": [f.to_dict() for f in feats], "clusters": [c.to_dict() for c in clusters]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[done] wrote {out}")
    print(quota.status_line())
    if hit_quota:
        print(quota.resume_hint())
    return 0


if __name__ == "__main__":
    sys.exit(main())
