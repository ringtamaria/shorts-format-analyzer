"""Render the Markdown report (and the separate discovery file).

Rules: no abstract adjectives; describe with seconds, order and structure;
numbers as quartile ranges, never a lone point; rates before positions; at
most three example URLs per format; ASR examples carry a disclaimer; every
unclassified opening is listed in full (it is the input for new rules).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from .features import Features, get_rules
from .formats import N_EXAMPLES, FormatGroup, fmt_position, fmt_range, quartiles

ASR_NOTE = "実例は自動文字起こしのため表記に誤りがあります。"


@dataclass
class ReportMeta:
    genre: str
    report_date: date
    n_requested: int
    n_collected: int
    transcript_backend: str
    quota_used: int
    quota_budget: int
    n_other_lang: int = 0
    n_not_fetched: int = 0
    transcript_lang: str = "ja"
    collection_route: str = "search"
    partial: bool = False
    credits_exhausted: bool = False
    transcripts_aborted: str | None = None
    transcripts_capped: bool = False
    rules_source: str = ""
    reclassified_from_cache: bool = False
    notes: list[str] | None = None


def _pct(x: float) -> str:
    return f"{int(round(x * 100))}%"


def _shares(share: dict[str, float]) -> str:
    return "、".join(f"{k} {_pct(v)}" for k, v in share.items()) or "なし"


def _analysed(feats: list[Features]) -> list[Features]:
    return [f for f in feats if f.speech in ("speech", "silent", "no_transcript")]


def _overall(feats: list[Features], groups: list[FormatGroup]) -> list[str]:
    r = get_rules()
    a = _analysed(feats)
    n = len(a)
    if n == 0:
        return ["対象動画なし。", ""]
    spoken = [f for f in a if f.speech == "speech"]
    L = ["## 全体の傾向", ""]
    L.append(f"- 分析本数: {n} 本（発話あり {len(spoken)} 本、発話なし {n - len(spoken)} 本）")
    durs = [f.duration_sec for f in a]
    L.append(f"- 尺: {fmt_range(quartiles(durs, 0), '秒')}、最短 {min(durs)} 秒、最長 {max(durs)} 秒")
    buckets = Counter("〜15秒" if d <= 15 else "16〜30秒" if d <= 30 else "31〜60秒" if d <= 60 else "61秒〜" for d in durs)
    L.append("- 尺の分布: " + "、".join(f"{k} {_pct(buckets[k] / n)}" for k in ("〜15秒", "16〜30秒", "31〜60秒", "61秒〜") if buckets[k]))
    L.append("- 型の分布: " + "、".join(f"{g.name} {_pct(g.share)}" for g in groups))
    if spoken:
        m = len(spoken)
        L.append(f"- 発話密度: {fmt_range(quartiles([f.speech_density for f in spoken], 1), '文字/秒', nd=1)}")
        for attr, label in (("completion_rel", "完成の提示"), ("question_rel", "問いかけ"), ("cta_rel", "CTA")):
            L.append(f"- {label}: {_pct(sum(getattr(f, attr) is not None for f in spoken) / m)}の動画にあり")
        L.append(f"- 大量投入: {_pct(sum(f.bulk_input for f in spoken) / m)}の動画にあり")
        if r.modifiers:
            L.append("- 冒頭の装飾（1 本に複数付く）: " + "、".join(
                f"{mod.name} {_pct(sum(mod.id in f.modifiers for f in spoken) / m)}" for mod in r.modifiers))
        L.append(f"- 冒頭に音楽・効果音: {_pct(sum(f.music_intro for f in spoken) / m)}の動画にあり")
    L.append(f"- タイトルにブランド名: {_pct(sum(f.brand is not None for f in a) / n)}")
    tt = Counter(f.title_type for f in a)
    L.append("- タイトルの型: " + "、".join(f"{k} {_pct(v / n)}" for k, v in tt.most_common()))
    L.append("")
    return L


def _examples(g: FormatGroup, *, thumbnails: bool = False) -> list[str]:
    L = [f"**該当動画（再生数上位 {N_EXAMPLES} 本）**", ""]
    for ex in g.examples[:N_EXAMPLES]:
        line = f"- [{ex['title']}]({ex['url']}) — {ex['duration_sec']} 秒、{ex['view_count']:,} 回"
        if thumbnails:
            line += f"（[サムネイル]({ex['thumbnail_url']})）"
        L.append(line)
    rest = g.size - len(g.examples[:N_EXAMPLES])
    if rest > 0:
        L.append(f"- 他 {rest} 本")
    L.append("")
    return L


def _type_section(g: FormatGroup, idx: int) -> list[str]:
    p = g.profile
    small = g.small_sample
    r = get_rules()
    L = [f"## フォーマット {idx}: {g.name}", ""]
    L.append(f"該当 {g.size} 本（分析対象の {_pct(g.share)}）。{g.one_line}")
    if small:
        L += ["", f"> **注意**: n={g.size} のため傾向の参考値。範囲は出さず中央値のみ示す。"]
    L += ["", "**構成レシピ**", ""]
    L += [f"{i}. {step}" for i, step in enumerate(g.recipe, 1)]
    L += ["", "**数値プロファイル**" + ("（Q1〜Q3 の範囲、括弧内は中央値）" if not small else "（中央値のみ、参考値）"), ""]
    L += ["| 項目 | 値 |", "|---|---|"]
    L.append(f"| 尺 | {fmt_range(p['duration'], '秒', small=small)} |")
    L.append(f"| 発話密度 | {fmt_range(p['speech_density'], '文字/秒', small=small, nd=1)} |")
    for key, label in (("completion", "完成の提示"), ("question", "問いかけ"), ("cta", "CTA")):
        L.append(f"| {label} | {fmt_position(p[key], small=small)} |")
    L.append(f"| 大量投入 | {_pct(p['bulk_input_share'])}の動画にあり |")
    if p.get("modifiers"):
        L.append("| 冒頭の装飾（複数付く） | " + "、".join(f"{k} {_pct(v)}" for k, v in p["modifiers"].items()) + " |")
    L.append(f"| 冒頭に音楽・効果音 | {_pct(p.get('music_intro_share', 0))}の動画にあり |")
    brands = "、".join(f"{b} {c}本" for b, c in p["brands"].items())
    L.append(f"| タイトルにブランド名 | {_pct(p['brand_share'])}" + (f"（{brands}）" if brands else "") + " |")
    L.append(f"| タイトルの型 | {_shares(p['title_type'])} |")
    L.append("")
    if p.get("opening_examples"):
        L += [f"**冒頭{int(r.opening_window_sec)}秒の実例**", ""]
        L += [f"- 「{t}」" for t in p["opening_examples"][:3]]
        L += ["", f"※{ASR_NOTE}", ""]
    L += _examples(g)
    return L


def _silent_evidence(g: FormatGroup) -> list[str]:
    kinds = g.profile.get("speech_kinds", {})
    L = ["**この型に入れた根拠**", ""]
    L.append(f"- 字幕が音楽・効果音の表記だけ（発話なしと確認）: {kinds.get('silent', 0)} 本")
    L.append(f"- 字幕がない（発話の有無は未確認）: {kinds.get('no_transcript', 0)} 本")
    if kinds.get("no_transcript"):
        L.append("  - 字幕がないのは、投稿者が字幕を無効にしている場合もある。**納品前にサムネイルと動画で目視確認すること。**")
    return L


def _silent_section(g: FormatGroup, idx: int) -> list[str]:
    p = g.profile
    small = g.small_sample
    L = [f"## フォーマット {idx}: {g.name}", ""]
    L.append(f"該当 {g.size} 本（分析対象の {_pct(g.share)}）。{g.one_line}")
    if small:
        L += ["", f"> **注意**: n={g.size} のため傾向の参考値。範囲は出さず中央値のみ示す。"]
    L += [""] + _silent_evidence(g)
    L += ["", "**数値プロファイル**" + ("（Q1〜Q3 の範囲、括弧内は中央値）" if not small else "（中央値のみ、参考値）"), ""]
    L += ["| 項目 | 値 |", "|---|---|"]
    L.append(f"| 尺 | {fmt_range(p['duration'], '秒', small=small)} |")
    brands = "、".join(f"{b} {c}本" for b, c in p["brands"].items())
    L.append(f"| タイトルにブランド名 | {_pct(p['brand_share'])}" + (f"（{brands}）" if brands else "") + " |")
    L.append(f"| タイトルの型 | {_shares(p['title_type'])} |")
    L.append("")
    L += _examples(g, thumbnails=True)
    return L


def _minor_section(groups: list[FormatGroup], min_size: int) -> list[str]:
    """Types below min_type_size: count, one-liner and URLs only."""
    if not groups:
        return []
    L = ["## 少数の型", "", f"該当が {min_size} 本未満の型。傾向を言えるだけの本数がないため、本数・一言説明・該当動画だけを載せる。", ""]
    for g in groups:
        L.append(f"### {g.name}（{g.size} 本、分析対象の {_pct(g.share)}）")
        L.append("")
        L.append(g.one_line)
        L.append("")
        if g.kind == "silent":
            L += _silent_evidence(g) + [""]
        for ex in g.examples[:N_EXAMPLES]:
            line = f"- [{ex['title']}]({ex['url']}) — {ex['duration_sec']} 秒、{ex['view_count']:,} 回"
            if g.kind == "silent":
                line += f"（[サムネイル]({ex['thumbnail_url']})）"
            L.append(line)
        if g.size > len(g.examples[:N_EXAMPLES]):
            L.append(f"- 他 {g.size - len(g.examples[:N_EXAMPLES])} 本")
        L.append("")
    return L


def _unclassified_section(g: FormatGroup, feats: list[Features]) -> list[str]:
    members = [f for f in feats if f.video_id in set(g.members)]
    L = ["## 未分類", ""]
    L.append(f"{g.size} 本（分析対象の {_pct(g.share)}）は、どの型のルールにも当たらなかった。"
             "冒頭テキストを全件載せる。ルールを足す材料にする。")
    L += ["", f"※{ASR_NOTE}", ""]
    for f in sorted(members, key=lambda x: -x.view_count):
        L.append(f"- 「{f.opening_text}」 — [{f.title[:40]}]({f.url})")
    L.append("")
    return L


def render_report(meta: ReportMeta, feats: list[Features], groups: list[FormatGroup]) -> str:
    r = get_rules()
    a = _analysed(feats)
    n_spoken = sum(f.speech == "speech" for f in a)
    L: list[str] = [f"# YouTube Shorts 構成フォーマット分析: {meta.genre}", "", f"作成日: {meta.report_date.isoformat()}", ""]
    L += ["## この資料について", ""]
    source = (f"検索語「{meta.genre}」で再生数上位の Shorts" if meta.collection_route == "search"
              else f"「{meta.genre}」の主要チャンネルが投稿した Shorts のうち再生数上位")
    source += " "
    excl = []
    if meta.n_other_lang:
        excl.append(f"字幕が日本語以外だった {meta.n_other_lang} 本")
    if meta.n_not_fetched:
        excl.append(f"字幕を未取得の {meta.n_not_fetched} 本")
    L.append(f"{source}を {meta.n_requested} 本を目標に収集し、{meta.n_collected} 本を集めた。"
             + (f"{'と'.join(excl)}を除き、" if excl else "")
             + f"{len(a)} 本を分析した（発話あり {n_spoken} 本、発話なし {len(a) - n_spoken} 本）。")
    L.append(f"冒頭{int(r.opening_window_sec)}秒の入り方で、あらかじめ定義した型に 1 本ずつ分類した。"
             "型ごとに、尺・発話の速さ・完成の提示・問いかけ・CTA・大量投入・タイトルを集計した。")
    L.append("数値は四分位範囲（該当動画の中央 50% が収まる幅）で示す。位置の区分は "
             + "／".join(f"{k}（{int(lo)}〜{int(hi)}%）" for k, (lo, hi) in r.position_bands.items()) + "。")
    L.append("再生数の予測はしていない。今この条件で上位にある動画の構成を、そのまま記述したもの。")
    if meta.partial and not meta.credits_exhausted:
        L += ["", "> **注意**: API の1日あたりの上限に達したため、収集途中のデータで作成している。翌日再実行すると本数が増える。"]
    if meta.credits_exhausted:
        L += ["", "> **注意**: 字幕取得 API の今月のクレジット上限に達したため、字幕の取得を途中で止めた。翌月の再実行で本数が増える。"]
    if meta.transcripts_aborted or meta.transcripts_capped:
        why = "取得元から接続を制限された" if meta.transcripts_aborted else "1回あたりの取得本数の上限に達した"
        nf = f"{meta.n_not_fetched} 本は未取得。" if meta.n_not_fetched else ""
        L += ["", f"> **注意**: 字幕の取得を途中で止めた（{why}）。{nf}"
                  "この版はジャンルの傾向ではなく、取得できた分だけの集計。再実行すると本数が増える。"]
    if meta.n_collected and meta.n_not_fetched == meta.n_collected:
        L += ["", "> **注意**: 字幕を1本も取得していないため、冒頭の型への分類は行えなかった。"]
    elif a and n_spoken == 0:
        L += ["", "> **注意**: 発話のある字幕が1本も取得できなかったため、冒頭の型への分類は行えなかった。"]
    L.append("")
    L += _overall(feats, groups)
    if groups:
        L += ["## フォーマット一覧", "", "| # | フォーマット | 本数 | 割合 | 一言で |", "|---|---|---|---|---|"]
        num = 0
        for g in groups:
            if g.kind == "unclassified":
                label = "—"
            elif g.size < r.min_type_size:
                label = "少数"
            else:
                num += 1
                label = str(num)
            note = f"（n={g.size} 参考値）" if g.small_sample and g.kind != "unclassified" else ""
            L.append(f"| {label} | {g.name} | {g.size} | {_pct(g.share)} | {note}{g.one_line} |")
        L.append("")
        idx = 0
        minor = []
        for g in groups:
            if g.kind == "unclassified":
                continue
            if g.size < r.min_type_size:
                minor.append(g)
                continue
            idx += 1
            L += _type_section(g, idx) if g.kind == "type" else _silent_section(g, idx)
        L += _minor_section(minor, r.min_type_size)
        for g in groups:
            if g.kind == "unclassified":
                L += _unclassified_section(g, feats)
    L += ["## 取得条件と制約", ""]
    src = Counter(f.transcript_source for f in a if f.speech in ("speech", "silent") and f.transcript_source)
    if src:
        L.append("- 字幕取得手段（字幕が取れた動画）: " + " / ".join(f"`{k}` {v} 本" for k, v in src.most_common()))
    else:
        L.append(f"- 字幕取得手段: `{meta.transcript_backend}`")
    if meta.n_other_lang:
        L.append(f"- 言語フィルタ: 字幕の主な言語が `{meta.transcript_lang}` 以外の {meta.n_other_lang} 本を分析対象から除外")
    n_silent_confirmed = sum(f.speech == "silent" for f in a)
    if n_silent_confirmed:
        L.append(f"- 発話なし判定: 字幕が音楽・効果音の表記だけの {n_silent_confirmed} 本は、除外せず {r.silent_name}に入れた")
    if meta.rules_source:
        L.append(f"- 判定ルール: `{meta.rules_source}`")
    if meta.reclassified_from_cache:
        L.append("- この版は保存済みのデータから再集計した（API は使っていない）")
    if meta.reclassified_from_cache:
        L.append("- YouTube Data API 使用量: この再集計では使っていない（収集時の使用量は収集時のレポートを参照）")
    else:
        L.append(f"- YouTube Data API 使用量: {meta.quota_used} / {meta.quota_budget} ユニット（当日分）")
    for n in meta.notes or []:
        L.append(f"- {n}")
    L.append("- **掲載URLは納品前に目視確認すること。** 検索はセーフサーチ無効で行っており、意図しない内容が混じり得る。")
    L.append("- 動画本体はダウンロードしていない。サムネイルは URL を載せるだけで、取得も解析もしていない。TikTok / Instagram は対象外。")
    L.append("")
    return "\n".join(L)


def render_discovery(genre: str, report_date: date, clusters: list[dict[str, Any]] | None, n_unclassified: int) -> str:
    """Operator-only file: candidate new types among unclassified videos. Never delivered."""
    r = get_rules()
    L = [f"# 未分類の動画から型の候補を探す: {genre}", "", f"作成日: {report_date.isoformat()}", ""]
    L.append("この資料は納品物ではない。`config/rules.yaml` に型を足すための材料。")
    L.append(f"未分類 {n_unclassified} 本を、尺・発話密度・完成/問いかけ/CTA の有無と位置・大量投入でまとめた。")
    L += ["", f"※{ASR_NOTE}", ""]
    if not clusters:
        L.append(f"未分類が {r.discovery_min_unclassified} 本未満のため、まとめは行っていない。")
        return "\n".join(L) + "\n"
    for i, c in enumerate(clusters, 1):
        p = c["profile"]
        L += [f"## 候補 {i}（{c['size']} 本）", ""]
        L.append(f"- 尺: {fmt_range(p['duration'], '秒', small=p['small_sample'])}、"
                 f"発話密度: {fmt_range(p['speech_density'], '文字/秒', small=p['small_sample'], nd=1)}")
        L.append(f"- 完成の提示: {fmt_position(p['completion'], small=p['small_sample'])}")
        if c["common_phrases"]:
            L.append("- 冒頭に共通する語: " + "、".join(f"「{g}」×{n}" for g, n in c["common_phrases"]))
        L += ["", "冒頭テキスト（全件）", ""]
        L += [f"- 「{o['text']}」 — [{o['title'][:40]}]({o['url']})" for o in c["openings"]]
        L.append("")
    return "\n".join(L)
