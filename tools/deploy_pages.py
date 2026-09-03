#!/usr/bin/env python3
"""Validate and deploy only SpeechMap's generated Pages tree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = REPO_ROOT / "dist"
WRANGLER = REPO_ROOT / "node_modules" / ".bin" / "wrangler"
REQUIRED_FILES = (
    ".speechmap-build-output",
    "index.html",
    "data/metadata-core.json",
    "data/pair-template.html",
)
FORBIDDEN_NAMES = {
    ".cache",
    ".git",
    ".venv",
    ".wrangler",
    "env",
    "node_modules",
    "venv",
}


def is_forbidden_name(name: str) -> bool:
    return name in FORBIDDEN_NAMES or name.startswith(".venv-")


def validate_deploy_root(deploy_root: Path) -> tuple[int, int]:
    """Return file count and byte count after validating an isolated build tree."""
    if deploy_root.is_symlink():
        raise RuntimeError(f"deploy root must not be a symlink: {deploy_root}")
    if not deploy_root.is_dir():
        raise RuntimeError(f"deploy root is missing: {deploy_root}")

    resolved_root = deploy_root.resolve(strict=True)
    for relative_path in REQUIRED_FILES:
        required = resolved_root / relative_path
        if not required.is_file() or required.is_symlink():
            raise RuntimeError(f"required build output is missing: {required}")

    file_count = 0
    byte_count = 0
    for current_root, dirnames, filenames in os.walk(resolved_root):
        current = Path(current_root)
        for name in dirnames:
            path = current / name
            if is_forbidden_name(name):
                raise RuntimeError(f"forbidden development directory in deploy tree: {path}")
            if path.is_symlink():
                raise RuntimeError(f"symlink in deploy tree: {path}")
        for name in filenames:
            path = current / name
            if is_forbidden_name(name):
                raise RuntimeError(f"forbidden development file in deploy tree: {path}")
            if path.is_symlink():
                raise RuntimeError(f"symlink in deploy tree: {path}")
            file_count += 1
            byte_count += path.stat().st_size

    if file_count == 0:
        raise RuntimeError(f"deploy root contains no files: {resolved_root}")
    return file_count, byte_count


def build_deploy_command(deploy_root: Path = DEPLOY_ROOT) -> list[str]:
    """Build the fixed production command; callers cannot replace the upload root."""
    return [
        str(WRANGLER),
        "pages",
        "deploy",
        str(deploy_root.resolve(strict=True)),
        "--project-name",
        "speechmap",
        "--branch",
        "main",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and deploy SpeechMap's generated dist directory."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate the upload tree without contacting Cloudflare",
    )
    args = parser.parse_args()

    file_count, byte_count = validate_deploy_root(DEPLOY_ROOT)
    resolved_root = DEPLOY_ROOT.resolve(strict=True)
    print(f"Pages upload root: {resolved_root}")
    print(f"Validated {file_count} files ({byte_count} bytes); no forbidden paths.")

    if args.check_only:
        return
    if not WRANGLER.is_file():
        raise RuntimeError(f"pinned Wrangler is missing; run npm install: {WRANGLER}")
    subprocess.run(build_deploy_command(resolved_root), cwd=REPO_ROOT, check=True)


if __name__ == "__main__":
    main()
