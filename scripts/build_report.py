#!/usr/bin/env python3
"""End-to-end: one genre -> one Markdown report.

  python scripts/build_report.py --genre "レシピ 料理" --n 50
  -> out/report_<genre>_<YYYY-MM-DD>.md

Collection routes (--source, default auto)
  channels  config/channels.yaml has the genre: channels.list + playlistItems.list
            (1 unit per 50 videos) -> videos.list. No search.list.
  search    otherwise: search.list pages (100 units each) -> videos.list.

Transcripts via TRANSCRIPT_BACKEND (hosted = Supadata by default). Cached
transcripts are never re-fetched, videos already known to have no captions
are never queried, and non-Japanese transcripts are excluded from analysis.

Budgets: YouTube quota (daily) and Supadata credits (monthly) both stop the
run *normally*; a partial report is written and marked as such.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sfa.channels import Channel, load_channels  # noqa: E402
from sfa.cli import bootstrap, genre_slug, graceful_quota_stop  # noqa: E402
from sfa.features import extract_features  # noqa: E402
from sfa.formats import extract_formats  # noqa: E402
from sfa.quota import QuotaExhausted  # noqa: E402
from sfa.report import ReportMeta, render_report  # noqa: E402
from sfa.store import Store  # noqa: E402
from sfa.transcript import TranscriptBlocked, TranscriptService, make_backend  # noqa: E402
from sfa.youtube import Video, YouTubeClient  # noqa: E402

LOCAL_DEFAULT_MAX_TRANSCRIPTS = 15  # home-IP backend gets blocked when it fetches too much at once


# ---- collection --------------------------------------------------------------

def _fetch_metadata(client: YouTubeClient, store: Store, genre: str, ids: list[str]) -> None:
    missing = [i for i in dict.fromkeys(ids) if store.get_video(i) is None]
    if missing:
        store.put_videos(client.list_videos(missing), genre)
        print(f"  videos.list -> {len(missing)} ids")


def _in_window(published_at: str, after: str | None, before: str | None) -> bool:
    if not published_at:
        return True
    return (not after or published_at >= after) and (not before or published_at < before)


def collect_by_search(client: YouTubeClient, store: Store, genre: str, n: int, *, order: str,
                      published_after: str | None, published_before: str | None, refresh: bool,
                      reuse_search: bool, max_pages: int = 6) -> list[Video]:
    seen: dict[str, Video] = {}
    page_token: str | None = None
    for page in range(max_pages):
        key = store.search_key(genre, order, page_token, published_after, published_before)
        cached = None if refresh else store.get_search(key, ignore_ttl=reuse_search)
        if cached is None:
            ids, next_token = client.search_video_ids(genre, max_results=50, page_token=page_token, order=order,
                                                      published_after=published_after,
                                                      published_before=published_before)
            store.put_search(key, ids, next_token)
            print(f"  page {page + 1}: search.list -> {len(ids)} ids")
        else:
            ids, next_token = cached
            print(f"  page {page + 1}: search.list (cache) -> {len(ids)} ids")
        _fetch_metadata(client, store, genre, ids)
        for i in ids:
            v = store.get_video(i)
            if v is not None and v.is_short and i not in seen:
                seen[i] = v
        print(f"  shorts so far: {len(seen)}/{n}  {client.quota.status_line()}")
        if len(seen) >= n or not next_token:
            break
        page_token = next_token
    return sorted(seen.values(), key=lambda v: -v.view_count)[:n]


def collect_by_channels(client: YouTubeClient, store: Store, genre: str, channels: list[Channel], n: int, *,
                        published_after: str | None, published_before: str | None, refresh: bool,
                        max_pages_per_channel: int = 2) -> list[Video]:
    """uploads playlist of each channel, newest first, within the publish window."""
    ids_needed = [c.id for c in channels
                  if refresh or store.get_search(store.uploads_key(c.id), ignore_ttl=True) is None]
    if ids_needed:
        for cid, pid in client.uploads_playlists(ids_needed).items():
            store.put_search(store.uploads_key(cid), [pid], None)
        print(f"  channels.list -> {len(ids_needed)} channels")
    seen: dict[str, Video] = {}
    for ch in channels:
        cached_pid = store.get_search(store.uploads_key(ch.id), ignore_ttl=True)
        if not cached_pid or not cached_pid[0]:
            print(f"  [warn] no uploads playlist for {ch.id} {ch.name}")
            continue
        pid = cached_pid[0][0]
        page_token: str | None = None
        ch_ids: list[str] = []
        for _ in range(max_pages_per_channel):
            key = store.playlist_key(pid, page_token)
            cached = None if refresh else store.get_search(key)
            if cached is None:
                items, next_token = client.playlist_items(pid, page_token=page_token)
                store.put_search(key, [list(x) for x in items], next_token)
            else:
                items, next_token = [tuple(x) for x in cached[0]], cached[1]
            in_window = [vid for vid, pub in items if _in_window(pub, published_after, published_before)]
            ch_ids += in_window
            oldest = min((pub for _, pub in items if pub), default="")
            if not next_token or (published_after and oldest and oldest < published_after):
                break
            page_token = next_token
        _fetch_metadata(client, store, genre, ch_ids)
        shorts = [v for i in ch_ids if (v := store.get_video(i)) is not None and v.is_short]
        for v in shorts:
            seen.setdefault(v.video_id, v)
        print(f"  {ch.name or ch.id}: {len(ch_ids)} uploads in window, {len(shorts)} shorts  {client.quota.status_line()}")
    return sorted(seen.values(), key=lambda v: -v.view_count)[:n]


# ---- main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genre", required=True, help="genre name; search query for the search route")
    ap.add_argument("--n", type=int, default=50, help="target number of Shorts")
    ap.add_argument("--source", default="auto", choices=["auto", "search", "channels"])
    ap.add_argument("--order", default="viewCount", choices=["viewCount", "relevance", "date"])
    ap.add_argument("--published-after", default=None, help="RFC3339, e.g. 2026-09-01T00:00:00Z")
    ap.add_argument("--published-before", default=None, help="RFC3339, exclusive")
    ap.add_argument("--max-pages-per-channel", type=int, default=2, help="playlistItems pages (50 each) per channel")
    ap.add_argument("--refresh", action="store_true", help="ignore cached search / playlist pages")
    ap.add_argument("--reuse-search", action="store_true",
                    help="reuse cached search pages even if older than the TTL (same video set as last run)")
    ap.add_argument("--max-transcripts", type=int, default=None,
                    help=f"cap NEW transcript fetches this run (default: {LOCAL_DEFAULT_MAX_TRANSCRIPTS} for local, "
                         "no cap for hosted, which is limited by monthly credits instead)")
    ap.add_argument("--no-llm", action="store_true", help="rule-based names only")
    ap.add_argument("--k", type=int, default=None, help="fixed number of clusters")
    ap.add_argument("--out", default=None, help="output path (default out/report_<genre>_<date>.md)")
    args = ap.parse_args(argv)

    settings, quota, store, client = bootstrap(need_api_key=True)
    assert client is not None
    print(quota.status_line())

    # ---- collect
    channels = load_channels(settings.channels_path, args.genre) if args.source in ("auto", "channels") else []
    if args.source == "channels" and not channels:
        print(f"[error] --source channels but no channels for {args.genre!r} in {settings.channels_path}")
        return 1
    route = "channels" if channels else "search"
    print(f"\n== collect: {args.genre} (target {args.n}, route={route}) ==")
    hit_quota = False
    shorts: list[Video] = []
    try:
        if route == "channels":
            shorts = collect_by_channels(client, store, args.genre, channels, args.n,
                                         published_after=args.published_after,
                                         published_before=args.published_before, refresh=args.refresh,
                                         max_pages_per_channel=args.max_pages_per_channel)
        else:
            if not args.reuse_search and not quota.can_afford("search.list"):
                return graceful_quota_stop(QuotaExhausted("search.list", 100, quota))
            shorts = collect_by_search(client, store, args.genre, args.n, order=args.order,
                                       published_after=args.published_after,
                                       published_before=args.published_before,
                                       refresh=args.refresh, reuse_search=args.reuse_search)
    except QuotaExhausted as e:
        hit_quota = True
        graceful_quota_stop(e)
        # keep whatever made it into the cache for this genre
        shorts = sorted([v for v in store.videos_for_genre(args.genre) if v.is_short],
                        key=lambda v: -v.view_count)[:args.n]
    if not shorts:
        print("No Shorts collected; nothing to report.")
        return 0

    # ---- transcripts
    print(f"\n== transcripts: backend={settings.transcript_backend} lang={settings.transcript_lang} ==")
    try:
        backend = make_backend(settings)
    except ValueError as e:  # missing API key
        print(f"[error] {e}")
        return 1
    credits = getattr(backend, "credits", None)
    if credits is not None:
        print(credits.status_line())
    svc = TranscriptService(backend, store)
    max_new = args.max_transcripts
    if max_new is None and settings.transcript_backend == "local":
        max_new = LOCAL_DEFAULT_MAX_TRANSCRIPTS

    transcripts = {}
    attempts_now = 0
    aborted: str | None = None
    credits_exhausted = False
    capped = False
    skipped_known_none = 0
    lang_excluded: list[str] = []
    for i, v in enumerate(shorts, 1):
        row = store.get_transcript_row(v.video_id)
        is_final = row is not None and row["status"] in TranscriptService.FINAL_STATUSES
        if not is_final:
            # Known to have no caption track at all (captions.list returned nothing): don't spend a credit.
            # contentDetails.caption is NOT used: it only covers owner uploads and was false for every
            # video in the first run although auto-generated captions existed.
            tracks = store.get_caption_check(v.video_id)
            if tracks is not None and len(tracks) == 0:
                store.put_transcript(v.video_id, "precheck", "unavailable", error="captions.list returned no tracks")
                skipped_known_none += 1
                continue
            if max_new is not None and attempts_now >= max_new:
                capped = True
                continue
        try:
            tr = svc.get_transcript(v.video_id)
        except TranscriptBlocked as e:  # stop fetching for this run; nothing cached for blocked videos
            aborted = str(e)
            print(f"[warn] transcript backend stopped: {e}")
            break
        except QuotaExhausted as e:  # monthly credits reached
            credits_exhausted = True
            print(f"[stop] {e}")
            print(f"[stop] {e.tracker.resume_hint()}")
            break
        if not is_final and settings.transcript_backend != "null":
            attempts_now += 1
        if tr is not None:
            if tr.is_lang(settings.transcript_lang):
                transcripts[v.video_id] = tr
            else:
                lang_excluded.append(v.video_id)
        if i % 10 == 0 or i == len(shorts):
            print(f"  {i}/{len(shorts)}  ok={len(transcripts)} excluded_lang={len(lang_excluded)} stats={svc.stats}")
    if capped:
        print(f"  [info] stopped after {max_new} new transcript fetches (--max-transcripts). "
              "Re-run later to continue; fetched ones are cached.")
    if credits is not None:
        print(credits.status_line())

    # Videos whose transcript is in another language are removed from the analysis entirely.
    excluded = set(lang_excluded)
    analysed = [v for v in shorts if v.video_id not in excluded]

    n_unavailable = n_not_fetched = 0
    for v in analysed:
        if v.video_id in transcripts:
            continue
        row = store.get_transcript_row(v.video_id)
        if row is not None and row["status"] == "unavailable":
            n_unavailable += 1
        else:
            n_not_fetched += 1

    # ---- features / formats
    print("\n== features / formats ==")
    feats = [extract_features(v, transcripts.get(v.video_id)) for v in analysed]
    n_tr = sum(f.has_transcript for f in feats)
    print(f"  videos={len(feats)} with_transcript={n_tr} excluded_lang={len(lang_excluded)}")
    # Only call the LLM with a key from .env. Never fall back to machine-wide credentials
    # (on a work machine those may belong to the company account).
    use_llm = not args.no_llm and bool(settings.anthropic_api_key)
    if not args.no_llm and not settings.anthropic_api_key:
        print("  [info] ANTHROPIC_API_KEY is not set in .env: using rule-based format names")
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

    window = ", ".join(x for x in (f"publishedAfter={args.published_after}" if args.published_after else "",
                                    f"publishedBefore={args.published_before}" if args.published_before else "") if x)
    if route == "channels":
        cond = f"収集経路: チャンネル起点（{len(channels)} チャンネルの投稿一覧、再生数順に上位 {args.n} 本）"
    else:
        cond = f"収集経路: 検索（検索語「{args.genre}」、order={args.order}）"
    notes = [cond + (f"、{window}" if window else "")]
    if credits is not None:
        notes.append(f"字幕取得 API のクレジット: {credits.used} / {credits.budget}（今月分）")
    meta = ReportMeta(
        genre=args.genre, report_date=date.today(), n_requested=args.n, n_videos=len(feats),
        n_with_transcript=n_tr, transcript_backend=settings.transcript_backend,
        quota_used=quota.used, quota_budget=quota.budget, partial=hit_quota or credits_exhausted,
        credits_exhausted=credits_exhausted,
        llm_error=llm_error, transcripts_aborted=aborted, transcripts_capped=capped,
        n_transcript_unavailable=n_unavailable, n_transcript_not_fetched=n_not_fetched,
        n_lang_excluded=len(lang_excluded), transcript_lang=settings.transcript_lang,
        collection_route=route, n_collected=len(shorts),
        naming_model=settings.llm_model if use_llm else None, notes=notes,
    )
    md = render_report(meta, feats, clusters, without)
    out = Path(args.out) if args.out else settings.out_dir / f"report_{genre_slug(args.genre)}_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    (out.with_suffix(".features.json")).write_text(
        json.dumps({"features": [f.to_dict() for f in feats], "clusters": [c.to_dict() for c in clusters],
                    "lang_excluded": lang_excluded, "skipped_known_no_captions": skipped_known_none},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[done] wrote {out}")
    print(quota.status_line())
    if hit_quota:
        print(quota.resume_hint())
    return 0


if __name__ == "__main__":
    sys.exit(main())
