"""Render the Markdown report.

Rules: no abstract adjectives; describe with seconds, order and structure;
numbers as quartile ranges, never a lone point; at most three example URLs
per format; say plainly when transcripts were missing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from .features import Features, get_rules
from .formats import N_EXAMPLES, FormatCluster, fmt_position, fmt_range, quartiles


@dataclass
class ReportMeta:
    genre: str
    report_date: date
    n_requested: int
    n_videos: int
    n_with_transcript: int
    transcript_backend: str
    quota_used: int
    quota_budget: int
    partial: bool = False
    llm_error: str | None = None
    naming_model: str | None = None
    notes: list[str] | None = None
    transcripts_aborted: str | None = None   # set when the backend was blocked mid-run
    transcripts_capped: bool = False         # --max-transcripts reached
    n_transcript_unavailable: int | None = None  # video has no transcript (final)
    n_transcript_not_fetched: int | None = None  # not fetched yet: blocked / capped / transient error
    n_lang_excluded: int = 0                 # transcript not in transcript_lang -> removed from analysis
    transcript_lang: str = "ja"
    collection_route: str = "search"         # search | channels
    n_collected: int | None = None           # Shorts collected before the language filter
    credits_exhausted: bool = False          # transcript API monthly credits reached


def _pct(x: float) -> str:
    return f"{int(round(x * 100))}%"


def _shares(share: dict[str, float]) -> str:
    return "、".join(f"{k} {_pct(v)}" for k, v in share.items()) or "なし"


def _overall(feats: list[Features]) -> list[str]:
    n = len(feats)
    if n == 0:
        return ["対象動画なし。"]
    r = get_rules()
    durs = [f.duration_sec for f in feats]
    lines = ["## 全体の傾向", ""]
    lines.append(f"- 対象本数: {n} 本（字幕あり {sum(f.has_transcript for f in feats)} 本）")
    lines.append(f"- 尺: {fmt_range(quartiles(durs, 0), '秒')}、最短 {min(durs)} 秒、最長 {max(durs)} 秒")
    buckets = Counter("〜15秒" if d <= 15 else "16〜30秒" if d <= 30 else "31〜60秒" if d <= 60 else "61秒〜" for d in durs)
    lines.append("- 尺の分布: " + "、".join(f"{k} {_pct(v / n)}" for k, v in sorted(buckets.items())))
    with_tr = [f for f in feats if f.has_transcript]
    if with_tr:
        m = len(with_tr)
        op = Counter(f.opening_type for f in with_tr)
        lines.append(f"- 冒頭{int(r.opening_window_sec)}秒の発話タイプ: "
                     + "、".join(f"{k} {_pct(op[k] / m)}" for k in r.opening_types if op[k]))
        lines.append(f"- 問いかけあり: {_pct(sum(f.question_rel is not None for f in with_tr) / m)}、"
                     f"CTAあり: {_pct(sum(f.cta_rel is not None for f in with_tr) / m)}")
        lines.append(f"- 発話密度: {fmt_range(quartiles([f.speech_density for f in with_tr], 1), '文字/秒', nd=1)}")
    tt = Counter(f.title_type for f in feats)
    lines.append("- タイトルの型: " + "、".join(f"{k} {_pct(v / n)}" for k, v in tt.most_common()))
    lines.append("")
    return lines


def _cluster_section(c: FormatCluster) -> list[str]:
    p = c.profile
    small = c.small_sample
    r = get_rules()
    lines = [f"## フォーマット {c.cluster_id + 1}: {c.name}", ""]
    lines.append(f"該当 {c.size} 本（字幕あり動画の {_pct(c.share)}）。{c.one_line}")
    if small:
        lines.append("")
        lines.append(f"> **注意**: n={c.size} のため傾向の参考値。範囲は出さず中央値のみ示す。")
    lines.append("")
    lines.append("**構成レシピ**")
    lines.append("")
    lines += [f"{i}. {step}" for i, step in enumerate(c.recipe, 1)]
    lines.append("")
    lines.append("**数値プロファイル**" + ("（Q1〜Q3 の範囲、括弧内は中央値）" if not small else "（中央値のみ、参考値）"))
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    lines.append(f"| 尺 | {fmt_range(p['duration'], '秒', small=small)} |")
    lines.append(f"| 冒頭{int(r.opening_window_sec)}秒の発話タイプ | {_shares(p['opening_type'])} |")
    lines.append(f"| 結論の位置 | {fmt_position(p['conclusion'], small=small)}（区分の内訳: {_shares(p['conclusion_pos'])}） |")
    for key, label in (("question", "問いかけ"), ("cta", "CTA")):
        pr = p[key]
        val = f"{_pct(pr['share'])}の動画にあり" + (f"、尺の{fmt_position(pr, small=small)}" if pr["pos"] else "")
        lines.append(f"| {label} | {val} |")
    lines.append(f"| 話題転換 | {fmt_range(p['topic_shifts'], '回', small=small)} |")
    lines.append(f"| 発話密度 | {fmt_range(p['speech_density'], '文字/秒', small=small, nd=1)} |")
    lines.append(f"| タイトルの型 | {_shares(p['title_type'])} |")
    lines.append("")
    if p.get("opening_examples"):
        lines.append(f"**冒頭{int(r.opening_window_sec)}秒の実例**")
        lines.append("")
        lines += [f"- 「{t}」" for t in p["opening_examples"][:3]]
        lines.append("")
    lines.append(f"**該当動画（再生数上位 {N_EXAMPLES} 本）**")
    lines.append("")
    shown = c.examples[:N_EXAMPLES]
    for ex in shown:
        lines.append(f"- [{ex['title']}]({ex['url']}) — {ex['duration_sec']} 秒、{ex['view_count']:,} 回")
    rest = c.size - len(shown)
    if rest > 0:
        lines.append(f"- 他 {rest} 本")
    lines.append("")
    return lines


def _no_transcript_section(without: list[Features], meta: "ReportMeta | None" = None) -> list[str]:
    if not without:
        return []
    n = len(without)
    lines = ["## 字幕が取得できなかった動画", ""]
    lines.append(f"{n} 本は字幕がないため、タイトルと尺のみで集計した。")
    if meta is not None and meta.n_transcript_unavailable is not None and meta.n_transcript_not_fetched is not None:
        lines.append(f"- 動画側に字幕がない: {meta.n_transcript_unavailable} 本")
        lines.append(f"- 取得を中断したため未取得: {meta.n_transcript_not_fetched} 本（再実行で取得できる可能性がある）")
    tt = Counter(f.title_type for f in without)
    lines.append("- タイトルの型: " + "、".join(f"{k} {_pct(v / n)}" for k, v in tt.most_common()))
    lines.append(f"- 尺: {fmt_range(quartiles([f.duration_sec for f in without], 0), '秒', small=n < get_rules().min_cluster_for_ranges)}")
    lines.append("")
    shown = sorted(without, key=lambda x: -x.view_count)[:N_EXAMPLES]
    for f in shown:
        lines.append(f"- [{f.title}]({f.url}) — {f.duration_sec} 秒、{f.view_count:,} 回")
    if n > len(shown):
        lines.append(f"- 他 {n - len(shown)} 本")
    lines.append("")
    return lines


def render_report(meta: ReportMeta, feats: list[Features], clusters: list[FormatCluster],
                  without: list[Features]) -> str:
    r = get_rules()
    L: list[str] = []
    L.append(f"# YouTube Shorts 構成フォーマット分析: {meta.genre}")
    L.append("")
    L.append(f"作成日: {meta.report_date.isoformat()}")
    L.append("")
    L.append("## この資料について")
    L.append("")
    collected = meta.n_collected if meta.n_collected is not None else meta.n_videos
    source = (f"検索語「{meta.genre}」で再生数上位の Shorts" if meta.collection_route == "search"
              else f"「{meta.genre}」の主要チャンネルが投稿した Shorts のうち再生数上位")
    L.append(f"{source}を {meta.n_requested} 本を目標に収集し、{collected} 本を集めた。"
             + (f"字幕が日本語以外だった {meta.n_lang_excluded} 本を除き、" if meta.n_lang_excluded else "")
             + f"{meta.n_videos} 本を分析した。うち字幕（発話テキスト）が取れたのは {meta.n_with_transcript} 本。")
    L.append(f"字幕とメタデータから、冒頭{int(r.opening_window_sec)}秒の入り方・結論の位置・問いかけ・CTA・話題転換・尺を取り出し、"
             "似た構成の動画をまとめて「フォーマット」として名前を付けた。")
    L.append("数値は四分位範囲（該当動画の中央 50% が収まる幅）で示す。位置の区分は 前半（0〜33%）／中盤（34〜66%）／後半（67〜100%）。")
    L.append("再生数の予測はしていない。今この検索語で上位にある動画の構成を、そのまま記述したもの。")
    if meta.partial and not meta.credits_exhausted:
        L.append("")
        L.append("> **注意**: API の1日あたりの上限に達したため、収集途中のデータで作成している。翌日再実行すると本数が増える。")
    if meta.credits_exhausted:
        L.append("")
        L.append("> **注意**: 字幕取得 API の今月のクレジット上限に達したため、字幕の取得を途中で止めた。翌月の再実行で本数が増える。")
    if meta.transcripts_aborted or meta.transcripts_capped:
        L.append("")
        why = "取得元から接続を制限された" if meta.transcripts_aborted else "1回あたりの取得本数の上限に達した"
        nf = f"{meta.n_transcript_not_fetched} 本は未取得。" if meta.n_transcript_not_fetched else ""
        L.append(f"> **注意**: 字幕の取得を途中で止めた（{why}）。{nf}"
                 "この版はジャンルの傾向ではなく、取得できた分だけの集計。日を改めて再実行すると本数が増える。")
    if meta.n_with_transcript == 0:
        L.append("")
        L.append("> **注意**: 字幕が1本も取得できなかったため、フォーマット抽出は行えなかった。以下はタイトルと尺のみの集計。")
    L.append("")
    L += _overall(feats)
    if clusters:
        L.append("## フォーマット一覧")
        L.append("")
        L.append("| # | フォーマット | 本数 | 割合 | 一言で |")
        L.append("|---|---|---|---|---|")
        for c in clusters:
            note = f"（n={c.size} 参考値）" if c.small_sample else ""
            L.append(f"| {c.cluster_id + 1} | {c.name} | {c.size} | {_pct(c.share)} | {note}{c.one_line} |")
        L.append("")
        for c in clusters:
            L += _cluster_section(c)
    L += _no_transcript_section(without, meta)
    L.append("## 取得条件と制約")
    L.append("")
    L.append(f"- 字幕取得手段: `{meta.transcript_backend}`")
    if meta.n_lang_excluded:
        L.append(f"- 言語フィルタ: 字幕の主な言語が `{meta.transcript_lang}` 以外の {meta.n_lang_excluded} 本を分析対象から除外")
    if meta.naming_model:
        src = "LLM" if any(c.naming_source == "llm" for c in clusters) else "ルールベース"
        L.append(f"- フォーマット命名: {src}" + (f"（{meta.naming_model}）" if src == "LLM" else ""))
    if meta.llm_error:
        L.append(f"- LLM 命名は失敗したためルールベースの名前を使用: `{meta.llm_error}`")
    L.append(f"- YouTube Data API 使用量: {meta.quota_used} / {meta.quota_budget} ユニット（当日分）")
    for n in meta.notes or []:
        L.append(f"- {n}")
    L.append("- **掲載URLは納品前に目視確認すること。** 検索はセーフサーチ無効で行っており、意図しない内容が混じり得る。")
    L.append("- 動画本体はダウンロードしていない。TikTok / Instagram は対象外。")
    L.append("")
    return "\n".join(L)
