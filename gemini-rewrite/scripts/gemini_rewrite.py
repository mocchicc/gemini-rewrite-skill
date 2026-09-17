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
    "light": "誤字・不自然な助詞・読点・読みにくい箇所だけを最小限直す。語り口と構成を維持する。",
    "natural": "情報を削らず、自然で読みやすい日本語にする。過剰な丁寧語や定型的なAI表現を避ける。",
    "business": "社外の読み手に伝わる、明瞭で落ち着いた文章にする。過剰な敬語、謝罪、約束を追加しない。",
    "social": "SNSや社内チャットで自然に読める文章にする。熱量は維持するが、煽り・絵文字・感嘆符は勝手に追加しない。文字数制限や要約は明示された場合だけ。",
}


class RewriteError(Exception):
    """An actionable error safe to display without exposing API response bodies."""


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


def protected_literals(source: str, keep: list[str]) -> list[str]:
    for item in keep:
        if not item or item not in source:
            raise RewriteError("維持指定の文字列が原文にありません。keep設定を確認してください。")
    urls = re.findall(r"https?://[^\s<>\"'\)\]`]+", source)
    inline = re.findall(r"(?<!`)`[^`\n]+`(?!`)", source)
    citations = re.findall(r"\[\^?\d+\]", source)
    return list(dict.fromkeys(keep + urls + inline + citations + code_blocks(source)))


def build_payload(source: str, brief: str, style: str, keep: list[str], mode: str,
                  thinking: str, max_output_tokens: int) -> tuple[dict[str, Any], list[str]]:
    system = read_text(SKILL_ROOT / "references" / "rewrite-system.md")
    locks = protected_literals(source, keep)
    task = {
        "task": "Rewrite source_text. Return only the complete rewritten text.",
        "mode": mode,
        "mode_instruction": MODES[mode],
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
            "thinkingConfig": {"thinkingLevel": thinking.upper()},
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
            raise RewriteError(f"Gemini API HTTP {code}。{hints.get(code, 'API通信が失敗しました。')} 出力は保存していません。") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            raise RewriteError("API通信が失敗またはタイムアウトしました。課金済みの可能性があるため通信エラーは自動再試行しません。") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RewriteError("API応答をJSONとして読み取れません。出力は保存していません。") from None
    raise RewriteError("API呼び出しが完了しませんでした。")


def extract_text(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise RewriteError("入力がAPIでブロックされました。別モデルへの迂回や本文保存は行いません。")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise RewriteError("APIから本文が返されませんでした。")
    candidate = candidates[0]
    reason = candidate.get("finishReason")
    if reason != "STOP":
        label = reason if isinstance(reason, str) and re.fullmatch(r"[A-Z_]+", reason) else "UNKNOWN"
        raise RewriteError(f"生成が正常完了していません (finishReason={label})。途中の本文は保存しません。")
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
        raise RewriteError(f"保護対象の文字列・URL・コード等で {len(failed)} 件の改変／欠落／重複を検出しました。本文は保存しません。")
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
    return {
        "verbatim_check": "passed", "protected_literal_count": len(locks),
        "source_characters": len(source), "output_characters": len(rewritten),
        "length_ratio": round(ratio, 4),
        "numeric_literals_missing": missing, "numeric_literals_added": added,
        "warnings": warnings,
        "semantic_review_required": True,
        "limitation": "機械検査は意味・事実・ニュアンスの保持を保証しません。呼び出し元が原文と差分を確認してください。",
    }


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
        keep = json.loads(read_text(args.keep_file))
        if not isinstance(keep, list) or not all(isinstance(x, str) for x in keep):
            raise RewriteError("keep-file は文字列のJSON配列にしてください。")
        inputs.append(args.keep_file)
    paths = output_paths(output)
    preflight_output(paths, inputs + [KEY_FILE])
    model = args.model or os.environ.get("GEMINI_REWRITE_MODEL", DEFAULT_MODEL)
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
        raise RewriteError("モデルIDの形式が不正です。")
    payload, locks = build_payload(source, brief, style, keep, args.mode, args.thinking, args.max_output_tokens)
    if args.dry_run:
        return {"api_called": False, "model_requested": model, "mode": args.mode,
                "thinking": args.thinking, "request_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                "protected_literal_count": len(locks), "output_files": [str(x) for x in paths]}
    data = request_gemini(payload, get_api_key(), model, args.timeout, args.retries)
    rewritten, meta = extract_text(data)
    audit = inspect_rewrite(source, rewritten, locks)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "api_called": True,
              "model_requested": model, "mode": args.mode, "thinking": args.thinking,
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
    rewrite.add_argument("--mode", choices=tuple(MODES), default="natural")
    rewrite.add_argument("--thinking", choices=("low", "medium", "high"), default="medium")
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
            print(json.dumps({"python": sys.version.split()[0], "api_key_available": True,
                              "api_called": False, "model_default": os.environ.get("GEMINI_REWRITE_MODEL", DEFAULT_MODEL)}, ensure_ascii=False))
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
