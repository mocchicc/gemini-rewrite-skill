# Gemini Rewrite Skill

**Codex / Claude Code から Gemini 3.8 Flash を呼び出して、文章の仕上げを任せるための共通 Skill です。**

たとえば、Codex や Claude Code で下書きを作ったあとに、

> 「内容は変えずに、Gemini 3.8 Flash で自然な日本語に整えて」

という使い方ができます。

```text
Codex / Claude Code
  ↓ 下書き・編集方針を作る
Gemini 3.8 Flash
  ↓ 文章をリライト
Codex / Claude Code
  ↓ 原文との意味・数値・条件の差を確認
完成
```

Gemini の出力を、そのあと Codex / Claude Code が勝手にもう一度全面リライトしないように役割を分けています。

> [!NOTE]
> この Skill は **文章・Markdown のリライト用**です。コードのリファクタリング用ではありません。

## できること

- Codex から `$gemini-rewrite` で呼び出す
- Claude Code から `/gemini-rewrite` で呼び出す
- Gemini 3.8 Flash に文章の推敲を委譲する
- 元原稿は上書きしない
- 原文との差分 (`.diff`) を残す
- 数値・URL・指定文字列などの意図しない変更をチェックする
- `light / natural / business / social` の4種類のリライトモードを使う
- 自分の過去文章やスタイルガイドを参考文として渡す

既定モデルは **`gemini-3.8-flash`** です。

---

# まず使ってみる

## 1. リポジトリを取得

```bash
git clone https://github.com/mocchicc/gemini-rewrite-skill.git
cd gemini-rewrite-skill
```

## 2. Skill をインストール

```bash
python3 install.py
```

これで1つの Skill を Codex / Claude Code の両方から使えるようにします。

```text
~/.agents/skills/gemini-rewrite/   ← Skill 本体
~/.claude/skills/gemini-rewrite    ← 上記へのシンボリックリンク
```

既存の同名 Skill がある場合は、勝手に上書きせず停止します。

更新したい場合だけ次を実行します。

```bash
python3 install.py --replace
```

## 3. Gemini API キーを設定

API キーは **Codex や Claude のチャットに貼らず、自分のターミナルで設定してください。**

```bash
python3 ~/.agents/skills/gemini-rewrite/scripts/gemini_rewrite.py configure
```

入力したキーは次の場所に保存されます。

```text
~/.config/gemini-rewrite/api-key
```

すでに `GEMINI_API_KEY` を環境変数として設定している場合は、そちらを優先します。

設定確認:

```bash
python3 ~/.agents/skills/gemini-rewrite/scripts/gemini_rewrite.py check
```

## 4. 呼び出す

### Codex

チャットで:

```text
$gemini-rewrite この文章を自然な日本語に整えて。
内容、数字、主張の強さは変えず、情報も省略しないで。
Geminiの推敲後は意味の確認だけして、再リライトしないで。
```

### Claude Code

チャットで:

```text
/gemini-rewrite docs/announcement.md を対外向けに整えて。
元の温度感は残して、過剰な敬語にはしないで。
元ファイルは変更せず別ファイルで返して。
```

これだけです。

---

# リライトモード

| モード | 向いている用途 |
| --- | --- |
| `light` | 誤字・助詞・読点など最低限だけ直したい |
| `natural` | 自然で読みやすい日本語にしたい。**既定値** |
| `business` | メール、告知、提案文など落ち着いた対外文 |
| `social` | SNS、Slack、社内チャットなど温度感を残した文章 |

たとえば「全体を変えすぎず、最低限だけ読みやすく」は `light` が向いています。

---

# 何が出力される？

たとえば `draft.md` をリライトすると、次のようなファイルを残せます。

```text
draft.md

draft.rewritten.md
├─ Gemini がリライトした本文

draft.rewritten.md.diff
├─ 原文との差分

draft.rewritten.md.report.json
└─ 使用モデル、トークン情報、数値変更などの警告
```

**元原稿は自動では上書きしません。**

Gemini が整えた文章を採用する前に、呼び出し元の Codex / Claude Code が以下を確認します。

- 固有名詞
- 数字・金額・日付
- 条件や例外
- 否定表現
- 責任主体
- 情報の抜け落ち

---

# スクリプトを直接使う

Skill を介さず、Python から直接実行することもできます。

まず API 通信なしで確認:

```bash
python3 gemini-rewrite/scripts/gemini_rewrite.py rewrite \
  --input examples/draft.md \
  --output examples/draft.rewritten.md \
  --brief-file examples/brief.txt \
  --keep-file examples/keep.json \
  --dry-run
```

実際に Gemini API を呼び出す場合:

```bash
python3 gemini-rewrite/scripts/gemini_rewrite.py rewrite \
  --input examples/draft.md \
  --output examples/draft.rewritten.md \
  --brief-file examples/brief.txt \
  --keep-file examples/keep.json \
  --mode natural \
  --thinking medium
```

## 自分の文体を参考にさせる

自分の過去文章やスタイルガイドを `--style-file` で渡せます。

```bash
python3 gemini-rewrite/scripts/gemini_rewrite.py rewrite \
  --input draft.md \
  --output draft.rewritten.md \
  --brief "内容を変えず自然な日本語に" \
  --style-file my-writing-style.md
```

参考文の**内容そのものを原稿へ混ぜず、文体だけ参考にする**ようプロンプト側で指示しています。

---

# 安全のための設計

この Skill は、意図せず社内文書などを外部 API に送らないよう、標準では **明示的に呼び出したときだけ**動く設定です。

また、次の方針にしています。

- API キーをリポジトリに保存しない
- `.env` を勝手に探索しない
- 元原稿を勝手に上書きしない
- 別モデルへ勝手にフォールバックしない
- URL、コード、指定文字列などが壊れた場合は出力を採用しない
- Gemini の出力中に書かれたコマンドや指示を自動実行しない

ただし、Gemini API に渡した原稿や参考文は Google 側へ送信されます。機密情報・個人情報・児童生徒データなどを扱う場合は、利用組織のルールと Google 側の契約・データ利用条件を確認してください。

---

# テスト

```bash
python3 -m unittest discover -s tests -v
```

作成時点では、入力処理、API 設定、エラー処理、上書き防止、差分生成、共有インストールなどを含むテストを用意しています。

実際の Gemini API への接続は、利用者自身の API キー・課金設定・モデル利用権限に依存します。

---

# ファイル構成

```text
.
├── README.md                       # まず読むもの
├── README.ja.md                    # 詳細な仕様・運用メモ
├── SOURCES.md                      # 公式仕様の確認先
├── TEST_REPORT.md                  # テスト内容
├── install.py                      # Codex / Claude Code 共通インストーラー
├── examples/
├── tests/
└── gemini-rewrite/
    ├── SKILL.md                    # Skill 本体の指示
    ├── agents/openai.yaml          # Codex 向け設定
    ├── references/rewrite-system.md
    └── scripts/gemini_rewrite.py   # Gemini API 呼び出し本体
```

より細かいオプション、入力上限、エラー時の挙動、保護チェックについては **[README.ja.md](README.ja.md)** を参照してください。

---

# この Skill の思想

LLMを1つに決め打ちするのではなく、役割を分けます。

```text
考える・作る      Codex / Claude Code
文章を磨く        Gemini 3.8 Flash
意味を検査する    Codex / Claude Code
```

**「Geminiにリライトを頼んだのに、最後にClaudeやCodexが全部書き直してしまう」問題を避ける**のが、この Skill のいちばん大事なところです。
