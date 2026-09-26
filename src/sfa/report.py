"""Render the Markdown report.

Rules: no abstract adjectives; describe with seconds, order and structure;
three example URLs per format; say plainly when transcripts were missing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from .features import Features, OPENING_TYPES
from .formats import FormatCluster


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


def _pct(x: float) -> str:
    return f"{int(round(x * 100))}%"


def _dist_table(title: str, share: dict[str, float]) -> list[str]:
    lines = [f"| {title} | 割合 |", "|---|---|"]
    lines += [f"| {k} | {_pct(v)} |" for k, v in share.items()]
    return lines


def _overall(feats: list[Features]) -> list[str]:
    n = len(feats)
    if n == 0:
        return ["対象動画なし。"]
    durs = sorted(f.duration_sec for f in feats)
    lines = ["## 全体の傾向", ""]
    lines.append(f"- 対象本数: {n} 本（字幕あり {sum(f.has_transcript for f in feats)} 本）")
    lines.append(f"- 尺: 中央値 {durs[n // 2]} 秒、最短 {durs[0]} 秒、最長 {durs[-1]} 秒")
    buckets = Counter("〜15秒" if d <= 15 else "16〜30秒" if d <= 30 else "31〜60秒" if d <= 60 else "61秒〜" for d in durs)
    lines.append("- 尺の分布: " + "、".join(f"{k} {_pct(v / n)}" for k, v in sorted(buckets.items())))
    with_tr = [f for f in feats if f.has_transcript]
    if with_tr:
        m = len(with_tr)
        op = Counter(f.opening_type for f in with_tr)
        lines.append("- 冒頭3秒の発話タイプ: " + "、".join(f"{k} {_pct(op[k] / m)}" for k in OPENING_TYPES if op[k]))
        lines.append(f"- 問いかけあり: {_pct(sum(f.question_rel is not None for f in with_tr) / m)}、"
                     f"CTAあり: {_pct(sum(f.cta_rel is not None for f in with_tr) / m)}")
        dens = sorted(f.speech_density for f in with_tr)
        lines.append(f"- 発話密度: 中央値 {dens[m // 2]:.1f} 文字/秒")
    tt = Counter(f.title_type for f in feats)
    lines.append("- タイトルの型: " + "、".join(f"{k} {_pct(v / n)}" for k, v in tt.most_common()))
    lines.append("")
    return lines


def _cluster_section(c: FormatCluster) -> list[str]:
    p = c.profile
    lines = [f"## フォーマット {c.cluster_id + 1}: {c.name}", ""]
    lines.append(f"該当 {c.size} 本（字幕あり動画の {_pct(c.share)}）。{c.one_line}")
    lines.append("")
    lines.append("**構成レシピ**")
    lines.append("")
    lines += [f"{i}. {step}" for i, step in enumerate(c.recipe, 1)]
    lines.append("")
    lines.append("**数値プロファイル**")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    lines.append(f"| 尺（中央値） | {int(p['duration_median_sec'])} 秒 |")
    lines.append(f"| 冒頭3秒の発話タイプ | " + "、".join(f"{k} {_pct(v)}" for k, v in p["opening_type"].items()) + " |")
    lines.append(f"| 結論の位置 | " + "、".join(f"{k} {_pct(v)}" for k, v in p["conclusion_pos"].items()) + " |")
    q = f"{_pct(p['question_share'])}" + (f"（尺の {_pct(p['question_rel_median'])} 地点）" if p["question_rel_median"] is not None else "")
    cta = f"{_pct(p['cta_share'])}" + (f"（尺の {_pct(p['cta_rel_median'])} 地点）" if p["cta_rel_median"] is not None else "")
    lines.append(f"| 問いかけあり | {q} |")
    lines.append(f"| CTAあり | {cta} |")
    lines.append(f"| 話題転換（中央値） | {int(p['topic_shifts_median'])} 回 |")
    lines.append(f"| 発話密度（中央値） | {p['speech_density_median']} 文字/秒 |")
    lines.append(f"| タイトルの型 | " + "、".join(f"{k} {_pct(v)}" for k, v in p["title_type"].items()) + " |")
    lines.append("")
    if p.get("opening_examples"):
        lines.append("**冒頭3秒の実例**")
        lines.append("")
        lines += [f"- 「{t}」" for t in p["opening_examples"][:3]]
        lines.append("")
    lines.append("**該当動画（再生数順）**")
    lines.append("")
    for ex in c.examples:
        lines.append(f"- [{ex['title']}]({ex['url']}) — {ex['duration_sec']} 秒、{ex['view_count']:,} 回")
    lines.append("")
    return lines


def _no_transcript_section(without: list[Features]) -> list[str]:
    if not without:
        return []
    lines = ["## 字幕が取得できなかった動画", ""]
    lines.append(f"{len(without)} 本は字幕が取得できなかったため、タイトルと尺のみで集計した。")
    n = len(without)
    tt = Counter(f.title_type for f in without)
    lines.append("- タイトルの型: " + "、".join(f"{k} {_pct(v / n)}" for k, v in tt.most_common()))
    durs = sorted(f.duration_sec for f in without)
    lines.append(f"- 尺: 中央値 {durs[n // 2]} 秒")
    lines.append("")
    for f in sorted(without, key=lambda x: -x.view_count)[:5]:
        lines.append(f"- [{f.title}]({f.url}) — {f.duration_sec} 秒、{f.view_count:,} 回")
    lines.append("")
    return lines


def render_report(meta: ReportMeta, feats: list[Features], clusters: list[FormatCluster],
                  without: list[Features]) -> str:
    L: list[str] = []
    L.append(f"# YouTube Shorts 構成フォーマット分析: {meta.genre}")
    L.append("")
    L.append(f"作成日: {meta.report_date.isoformat()}")
    L.append("")
    L.append("## この資料について")
    L.append("")
    L.append(f"検索語「{meta.genre}」で再生数上位の Shorts を {meta.n_requested} 本を目標に収集し、"
             f"{meta.n_videos} 本を対象にした。うち字幕（発話テキスト）が取れたのは {meta.n_with_transcript} 本。")
    L.append("字幕とメタデータから、冒頭3秒の入り方・結論の位置・問いかけ・CTA・話題転換・尺を取り出し、"
             "似た構成の動画をまとめて「フォーマット」として名前を付けた。")
    L.append("再生数の予測はしていない。今この検索語で上位にある動画の構成を、そのまま記述したもの。")
    if meta.partial:
        L.append("")
        L.append("> **注意**: API の1日あたりの上限に達したため、収集途中のデータで作成している。翌日再実行すると本数が増える。")
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
            L.append(f"| {c.cluster_id + 1} | {c.name} | {c.size} | {_pct(c.share)} | {c.one_line} |")
        L.append("")
        for c in clusters:
            L += _cluster_section(c)
    L += _no_transcript_section(without)
    L.append("## 取得条件と制約")
    L.append("")
    L.append(f"- 字幕取得手段: `{meta.transcript_backend}`")
    if meta.naming_model:
        src = "LLM" if any(c.naming_source == "llm" for c in clusters) else "ルールベース"
        L.append(f"- フォーマット命名: {src}" + (f"（{meta.naming_model}）" if src == "LLM" else ""))
    if meta.llm_error:
        L.append(f"- LLM 命名は失敗したためルールベースの名前を使用: `{meta.llm_error}`")
    L.append(f"- YouTube Data API 使用量: {meta.quota_used} / {meta.quota_budget} ユニット（当日分）")
    for n in meta.notes or []:
        L.append(f"- {n}")
    L.append("- 動画本体はダウンロードしていない。TikTok / Instagram は対象外。")
    L.append("")
    return "\n".join(L)
