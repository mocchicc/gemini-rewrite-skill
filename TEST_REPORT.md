# 検証記録

実施日：2026-09-17  
環境：Linux / Python 3.13.5  
コマンド：`python3 -m unittest discover -s tests -v`  
結果：**40 tests / OK**

## 検査した項目

モデル既定値とリクエスト設定、原稿のJSONデータ化、保護指定の存在確認、コードフェンス・インラインコード・URL・引用番号の保護、数値変化と極端な文字数変化の警告、正常応答と推論部分の除外、途中終了・空応答・安全ブロックの拒否、APIキーをURLやエラー本文へ出さないこと、リダイレクトの拒否、既定の再試行なし、明示設定された503再試行、タイムアウトを再試行しないこと、原文・付随ファイルへの上書き拒否、書き込み途中の巻き戻し、出力権限600、空入力・バイナリ入力の拒否、dry-runの無通信・無保存、モックAPIを使った差分・レポート生成、設定ファイルの権限、Codex / Claude Code向けの共有インストール、既存版のバックアップ。

## 追記：2026-09-18 macOS 実機検証

環境：macOS (Darwin 25.5) / Python 3.14.6 / Codex CLI 0.144.1  
コマンド：`python3 -m unittest discover -s tests -v`  
結果：**41 tests / OK**（Codex用シンボリックリンクとURL保護の句読点除外のテストを追加）

- `python3 install.py` で `~/.agents/skills` 本体、`~/.codex/skills` と `~/.claude/skills` のシンボリックリンクが作成されることを確認。
- `configure` でAPIキーを保存後、`examples/draft.md` を実Gemini API（`gemini-3.8-flash`、`--mode natural --thinking medium`）でリライト。`finishReason=STOP`、`model_returned=gemini-3.8-flash`、警告0件、文字数比0.99。数値・URL・インラインコード・コードフェンス・keep指定はすべて保持され、変更は文末表現のみだった。
- 小文字の `thinkingLevel`（`"medium"`）でAPIが受理されることを確認。
- 参考：この短い原稿で `thoughtsTokenCount` 929 / 本文出力 110 トークン。短文では `--thinking low` で十分な可能性がある。

## 追記：2026-09-18 改良後の検証

結果：**52 tests / OK**（通信エラー分類 dns / tls / refused / timeout、HTTP状態の保持、失敗レポートに原稿・キーを含めないこと、評価語追加の警告、keep-file不正JSONの案内、モデル設定元の判定を追加）

- Claude Code から `/gemini-rewrite` の呼び出しを確認（Skill が読み込まれ、`check` → `rewrite` の手順どおりに動作）。
- 実原稿4件（プレスリリース草案、社内状況報告、政策メモ）で `business` / `natural` を実行。数値・URL・人名・タブ区切り表は全件保持。一方、4回中3回で原文に無い「安全に」「厳重に」「にとどまる」が追加されており、これを `evaluative_terms_added` 警告として検出できることを実APIで確認した。
- `--thinking low`（新既定）でも文体調整の品質は変わらず、thoughts トークンは0になった。

## 追記：2026-09-18 AI臭 lint 第1弾

結果：**62 tests / OK**

- リズム指標（burstiness・隣接文長相関・語尾トップ2・同一語尾連続）、最小文字数ガード、Markdown構造の差分、リライト側定型表現の差分（単発 info / 同一段落複数 warn）、findings のレベル分けを追加。
- 実API（auto、audience 指定、AI が書いたプレスリリース草案 1,966 字）: 同一語尾の最大連続 12 → 6、平均文長 58 → 55 と改善した一方、burstiness は −0.27 → −0.33 と均質寄りに動いた。auto モードのリズム改善効果は限定的で、`rhythm_not_improved` を info で出すようにした。評価語（厳重・手軽）とカタカナ語（ケース・サーバー・パターン）の追加は今回も検出。

## 未検証

lint 閾値の人間コーパスでの妥当性（coji/natural-japanese の値を借用）。長文原稿・`business` / `social` モードでのリライト品質。実Codex / Claude Codeからの `$gemini-rewrite` / `/gemini-rewrite` 呼び出し。Windowsは想定対象外。

API応答を置換したモックテストは、実際にGeminiが文章を書き換えたことを示すものではありません。利用前に架空のサンプル原稿で本人の環境における疎通を確認してください。
