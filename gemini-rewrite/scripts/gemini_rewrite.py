#!/usr/bin/env python3
"""Explicit, file-based Gemini rewriting. Python 3.10+; no third-party packages.

Only the selected source, brief, optional style reference, and editing rules are
sent to the fixed Google endpoint. No tools, repository discovery, or fallback
models are enabled. Run `... check` or `... rewrite --dry-run` without an API call.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import difflib
import getpass
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemini-3.8-flash"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
KEY_FILE = Path.home() / ".config" / "gemini-rewrite" / "api-key"
SKILL_ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 300_000  # Local cost guard, NOT the model's context limit.
MAX_RESPONSE_BYTES = 12_000_000
MODES = {
    "auto": "おまかせ。情報を削らず、人が書いたような自然な日本語にする。AIらしい均質さを崩す: 似た長さの文を続けない、段落ごとに同じ文数で刻まない、体言止めや短い一文を適度に混ぜる、「〜ではなく〜」の対比構文を繰り返さない、定型的なAI表現を使わない。読み手が一読で分かる語彙にし、専門用語や抽象語には言い換えや具体を添える。",
    "light": "誤字・不自然な助詞・読点・読みにくい箇所だけを最小限直す。語り口と構成を維持する。",
    "natural": "情報を削らず、自然で読みやすい日本語にする。過剰な丁寧語や定型的なAI表現を避ける。",
    "business": "社外の読み手に伝わる、明瞭で落ち着いた文章にする。過剰な敬語、謝罪、約束を追加しない。",
    "social": "SNSや社内チャットで自然に読める文章にする。熱量は維持するが、煽り・絵文字・感嘆符は勝手に追加しない。文字数制限や要約は明示された場合だけ。",
}


class RewriteError(Exception):
    """An actionable error safe to display without exposing API response bodies."""

    def __init__(self, message: str, *, category: str = "error", http_status: int | None = None):
        super().__init__(message)
        self.category = category  # 失敗レポート用の分類（原稿・キー・応答本文は含めない）
        self.http_status = http_status


# 原文に無いのに追加されやすい評価語・程度表現。検出は警告のみで自動修正しない。
EVALUATIVE_TERMS = ("安全", "安心", "厳重", "確実", "万全", "徹底", "強力", "大幅", "画期的",
                    "にとどまる", "にすぎない", "しっかり", "手軽", "簡単")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the API key to a redirect target.
        return None


def read_text(path: Path, *, allow_empty: bool = False) -> str:
    with path.expanduser().open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise RewriteError(f"入力ファイルが上限 {MAX_BYTES:,} bytes を超えています。省略せず章単位で分割してください。")
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise RewriteError("バイナリファイルは対象外です。UTF-8のテキストを指定してください。")
    if not allow_empty and not text.strip():
        raise RewriteError("空の入力はリライトできません。")
    return text


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        try:
            if KEY_FILE.is_symlink():
                raise RewriteError("APIキーファイルにシンボリックリンクは使用できません。")
            info = KEY_FILE.stat()
            if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                raise RewriteError("APIキーファイルの権限を600にしてください。")
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise RewriteError("APIキーファイルの形式が不正です。")
            key = KEY_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
    if not key:
        raise RewriteError("APIキー未設定です。本人のターミナルで configure を実行するか、GEMINI_API_KEY を設定してください。")
    if any(c.isspace() for c in key) or not key.isascii() or len(key) > 4096:
        raise RewriteError("APIキーの形式が不正です。値を表示せず設定を確認してください。")
    return key


def configure_key(replace: bool = False) -> None:
    if not sys.stdin.isatty():
        raise RewriteError("configure は本人の対話ターミナルで実行してください。チャットにキーを貼らないでください。")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if KEY_FILE.is_symlink():
        raise RewriteError("APIキーファイルにシンボリックリンクは使用できません。")
    if os.path.lexists(KEY_FILE) and not replace:
        raise RewriteError("APIキーは保存済みです。更新する場合は configure --replace を明示してください。")
    key = getpass.getpass("Gemini APIキー（画面には表示されません）: ").strip()
    if not key or any(c.isspace() for c in key) or not key.isascii() or len(key) > 4096:
        raise RewriteError("APIキーの形式が不正です。")
    if replace:
        fd, tmp_name = tempfile.mkstemp(prefix=".key-", dir=KEY_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(key + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, KEY_FILE)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    else:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
    print("APIキーを権限600のローカルファイルに保存しました。API通信は実行していません。")


def code_blocks(text: str) -> list[str]:
    blocks, buffer = [], []
    fence = ""
    for line in text.splitlines(keepends=True):
        if not fence:
            match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if match:
                fence = match.group(1)
                buffer = [line]
        else:
            buffer.append(line)
            if re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", line):
                blocks.append("".join(buffer).rstrip("\r\n"))
                fence, buffer = "", []
    if buffer:  # An unclosed source fence is still protected verbatim.
        blocks.append("".join(buffer).rstrip("\r\n"))
    return blocks


def text_metrics(text: str) -> dict[str, Any]:
    """AI臭の簡易lint。文長の均質さ・段落の文数の均質さ・体言止め・対比構文を数える。
    判定はしない（疑いの提示のみ）。閾値はコーパス検証していない参考値。"""
    for block in code_blocks(text):  # コードブロックの中身は文として数えない
        text = text.replace(block, "")
    body = "\n".join(line for line in text.splitlines() if not re.match(r"^\s*(#|\||-|\*|\d+\.|```|~~~)", line))
    body = re.sub(r"https?://\S+|`[^`\n]+`", "", body)
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])\s*|\n+", body) if s.strip()]
    lengths = [len(s) for s in sentences]

    def cv(values: list[int]) -> float | None:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        if mean == 0:
            return None
        var = sum((v - mean) ** 2 for v in values) / len(values)
        return round((var ** 0.5) / mean, 3)

    para_counts = [len([s for s in re.split(r"(?<=[。！？!?])\s*|\n+", p) if s.strip()]) for p in paragraphs]
    taigen = sum(1 for s in sentences if re.search(r"[\u4e00-\u9fff\u30a0-\u30ff]。?$", s.rstrip("。！？!?") + ""))
    contrast = sum(s.count("ではなく") for s in sentences)
    return {
        "sentences": len(sentences),
        "mean_sentence_length": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "sentence_length_cv": cv(lengths),  # 低いほど文長が均質（AI臭の疑い）
        "paragraph_sentence_count_cv": cv(para_counts),  # 低いほど段落の文数が均質
        "taigendome_ratio": round(taigen / len(sentences), 3) if sentences else 0,
        "contrast_dewanaku_ratio": round(contrast / len(sentences), 3) if sentences else 0,
        "abstract_noun_count": len(re.findall(r"[\u4e00-\u9fff]{1,3}(?:性|化|的)(?=[はがをにでのと、。])", body)),
    }


def diagnostics_hints(metrics: dict[str, Any]) -> list[str]:
    """原稿側の疑いを、Geminiへ渡す短いヒントにする。"""
    hints = []
    if metrics["sentences"] >= 6:
        if metrics["sentence_length_cv"] is not None and metrics["sentence_length_cv"] < 0.35:
            hints.append("文の長さが均質。短い文と長い文を混ぜて緩急をつける")
        if metrics["taigendome_ratio"] == 0:
            hints.append("体言止めがゼロ。適度に混ぜる")
        if metrics["contrast_dewanaku_ratio"] > 0.05:
            hints.append("「〜ではなく」の対比構文が多い。別の言い方に散らす")
        if metrics["mean_sentence_length"] > 55:
            hints.append("一文が長い。読点の多い文は分ける")
    if metrics["paragraph_sentence_count_cv"] is not None and metrics["paragraph_sentence_count_cv"] < 0.2 and metrics["sentences"] >= 9:
        hints.append("段落ごとの文数が揃いすぎ。段落の長さを変える")
    return hints


def protected_literals(source: str, keep: list[str]) -> list[str]:
    for item in keep:
        if not item or item not in source:
            raise RewriteError("維持指定の文字列が原文にありません。keep設定を確認してください。")
    urls = re.findall(r"https?://[^\s<>\"'\)\]`。、）」]+", source)
    inline = re.findall(r"(?<!`)`[^`\n]+`(?!`)", source)
    citations = re.findall(r"\[\^?\d+\]", source)
    return list(dict.fromkeys(keep + urls + inline + citations + code_blocks(source)))


def build_payload(source: str, brief: str, style: str, keep: list[str], mode: str,
                  thinking: str, max_output_tokens: int, audience: str = "") -> tuple[dict[str, Any], list[str]]:
    system = read_text(SKILL_ROOT / "references" / "rewrite-system.md")
    locks = protected_literals(source, keep)
    task = {
        "task": "Rewrite source_text. Return only the complete rewritten text.",
        "mode": mode,
        "mode_instruction": MODES[mode],
        "audience": audience,
        "audience_instruction": ("この読み手が一読で理解できる語彙と文の長さにする。専門用語は初出で言い換え、抽象語には具体を添える。情報は省略しない。" if audience else ""),
        "source_diagnostics_hints": diagnostics_hints(text_metrics(source)),
        "editing_brief": brief,
        "style_reference_not_factual_source": style,
        "verbatim_locks": locks,
        "source_text": source,
    }
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(task, ensure_ascii=False)}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingLevel": thinking},  # 公式REST例に合わせて小文字
        },
    }
    # Do not set temperature, topP, topK, candidateCount, or thinkingBudget.
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise RewriteError(f"原文・文体例・保護文字列を含む送信データが {MAX_BYTES:,} bytes を超えています。自動要約せず分割してください。")
    return payload, locks


def request_gemini(payload: dict[str, Any], key: str, model: str, timeout: float,
                   retries: int = 0) -> dict[str, Any]:
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
        raise RewriteError("モデルIDの形式が不正です。gemini-3.8-flash などを指定してください。")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_ROOT}/{model}:generateContent", data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(retries + 1):
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RewriteError("API応答がローカル上限を超えました。出力は保存していません。")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RewriteError("API応答の形式が不正です。")
            return data
        except urllib.error.HTTPError as exc:
            code = exc.code
            header = exc.headers.get("Retry-After", "") if exc.headers else ""
            exc.close()
            # Errors may echo sensitive input. Never print their body or key.
            delay = float(header) if re.fullmatch(r"\d+(?:\.\d+)?", header) else min(2 ** attempt, 8)
            if code in (429, 503) and attempt < retries and delay <= 60:
                print(f"HTTP {code}: 明示設定の範囲内で再試行します ({attempt + 1}/{retries})。", file=sys.stderr)
                time.sleep(delay)
                continue
            hints = {
                400: "モデル・リクエスト設定を確認してください。",
                401: "APIキーを確認してください。",
                403: "APIキーの制限、プロジェクト権限、利用条件を確認してください。",
                404: "指定モデルへのアクセスを確認してください。別モデルには切り替えていません。",
                429: "割り当て・レート制限・課金設定を確認してください。",
                503: "サービスが一時的に利用できません。自動的な別モデルへの切り替えは行いません。",
            }
            raise RewriteError(f"Gemini API HTTP {code}。{hints.get(code, 'API通信が失敗しました。')} 出力は保存していません。",
                               category="http", http_status=code) from None
        except (urllib.error.URLError, OSError) as exc:
            category, hint = classify_network_error(exc)
            raise RewriteError(
                f"API通信に失敗しました（分類: {category}）。{hint} "
                "Google側で処理・課金されたかは不明です。通信エラーは自動再試行しません。"
                "通信制限のある環境では、正規の権限申請手順で許可を得てから再実行してください。",
                category=category) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RewriteError("API応答をJSONとして読み取れません。出力は保存していません。", category="bad_response") from None
    raise RewriteError("API呼び出しが完了しませんでした。", category="incomplete")


def classify_network_error(exc: BaseException) -> tuple[str, str]:
    """通信例外を、原稿・キー・本文を含まない分類名と対処ヒントに変換する。"""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", "応答待ちが上限を超えました。--timeout の延長は本人が判断してください。"
    if isinstance(reason, socket.gaierror):
        return "dns", "ホスト名を解決できません。ネットワーク接続やサンドボックスの通信制限を確認してください。"
    if isinstance(reason, ssl.SSLError):
        return "tls", "TLS証明書の検証に失敗しました。プロキシや証明書設定を確認してください。"
    if isinstance(reason, ConnectionRefusedError):
        return "refused", "接続が拒否されました。プロキシやファイアウォール設定を確認してください。"
    if isinstance(reason, ConnectionError):
        return "connection", "接続が切断されました。"
    return "network", "分類できない通信エラーです。"


def extract_text(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise RewriteError("入力がAPIでブロックされました。別モデルへの迂回や本文保存は行いません。", category="blocked")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise RewriteError("APIから本文が返されませんでした。")
    candidate = candidates[0]
    reason = candidate.get("finishReason")
    if reason != "STOP":
        label = reason if isinstance(reason, str) and re.fullmatch(r"[A-Z_]+", reason) else "UNKNOWN"
        raise RewriteError(f"生成が正常完了していません (finishReason={label})。途中の本文は保存しません。", category="not_stop")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p["text"] for p in parts
                   if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str))
    if not text.strip():
        raise RewriteError("空の本文が返されました。出力は保存していません。")
    meta = {"finish_reason": reason, "model_returned": data.get("modelVersion"),
            "usage": data.get("usageMetadata", {})}
    return text.rstrip("\r\n") + "\n", meta


def inspect_rewrite(source: str, rewritten: str, locks: list[str]) -> dict[str, Any]:
    failed = [item for item in locks if rewritten.count(item) != source.count(item)]
    if failed:
        # Do not expose potentially sensitive locked text on stderr.
        raise RewriteError(f"保護対象の文字列・URL・コード等で {len(failed)} 件の改変／欠落／重複を検出しました。本文は保存しません。", category="verbatim_lock")
    pattern = r"(?<![A-Za-z0-9])[0-9０-９]+(?:[.,．，][0-9０-９]+)*"
    old, new = Counter(re.findall(pattern, source)), Counter(re.findall(pattern, rewritten))
    missing, added = dict(old - new), dict(new - old)
    ratio = len(rewritten.strip()) / max(len(source.strip()), 1)
    warnings = []
    if missing or added:
        warnings.append("数値の表記・出現数に差があります。日付・金額・割合を原文と照合してください。")
    if ratio < 0.70:
        warnings.append("文字数が原文の70%未満です。意図しない要約や情報欠落を確認してください。")
    if ratio > 1.40:
        warnings.append("文字数が原文の140%超です。事実の追加や冗長化を確認してください。")
    added_terms = {t: rewritten.count(t) - source.count(t) for t in EVALUATIVE_TERMS if rewritten.count(t) > source.count(t)}
    if added_terms:
        warnings.append("原文に無い評価語・程度表現が追加されています: " + "、".join(f"{t}(+{n})" for t, n in added_terms.items()) + "。主張の強さが変わっていないか確認してください。")
    # 原文に無いカタカナ語（機能名・用語の勝手な追加を拾う。言い換えでも出るので疑いの提示）
    katakana = r"[\u30a1-\u30fa\u30fc]{2,}"
    new_katakana = sorted(set(re.findall(katakana, rewritten)) - set(re.findall(katakana, source)))
    if new_katakana:
        warnings.append("原文に無いカタカナ語が追加されています: " + "、".join(new_katakana) + "。事実や機能の追加になっていないか確認してください。")
    before, after = text_metrics(source), text_metrics(rewritten)
    if (before["sentences"] >= 6 and after["sentences"] >= 6
            and before["sentence_length_cv"] is not None and after["sentence_length_cv"] is not None):
        if after["sentence_length_cv"] < before["sentence_length_cv"] * 0.8:
            warnings.append("出力の文長が原文より均質になっています（AI臭が増えた疑い）。readability を確認してください。")
    return {
        "verbatim_check": "passed", "protected_literal_count": len(locks),
        "readability": {"source": before, "output": after,
                        "note": "簡易lint。sentence_length_cv と paragraph_sentence_count_cv は高いほど人間らしい緩急。閾値は未検証の参考値。"},
        "source_characters": len(source), "output_characters": len(rewritten),
        "length_ratio": round(ratio, 4),
        "numeric_literals_missing": missing, "numeric_literals_added": added,
        "evaluative_terms_added": added_terms,
        "new_katakana_terms": new_katakana,
        "warnings": warnings,
        "semantic_review_required": True,
        "limitation": "機械検査は意味・事実・ニュアンスの保持を保証しません。呼び出し元が原文と差分を確認してください。",
    }


def resolve_model(cli_model: str | None) -> tuple[str, str]:
    """適用するモデルIDと、その設定元（--model / 環境変数 / 既定）を返す。"""
    if cli_model:
        return cli_model, "--model"
    env = os.environ.get("GEMINI_REWRITE_MODEL", "").strip()
    if env:
        return env, "環境変数 GEMINI_REWRITE_MODEL"
    return DEFAULT_MODEL, "既定値"


def write_failure_report(output: Path, model: str, mode: str, thinking: str,
                         started: float, exc: RewriteError) -> Path | None:
    """失敗時の診断を残す。原稿・キー・API応答本文は含めない。既存ファイルは上書きしない。"""
    path = Path(str(output) + ".failure.json")
    if os.path.lexists(path) or not path.parent.is_dir():
        return None
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "api_called": True, "succeeded": False,
              "model_requested": model, "mode": mode, "thinking": thinking,
              "elapsed_seconds": round(time.monotonic() - started, 1),
              "error_category": exc.category, "http_status": exc.http_status, "message": str(exc),
              "billing_status": "unknown"}
    try:
        publish_files({path: json.dumps(report, ensure_ascii=False, indent=2) + "\n"})
    except OSError:
        return None
    return path


def output_paths(output: Path) -> list[Path]:
    return [output, Path(str(output) + ".diff"), Path(str(output) + ".report.json")]


def preflight_output(paths: list[Path], inputs: list[Path]) -> None:
    resolved = [p.expanduser().resolve() for p in paths]
    if len(set(resolved)) != len(resolved):
        raise RewriteError("出力先が重複しています。")
    input_paths = {p.expanduser().resolve() for p in inputs}
    for path, canonical in zip(paths, resolved):
        if canonical in input_paths:
            raise RewriteError("入力ファイルへの上書きは禁止しています。別の出力先を指定してください。")
        if os.path.lexists(path):
            raise RewriteError("出力先または付随ファイルが既に存在します。上書きせず別の出力名を指定してください。")
        if not path.parent.is_dir():
            raise RewriteError("出力先の親ディレクトリがありません。先に作成してください。")


def publish_files(files: dict[Path, str]) -> None:
    created = []
    try:
        for path, content in files.items():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def run_rewrite(args: argparse.Namespace) -> dict[str, Any]:
    source_path, output = args.input.expanduser(), args.output.expanduser()
    inputs = [source_path]
    source = read_text(source_path)
    brief, style, keep = args.brief or "", "", []
    if args.brief_file:
        brief = read_text(args.brief_file)
        inputs.append(args.brief_file)
    if args.style_file:
        style = read_text(args.style_file)
        inputs.append(args.style_file)
    if args.keep_file:
        try:
            keep = json.loads(read_text(args.keep_file))
        except json.JSONDecodeError as exc:
            raise RewriteError("keep-file のJSONが不正です。文字列にタブや改行を含めず、[\"語1\", \"語2\"] の形式にしてください。") from exc
        if not isinstance(keep, list) or not all(isinstance(x, str) for x in keep):
            raise RewriteError("keep-file は文字列のJSON配列にしてください。")
        inputs.append(args.keep_file)
    paths = output_paths(output)
    preflight_output(paths, inputs + [KEY_FILE])
    model, model_source = resolve_model(args.model)
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
        raise RewriteError("モデルIDの形式が不正です。")
    print(f"モデル: {model}（設定元: {model_source}）", file=sys.stderr)
    payload, locks = build_payload(source, brief, style, keep, args.mode, args.thinking, args.max_output_tokens, args.audience or "")
    if args.dry_run:
        return {"api_called": False, "model_requested": model, "mode": args.mode,
                "thinking": args.thinking, "request_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                "protected_literal_count": len(locks), "output_files": [str(x) for x in paths]}
    started = time.monotonic()
    try:
        data = request_gemini(payload, get_api_key(), model, args.timeout, args.retries)
        rewritten, meta = extract_text(data)
        audit = inspect_rewrite(source, rewritten, locks)
    except RewriteError as exc:
        failure = write_failure_report(output, model, args.mode, args.thinking, started, exc)
        if failure:
            print(f"診断レポート: {failure}", file=sys.stderr)
        raise
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "api_called": True,
              "model_requested": model, "model_source": model_source, "mode": args.mode, "thinking": args.thinking,
              **meta, **audit}
    # Normalize the final newline for a legible unified diff; do not mutate source.
    diff = "".join(difflib.unified_diff(
        (source.rstrip("\r\n") + "\n").splitlines(keepends=True), rewritten.splitlines(keepends=True),
        fromfile=str(source_path), tofile=str(output),
    ))
    publish_files({paths[0]: rewritten, paths[1]: diff,
                   paths[2]: json.dumps(report, ensure_ascii=False, indent=2) + "\n"})
    return {"api_called": True, "model_requested": model, "model_returned": meta["model_returned"],
            "output_files": [str(x) for x in paths], "warning_count": len(audit["warnings"]),
            "semantic_review_required": True}


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    config = subs.add_parser("configure", help="本人の対話ターミナルでAPIキーを保存（非表示入力）")
    config.add_argument("--replace", action="store_true")
    subs.add_parser("check", help="ローカル設定を検査。API通信はしない")
    rewrite = subs.add_parser("rewrite", help="指定した原稿を別ファイルにリライト")
    rewrite.add_argument("--input", type=Path, required=True)
    rewrite.add_argument("--output", type=Path, required=True)
    brief = rewrite.add_mutually_exclusive_group()
    brief.add_argument("--brief")
    brief.add_argument("--brief-file", type=Path)
    rewrite.add_argument("--style-file", type=Path)
    rewrite.add_argument("--keep-file", type=Path)
    rewrite.add_argument("--mode", choices=tuple(MODES), default="auto", help="既定auto（おまかせ）")
    rewrite.add_argument("--audience", help="読み手（例: 教育委員会の担当者、中学生）。語彙と文の長さの基準になる")
    rewrite.add_argument("--thinking", choices=("low", "medium", "high"), default="low",
                         help="既定low。構成変更を伴う編集だけmedium以上を明示")
    rewrite.add_argument("--model", help="既定はgemini-3.8-flash。モデル変更はユーザー明示時だけ")
    rewrite.add_argument("--max-output-tokens", type=int, default=32768)
    rewrite.add_argument("--timeout", type=float, default=180)
    rewrite.add_argument("--retries", type=int, default=0, help="429/503だけの追加試行数。既定0、最大3")
    rewrite.add_argument("--dry-run", action="store_true", help="入力と保存先だけ検査。API通信・保存なし")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            configure_key(args.replace)
        elif args.command == "check":
            get_api_key()
            model, model_source = resolve_model(None)
            print(json.dumps({"python": sys.version.split()[0], "local_config": "passed",
                              "api_key_available": True, "api_key_valid": "not_checked",
                              "connectivity": "not_checked", "model_access": "not_checked",
                              "api_called": False, "model": model, "model_source": model_source}, ensure_ascii=False))
        else:
            if not 1 <= args.max_output_tokens <= 65536:
                raise RewriteError("max-output-tokens は1〜65536で指定してください。")
            if not 0 < args.timeout <= 600 or not 0 <= args.retries <= 3:
                raise RewriteError("timeoutは0超〜600秒、retriesは0〜3で指定してください。")
            print(json.dumps(run_rewrite(args), ensure_ascii=False, indent=2))
        return 0
    except (RewriteError, OSError, UnicodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
