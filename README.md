# shorts-format-analyzer

YouTube Shorts の市場動画を収集し、伸びている動画に共通する **構成フォーマット** を抽出して、
企業の SNS 担当者向けのレポート（Markdown）として出力するツール。

目的は「今どういう構成が流行っているか」を **企画段階で** 提示すること。
再生数の予測はしない。現状の記述だけを出す。

## 使い方

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # YOUTUBE_API_KEY を記入

# Phase A: ジャンルごとに字幕保有率を調べて GO / CAUTION / NG を出す
python scripts/check_captions.py --genres "レシピ 料理" "コスメ" --sample 20

# Phase B: 1ジャンル分のレポートを end-to-end で生成
python scripts/build_report.py --genre "レシピ 料理" --n 100
# -> out/report_<genre>_<YYYY-MM-DD>.md
```

どちらのコマンドも実行前後に **クォータ残量** を表示する。
上限に近づくと例外ではなく **正常終了** し、残量とリセット時刻、再開方法を表示する。
取得済みのメタデータと字幕は `data/sfa.db` にキャッシュされ、再実行時は API を消費しない。

## やらないこと（理由つき）

| やらないこと | 理由 |
|---|---|
| 再生数・エンゲージメントの予測モデル | 予測は制作コストを払い終えた後に出てくる情報で、現場の意思決定を変えない |
| 色味・商品位置・効果音などの要素分解 | 制作者が動かせる単位ではないため現場で使われない。フォーマット・構成分析を出す |
| Web アプリ化 / SaaS 化 | クォータ 10,000 ユニット/日 が構造的な上限。単発レポートという商品形態がこの制約に合う |
| 動画本体のダウンロード | YouTube 利用規約で原則禁止。`yt-dlp` 等は使わない |
| TikTok / Instagram のスクレイピング | 公開 API が実質存在せず規約違反 |
| `captions.download` の実装 | 動画の所有者しか使えない（他人の動画は 403）。スコープを広げても回避不可 |

## 規約とクォータの制約

### YouTube Data API v3 のクォータ

デフォルト **10,000 ユニット/日**。太平洋時間の深夜（日本時間 16〜17 時ごろ）にリセット。
増枠はセルフサービス不可で、競争分析目的の申請は却下されやすい前提で設計している。

| メソッド | コスト | 使い方 |
|---|---|---|
| `search.list` | **100** / 回（最大 50 件） | 最も高い。新しい video_id が必要なときだけ呼ぶ |
| `videos.list` | 1 / 回（最大 50 件） | 既知の ID があるなら必ずこちら |
| `captions.list` | **50** / 本 | 高い。Phase A のサンプル調査のみ |

使用量は `data/quota.json` に永続記録し、`YOUTUBE_DAILY_QUOTA - YOUTUBE_QUOTA_SAFETY_MARGIN` を
超えそうな呼び出しは行わずに正常終了する。更新はファイルロック（`flock`）で排他しており、
2 つのターミナルで同時に走らせても使用量を取りこぼさない。

### 字幕の取得手段（`TRANSCRIPT_BACKEND`）

字幕テキストの取得は `sfa.transcript.get_transcript(video_id)` の裏に隠してあり、`.env` で切り替える。

- `local` … `youtube-transcript-api` を使う **検証用**。YouTube の規約上グレーなので
  **自宅など住宅用 IP から低頻度でのみ** 使う。リクエスト間隔（既定 4 秒）とリトライを入れてある。
  **データセンター IP（クラウド、VPN、社内プロキシ）からは `IpBlocked` になる。**
  **自宅の IP でも、頻度が高いとブロックされる。** 2026-10-01 の初回実行では、4 秒間隔で約 17 本取得した時点でブロックされた。
  既定は 30 秒間隔、1 回の実行で新規に取るのは 15 本まで（`--max-transcripts`）。取得済みはキャッシュされるので、
  1 ジャンルを数日に分けて取りきる運用にする。ブロックされたらその日は止め、翌日 1 本だけで解除を確かめてから再開する。
  プロキシ経由での回避はしない（ブロック回避にあたるため）。
- `hosted` … 商用向けの有料ホスト型 API。**未実装のスタブ**。インターフェースだけ通してある。
- `null` … 字幕を取らない。メタデータのみで特徴量を出すフォールバック。

字幕が 1 本も取れないジャンルでも、クラッシュせずその旨をレポートに書いて終了する。
取得を途中で止めた場合は、レポート冒頭に注意書きが入り、「動画側に字幕がない」と「未取得」の本数を分けて書く。
未取得分はキャッシュされないので、再実行で取得される。

LLM による命名は、`.env` に `ANTHROPIC_API_KEY` があるときだけ使う。端末に入っている他の Anthropic 認証情報
（会社のものなど）は使わない。キーがなければルールベースの名前になる。

## 秘密情報

- API キーは `.env` にだけ置く。`.gitignore` で除外済み。
- 取得したデータ（`data/`）と生成物（`out/`）もコミットしない。
- コミットの著者は GitHub の noreply アドレスを使う（`git config --local user.email`）。実メールを履歴に入れない。

## 判定ルール（`config/`）

冒頭タイプの正規表現、ブランド名リスト、CTA 動詞、結論マーカー、各種閾値はコードに書かず、YAML から読む。

| ファイル | 用途 |
|---|---|
| `config/rules.example.yaml` | コミットする汎用の最小セット。クローン直後はこれで動く |
| `config/rules.yaml` | 実運用のルール。**`.gitignore` 済み**でコミットされない |

ローダー（`src/sfa/rules.py`）は `rules.yaml` があればそれを、なければ `rules.example.yaml` を使う。
`rules.yaml` がなくてもテストは通る。ブランド名リストはジャンルに応じて `rules.yaml` に追記する。

## レポートの数値表記

- 数値は四分位範囲（Q1〜Q3）で出し、括弧内に中央値を添える。例: `20〜34秒（中央値27秒）`
- 位置は 前半（0〜33%）／中盤（34〜66%）／後半（67〜100%）の区分名に範囲を添える。例: `後半（65〜80%地点）`
- 5 本未満のクラスタは範囲を出さず「n=3 のため傾向の参考値」と明記する
- 該当動画の URL は再生数上位 3 本に固定し、残りは本数のみ書く
- 検索はセーフサーチ無効なので、**掲載 URL は納品前に目視確認する**（レポート末尾にも記載される）

## 構成

```
src/sfa/
  config.py       .env 読み込み・設定
  quota.py        クォータの永続記録と上限チェック
  youtube.py      Data API クライアント（quota 経由でしか呼べない）
  store.py        SQLite キャッシュ
  transcript/     get_transcript() の共通インターフェースと local / hosted / null 実装
  rules.py        config/ の判定ルールを読み込む
  features.py     構成の特徴量抽出（制作者が真似できる単位）
  formats.py      クラスタリングとフォーマット命名（LLM、なければルールベース）
  report.py       Markdown レポート生成
config/
  rules.example.yaml  判定ルールの汎用セット（rules.yaml は git-ignore）
scripts/
  check_captions.py   Phase A
  build_report.py     end-to-end
tests/
```

## テスト

```bash
.venv/bin/python -m pytest -q
```

ネットワークも API キーも使わず、YouTube API を偽装した end-to-end テストを含む。
