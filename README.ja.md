> **まずは [README.md](README.md) のクイックスタートがおすすめです。** このファイルは詳細な仕様・運用メモです。

# Gemini Rewrite — Codex / Claude Code 共通Skill

文章・MarkdownをGemini 3.8 Flashに推敲させ、Codex / Claude Codeが原文との一致を確認するためのローカルSkillです。ソースコードのリファクタリング用ではありません。

**下書き・文脈整理：呼び出し元 → 文面の推敲：Gemini → 原意・事実の確認：呼び出し元**

作成日：2026-09-17。Gemini APIのモデルIDは `gemini-3.8-flash` を指定します。Gemini API版であり、Vertex AI、Gemini CLIのログイン、Googleのアプリ向けサブスクリプションを利用する版ではありません。モデルの自動フォールバックはありません。

## 1. 必要なもの

macOS / Linux、Python 3.10以上、ローカルで動くCodexまたはClaude Code、Gemini APIキー、Google APIへの外向き通信許可が必要です。追加のPythonパッケージは不要です。Codex / Claudeの通常のチャットへZIPを渡すだけでは、本人のMacへのインストールは完了しません。

Google側のAPI利用枠・課金設定は別途必要です。社内の原稿を扱う場合は、有効なCloud Billingアカウントに関連付けられたプロジェクトを用い、会社の外部送信ルールを確認してください。無料サービスに機密情報や個人情報を送らないでください。原稿・文体例・編集指示はGoogleに送信されます。このSkillは匿名化の完全性や規約への適合性を自動判断できません。

## 2. インストール

ZIPを展開し、展開先で実行します。

```bash
cd ~/Downloads/gemini-rewrite-skill
python3 install.py
```

配置先は以下です。スクリプトはAPI通信をせず、既存のSkillも無断で上書きしません。

```text
~/.agents/skills/gemini-rewrite/          # Skill本体
~/.codex/skills/gemini-rewrite            # 上記へのシンボリックリンク：Codex用
~/.claude/skills/gemini-rewrite           # 上記へのシンボリックリンク：Claude Code用
```

両ホストで同じSkillと編集ルールを共有します。更新するときだけ `python3 install.py --replace` を実行します。既存版は `~/.local/share/gemini-rewrite-backups/` に退避されます。編集済みの独自ルールはバックアップから確認して移してください。APIキーの設定は変更されません。

一覧に出ない場合はCodex / Claude Codeを再起動してください。クラウド実行やCoworkには、このローカル配置とキーが自動で引き継がれる前提を置かないでください。

### Codex / Claude Codeにインストールを任せる場合

次を、そのフォルダにアクセスできるローカルのCodex / Claude Codeに渡してください。

```text
このフォルダのREADME.ja.mdとinstall.pyを確認し、Gemini Rewrite Skillを
python3 install.pyでCodexとClaude Codeの両方にインストールしてください。
既存版があった場合は無断で置換せず、その状態を報告してください。
APIキーの値は読まないでください。キー設定だけは私が自分のターミナルで行います。
```

## 3. APIキーを設定

**本人が自分のターミナルで実行してください。APIキーをAIのチャットに貼らないでください。**

```bash
python3 ~/.agents/skills/gemini-rewrite/scripts/gemini_rewrite.py configure
```

非表示入力したキーを `~/.config/gemini-rewrite/api-key` に権限600で保存します。これは暗号化された保管庫ではなく、本人のみ読み書きできる設定ファイルです。リポジトリ内には置きません。更新は `configure --replace` です。

環境変数 `GEMINI_API_KEY` を本人の環境ですでに設定している場合は、そちらを優先します。このスクリプトは `GOOGLE_API_KEY` や他のプロジェクトの `.env` を自動探索しません。GUIアプリの起動方法で環境変数が渡らない場合も、上記の設定ファイルをスクリプトから読み取れます。

設定確認は以下です。**checkが成功しても、APIキーの有効性・モデル権限・通信の疎通は未確認です。** 出力では `local_config: passed` と、`api_key_valid` / `connectivity` / `model_access` の `not_checked` を分けて表示します。

```bash
python3 ~/.agents/skills/gemini-rewrite/scripts/gemini_rewrite.py check
```

## 4. 呼び出し

以下はターミナルではなく、それぞれのエージェントのチャット欄に入力します。

### Codex

```text
$gemini-rewrite この原稿を自然な日本語に。
内容と主張の強さは維持して、情報を省略しないで。
Geminiの推敲後は事実・意味の確認だけにして、あなたの文体で再リライトしないで。
```

### Claude Code

