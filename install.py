#!/usr/bin/env python3
"""Install one shared skill for local Codex and Claude Code. No network access."""
from __future__ import annotations
import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


def install(home: Path, replace: bool = False) -> tuple[Path, Path]:
    source = Path(__file__).resolve().parent / "gemini-rewrite"
    codex = home.expanduser().resolve() / ".agents" / "skills" / "gemini-rewrite"
    claude = home.expanduser().resolve() / ".claude" / "skills" / "gemini-rewrite"
    if not (source / "SKILL.md").is_file():
        raise RuntimeError("同梱のgemini-rewrite/SKILL.mdが見つかりません。ZIP全体を展開してください。")
    if source.resolve() in (codex.resolve(), claude.resolve()):
        raise RuntimeError("インストール元とインストール先を別の場所にしてください。")
    for dest in (codex, claude):
        if os.path.lexists(dest) and not replace:
            raise RuntimeError(f"既存のSkillがあります: {dest}\n更新する場合だけ --replace を指定してください。既存版はバックアップされます。")
    codex.parent.mkdir(parents=True, exist_ok=True)
    claude.parent.mkdir(parents=True, exist_ok=True)
    # Backups stay outside skills/ so the host does not load duplicate skills.
    backup_root = home.expanduser().resolve() / ".local" / "share" / "gemini-rewrite-backups"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    backups: list[tuple[Path, Path]] = []
    staged = Path(tempfile.mkdtemp(prefix=".rewrite-stage-", dir=codex.parent))
    installed_codex, installed_claude = False, False
    try:
        shutil.copytree(source, staged, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        for label, dest in (("codex", codex), ("claude", claude)):
            if os.path.lexists(dest):
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup = backup_root / f"{stamp}-{label}"
                dest.rename(backup)
                backups.append((dest, backup))
        staged.rename(codex)
        installed_codex = True
        claude.symlink_to(codex, target_is_directory=True)
        installed_claude = True
    except BaseException:
        if installed_claude:
            claude.unlink(missing_ok=True)
        if installed_codex:
            shutil.rmtree(codex)
        for dest, backup in reversed(backups):
            backup.rename(dest)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    for _, backup in backups:
        print(f"バックアップ: {backup}")
    return codex, claude


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true", help="既存版を退避して更新")
    parser.add_argument("--home", type=Path, default=Path.home(), help="通常は省略。テスト用のインストール先ホーム")
    args = parser.parse_args()
    try:
        codex, claude = install(args.home, args.replace)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Codex: {codex}\nClaude Code: {claude} -> {codex}")
    print("Skillを配置しました。APIキーの設定と実APIによる疎通確認は未実施です。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
