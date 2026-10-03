#!/usr/bin/env python3
"""Re-run the analysis from the cache after editing config/rules.yaml. No API calls.

  python scripts/reclassify.py --genre "レシピ 料理"
  python scripts/reclassify.py --features out/report_レシピ_料理_2026-10-02.features.json

Uses the same set of videos as the latest report for the genre (its
.features.json), reads metadata and transcripts from data/sfa.db only, and
writes a new report, features JSON and (when there are enough unclassified
videos) the discovery file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sfa.cli import genre_slug  # noqa: E402
from sfa.config import load_settings  # noqa: E402
from sfa.features import get_rules, set_rules  # noqa: E402
from sfa.rules import load_rules  # noqa: E402
from sfa.pipeline import analyse, meta_from_run, write_outputs  # noqa: E402
from sfa.quota import QuotaTracker  # noqa: E402
from sfa.store import Store  # noqa: E402


def latest_features(out_dir: Path, genre: str) -> Path | None:
    files = sorted(out_dir.glob(f"report_{genre_slug(genre)}_*.features.json"))
    return files[-1] if files else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genre", default=None)
    ap.add_argument("--features", default=None, help="features JSON of the run to reclassify")
    ap.add_argument("--rules", default=None,
                    help="rules file to use instead of config/rules.yaml (e.g. a draft kept elsewhere)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if not args.genre and not args.features:
        ap.error("give --genre or --features")

    settings = load_settings()
    store = Store(settings.db_path)
    src = Path(args.features) if args.features else latest_features(settings.out_dir, args.genre)
    if src is None or not src.exists():
        print(f"[error] no previous report for {args.genre!r} in {settings.out_dir}. Run build_report.py first.")
        return 1
    data = json.loads(src.read_text(encoding="utf-8"))
    run = dict(data.get("run") or {})
    # Files written before round 4 have no video_ids / run: rebuild them from what they have.
    ids = data.get("video_ids") or ([f["video_id"] for f in data.get("features", [])] + list(data.get("lang_excluded", [])))
    genre = args.genre or run.get("genre")
    if not genre:
        print("[error] the features file has no genre; pass --genre")
        return 1
    run.setdefault("genre", genre)
    run.setdefault("n_requested", len(ids))
    run.setdefault("transcript_backend", settings.transcript_backend)
    run.setdefault("transcript_lang", settings.transcript_lang)

    videos, missing = [], []
    for vid in ids:
        v = store.get_video(vid, ignore_ttl=True)
        (videos.append(v) if v is not None else missing.append(vid))
    if missing:
        print(f"[warn] {len(missing)} videos are not in the cache and are skipped: {missing[:5]}…")
    if args.rules:
        set_rules(load_rules(args.rules))
    rules = get_rules()
    # Runs recorded before round 4 have no collection route / credit lines. Say so instead of leaving a gap.
    if not run.get("notes"):
        run["notes"] = ["収集経路・字幕取得 API のクレジット: 再集計元の実行時に記録されていない"]
    print(f"[reclassify] {len(videos)} videos from {src.name}, rules={rules.source}")
    an = analyse(videos, store, lang=run.get("transcript_lang", "ja"), rules=rules)
    for g in an.groups:
        print(f"  {g.name}: {g.size}")
    quota = QuotaTracker(settings.quota_path, settings.quota_budget)  # read only, for the footer
    meta = meta_from_run(run, an, quota_used=quota.used, quota_budget=quota.budget, rules=rules,
                         report_date=date.today(), reclassified=True)
    out = Path(args.out) if args.out else settings.out_dir / f"report_{genre_slug(genre)}_{date.today().isoformat()}.md"
    paths = write_outputs(an, meta, out, video_ids=[v.video_id for v in videos], run_info=run)
    for k, p in paths.items():
        print(f"[done] {k}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