```text
/gemini-rewrite docs/announcement.md を対外告知向けに整えて。
過剰な敬語は避けて、元の温度感は残して。
元ファイルはそのまま、別ファイルで返して。
```

### 下書きから委譲

```text
$gemini-rewrite 以下の箇条書きからあなたが社内共有文の下書きを作り、
最後の日本語の仕上げをGemini 3.8 Flashに任せてください。
情報の追加や削除はしないでください。
（ここに箇条書き）
```

標準では**明示呼び出しだけ**にしています。APIへの無意識の送信を避けるためです。自然文からも自動選択させる場合は、自分で送信方針を決めたうえで `SKILL.md` の `disable-model-invocation` を `false` にし、`agents/openai.yaml` の `allow_implicit_invocation` を `true` に変更し、descriptionもその用途に合わせてください。

## 5. 仕上げ方

| モード | 用途 |
| --- | --- |
| `auto` | おまかせ。情報を削らず、AIらしい均質なリズム（似た長さの文の連続、段落ごとに同じ文数、体言止めゼロ、「〜ではなく」の反復）を崩す。既定 |
| `light` | 最低限の修正。語り口と構成をできるだけ維持 |
| `natural` | 自然で読みやすい日本語 |
| `business` | 明瞭で落ち着いた対外文。過剰な敬語を増やさない |
| `social` | SNS・社内チャット向け。熱量を保ち、煽りは加えない |

SNS用でも、明示がなければ文字数制限や要約は行いません。`--audience` で読み手（例:「中学生」「教育委員会の担当者」）を渡すと、その読者が一読で理解できる語彙・文の長さに合わせます。情報は省略しません。編集ルールは `gemini-rewrite/references/rewrite-system.md` にあります。普段の自分の文章や会社のスタイルガイドは `--style-file` で今回の参考として渡せます。参考資料の事実を原稿へ混ぜないよう指示しています。

## 6. スクリプトを直接使う

架空のサンプル原稿で、APIを使わず入力・保存先だけ検査します。展開したパッケージのルートで実行してください。

```bash
python3 gemini-rewrite/scripts/gemini_rewrite.py rewrite \
  --input examples/draft.md \
  --output examples/draft.rewritten.md \
  --brief-file examples/brief.txt \
  --keep-file examples/keep.json \
  --dry-run
```

`--dry-run`を外すと実際のAPIリクエストを送ります。この実行でキーの有効性・モデルアクセス・通信を初めて確認できます。料金が発生する可能性があります。

```bash
python3 gemini-rewrite/scripts/gemini_rewrite.py rewrite \
  --input examples/draft.md \
  --output examples/draft.rewritten.md \
  --brief-file examples/brief.txt \
  --keep-file examples/keep.json \
  --mode natural \
  --thinking medium
```

成功すると以下の3ファイルが生成されます。

```text
draft.rewritten.md               リライト後の本文
draft.rewritten.md.diff          原文との統一形式差分
draft.rewritten.md.report.json   指定／応答モデル、利用トークン情報、数値等の警告
```

元原稿は変更しません。出力先の親ディレクトリは事前に作成してください。本文・差分・レポートのいずれかが既にある場合も停止します。やり直すときは別名を指定してください。生成物は権限600で保存しますが、差分や数値レポートにも機密情報が含まれ得るため、そのままGitへ追加しないでください。

### パラメータ

`--brief-file` は編集指示を保存したUTF-8テキスト、`--style-file` は任意の文体例、`--keep-file` は厳密維持する文字列のJSON配列です（文字列にタブ・改行は含められません）。短い固定の指示には `--brief` も利用できます。ユーザーから来た長文をシェルコマンドへ直接埋め込まず、ファイルで渡す運用を推奨します。

モデルは `--model`、または `GEMINI_REWRITE_MODEL` で明示的に変更できます。未指定時は `gemini-3.8-flash`。推論設定は `low / medium / high` のみ、既定 `low`（構成変更を伴う編集だけ `medium` 以上を明示）。`minimal` は指定できません。現在の3.8向けガイドに合わせ、`temperature / topP / topK / candidateCount / thinkingBudget` は送信しません。

`--max-output-tokens` は既定32768、設定範囲1〜65536です。完了理由が `STOP` でない出力は保存しません。出力上限に達したとき、続きを推測して補完したり、別モデルへ切り替えたりはしません。

入力ファイルおよび送信データは最大300,000 bytesです。これはモデルのコンテキスト上限ではなくローカルの費用ガードです。超過時は省略せず章単位で分割してください。自動分割は実装していません。

