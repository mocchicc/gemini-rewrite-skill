# 公式資料と実装判断

確認日：2026-09-17。下記は調査時点の公式資料です。各ホストやAPIのアップデートで挙動が変わる可能性があります。

## 公式仕様として参照した内容

- [Gemini 3.8 Flash model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
  - モデルID `gemini-3.8-flash`、テキスト出力、low / medium / highの推論設定、出力トークン上限65536。
- [What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/latest-model)
  - `minimal` は非対応。samplingパラメータの廃止・非推奨化、`thinking_level`の利用。
- [GenerateContent REST API](https://ai.google.dev/api/generate-content)
  - generateContentエンドポイント、systemInstruction、generationConfig、thinkingConfig、STOP等の終了理由、thought付きparts、usageMetadata。
- [Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key)
  - APIキーによる認証、環境変数利用。なお同梱スクリプトはSDKを使わず、独自に`GEMINI_API_KEY`だけを環境変数から参照する。
- [Codex skills](https://developers.openai.com/codex/skills)
  - 調査時はOpenAIのChatGPT Learn「Build skills」へリダイレクト。
  - SKILL.md、`~/.agents/skills`、シンボリックリンク、`$skill`での明示呼び出し、agents/openai.yamlの`allow_implicit_invocation`。
- [Claude Code skills](https://code.claude.com/docs/en/skills)
  - `~/.claude/skills`、シンボリックリンク、`/skill`呼び出し、`disable-model-invocation`。ローカルSkill配置とCowork / cloud sessionsの違い。
- [Gemini API Additional Terms of Service](https://ai.google.dev/gemini-api/terms)
  - 無料サービスに機密情報・個人情報を送信しないこと。
  - 有効な課金アカウントに関連付けられたCloud Project経由のGemini APIをPaid Servicesとして扱うこと。
  - Paid Servicesではプロンプトや応答を製品改善に使わない一方、安全性・不正利用対策等のログ処理があること。
- [Gemini API billing](https://ai.google.dev/gemini-api/docs/billing)
  - APIのプロジェクト単位の課金・利用枠の説明。

## 今回独自に選んだ設計

MCPサーバーや別の常駐エージェントを増やさず、Python標準ライブラリでREST APIを1回呼ぶ。原稿を自動探索しない。明示呼び出しだけ。元原稿を上書きしない。リライト後の本文・差分・レポートを残す。Geminiの推敲後、呼び出し元は原意と事実を確認し、自分の文体で全面リライトしない。

既定の300,000 bytes制限、出力32768 tokens、通信待機180秒、再試行0回、文字数70% / 140%の警告閾値は、このSkillの実装上の初期値であり、Googleまたは各ホストの推奨値だと主張するものではない。