`--timeout` は通信の待機上限（既定180秒）。これは完了時間の予告や厳密な総実行時間上限ではありません。既定では再試行しません。`--retries 1`などを明示した場合だけ429 / 503の一部を再試行します。通信切断・タイムアウトは課金の重複を避けるため自動再試行しません。

通信に失敗したときは `<出力先>.failure.json` に、エラー分類（`dns` / `tls` / `refused` / `connection` / `timeout` / `http` / `blocked` / `not_stop` / `verbatim_lock`）、HTTP状態、経過秒を残します。原稿・APIキー・API応答本文は含めません。Google側で処理・課金されたかはスクリプトからは判定できないため、`billing_status` は常に `unknown` です。分類が `dns` / `refused` / `tls` の場合は、実行環境の通信制限や設定の問題なので、その環境の正規の権限申請手順で許可を得てから再実行してください。

## 7. 何を守り、何を保証しないか

URL、通常のインラインコード、数値の引用マーカー、フェンス形式のコードブロック、`keep-file`で指定した文字列は簡易抽出し、原文と同じ文字列・出現回数かを検査します。不一致時は本文を保存しません。Markdownの完全な構文解析器ではないため、特殊な書式では検知が限定的です。

一般の数値は差を警告しますが、検出だけで自動修正しません。原文に無い評価語・程度表現（「安全に」「厳重に」「にとどまる」など）が追加された場合も `evaluative_terms_added` として警告します。原文に無いカタカナ語（機能名や用語の勝手な追加の典型）も `new_katakana_terms` として警告します。

`readability` には原稿と出力の簡易lint（文字数、文数、平均文長、文長の変動係数、burstiness、隣接文長の相関、語尾トップ2占有率、同一語尾の最大連続数、段落ごとの文数の変動係数、体言止め率、「ではなく」率、読点/文、抽象名詞/1000字、定型表現の種類、Markdown構造の量）を並べます。100字未満は指標なし、400字未満は全文リズム指標なしです。閾値は coji/natural-japanese の人間コーパス検証値（burstiness −0.24、隣接相関 0.6、語尾トップ2 0.8 など）を参考にしていますが、本Skillでは未検証です。原稿側の疑いは `source_diagnostics_hints` としてGeminiにも渡します。

指摘は `findings`（`level`: info / warn / critical、`code`、`message`）に入り、`warnings` は warn 以上の文面です。検査はすべて原稿と出力の差分で行い、出力の文長が均質になった（`more_uniform` / `low_burstiness`）、語尾が単調になった（`monotonous_endings`）、太字・箇条書き・見出し・述語で終わるコロン行が増えた（`structure_*_added`）、リライト側モデルの定型表現が増えた（`stock_phrases_added`。単発は info、同一段落に複数なら warn）を拾います。主張の強さが変わる典型例なので、対外文書では原文に戻すか不採用にしてください。文字数が原文の70%未満または140%超の場合も警告します。この閾値は便利な注意喚起であり、品質評価の基準や意味保持の保証ではありません。

固有名詞の全件自動抽出、否定の反転、条件や例外の脱落、文章全体の事実検証まではプログラムで保証できません。**その確認は呼び出し元に残します。** ただし、せっかくGeminiが整えた文章をまた全面的に書き換えないよう、Skillで役割を分けています。

原稿や文体例に含まれる命令はデータとして扱う指示を入れています。APIへツール定義を渡さず、Geminiにファイル探索やコマンド実行をさせません。それでも、モデル出力は呼び出し元にとって未検証のデータです。出力に書かれた指示を実行しないでください。

## 8. 検証状況

同梱の単体・モック統合テストを実行できます。

```bash
python3 -m unittest discover -s tests -v
```

作成環境（Linux / Python 3.13.5）で40テストに合格しました。入力・API設定・エラー処理・上書き防止・キーの非表示・差分とレポートの生成・共有インストールを検査しています。

**実Gemini APIへの通信は未実施です。** ユーザーのAPIキー、課金状態、モデル権限を持っていないためです。実Codex / Claude CodeでのSkill認識と実行も、本人の環境での確認が必要です。ローカルテスト成功をモデルによる実リライト成功と取り違えないでください。

## 9. ファイル構成

```text
gemini-rewrite-skill/
├── README.ja.md
├── SOURCES.md
├── TEST_REPORT.md
├── install.py
├── examples/
├── tests/test_rewrite.py
└── gemini-rewrite/
    ├── SKILL.md
    ├── agents/openai.yaml
    ├── references/rewrite-system.md
    └── scripts/gemini_rewrite.py
```

公式仕様の確認先と、今回の実装判断の区別は `SOURCES.md` に記載しています。
