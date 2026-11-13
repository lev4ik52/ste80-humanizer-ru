#!/usr/bin/env python3
"""Validate this repository's constrained skill format and release allowlist."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote

try:
    from .run_evals import load_cases, load_triggers
except ImportError:
    from run_evals import load_cases, load_triggers

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-manifest.json"
ROOT_FILES = (
    "SKILL.md", "README.md", "README.en.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "UPSTREAM.md", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
    "TEST_REPORT.md", ".gitignore", ".gitattributes",
)
REQUIRED_SUPPORT = (
    "agents/openai.yaml", "references/ru-rules.md", "references/fidelity.md",
    "references/genres.md", "references/en-profile.md", "references/evaluation.md",
    "assets/voice-profile.example.md", "assets/review-template.md",
    "scripts/humanizer_check.py", "scripts/run_evals.py", "scripts/validate_repo.py",
    "scripts/publish.py", "tests/test_checker.py", "tests/test_tooling.py",
    "evals/cases.json", "evals/triggers.json", "evals/README.md",
    ".github/workflows/ci.yml",
)
PATTERNS = (
    "agents/*.yaml", "references/*.md", "assets/*.md", "scripts/*.py", "tests/*.py",
    "evals/cases.json", "evals/triggers.json", "evals/README.md", "examples/*.md",
    ".github/workflows/*.yml", ".github/ISSUE_TEMPLATE/*.md", ".github/dependabot.yml",
)


def file_set(root: Path) -> list[str]:
    paths = {path for path in ROOT_FILES if (root/path).is_file()}
    for pattern in PATTERNS:
        paths.update(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())
    return sorted(paths)


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    posix = PurePosixPath(relative)
    if (not relative or not posix.parts or relative != posix.as_posix()
            or posix.is_absolute() or ".." in posix.parts or "\\" in relative or ":" in relative):
        raise ValueError(f"Небезопасный путь: {relative}")
    path = root.joinpath(*posix.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Путь выходит за корень: {relative}")
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError(f"Символические ссылки запрещены в пакете: {relative}")
        cursor = cursor.parent
    return path


def manifest_data(root: Path) -> dict:
    files = {}
    for relative in file_set(root):
        path = safe_path(root, relative)
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema_version": 1, "skill": "textrefine-ru", "version": "1.1.0", "files": files}


def refresh_manifest(root: Path) -> None:
    (root/MANIFEST).write_text(json.dumps(manifest_data(root),ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")


def load_manifest(root: Path) -> dict:
    manifest_path = safe_path(root, MANIFEST)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("Неизвестный формат manifest")
    for path, digest in data["files"].items():
        if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
            raise ValueError("Некорректная запись manifest")
        safe_path(root,path)
    return data


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative in ROOT_FILES + REQUIRED_SUPPORT:
        if not (root/relative).is_file():
            errors.append(f"Нет обязательного файла: {relative}")
    try:
        manifest = load_manifest(root)
        actual = manifest_data(root)
        if manifest != actual:
            errors.append("Manifest не совпадает с файлами. После проверки изменений выполните --refresh-manifest.")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    skill_path=root/'SKILL.md'
    if not skill_path.is_file():
        return errors
    skill=skill_path.read_text(encoding='utf-8')
    header=re.match(r'\A---\n(.*?)\n---\n',skill,re.S)
    if not header:
        errors.append('Нет корректного frontmatter SKILL.md')
    else:
        name=re.search(r'^name: (.+)$',header[1],re.M)
        if not name or name[1] != 'textrefine-ru':
            errors.append('Неверное имя скилла')
        description=re.search(r'^description: >-\n((?:  [^\n]*\n?)+)',header[1],re.M)
        if not description or not 1 <= len(' '.join(line.strip() for line in description[1].splitlines())) <= 1024:
            errors.append('Некорректное описание скилла')
        if 'version: "1.1.0"' not in header[1] or 'license: MIT' not in header[1]:
            errors.append('Проверьте версию и лицензию')
    if len(skill.splitlines()) > 250:
        errors.append('SKILL.md превышает 250 строк')
    changelog = root/'CHANGELOG.md'
    if changelog.is_file() and '## 1.1.0' not in changelog.read_text(encoding='utf-8'):
        errors.append('Версия CHANGELOG не совпадает')
    for loader, relative in ((load_cases, 'evals/cases.json'), (load_triggers, 'evals/triggers.json')):
        try:
            loader(safe_path(root, relative))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f'{relative}: {exc}')
    for relative in file_set(root):
        try:
            path=safe_path(root,relative)
            raw=path.read_bytes()
            text=raw.decode('utf-8')
            if b'\r\n' in raw:
                errors.append(f'Ожидаются LF-переносы: {relative}')
            if not text.endswith('\n'):
                errors.append(f'Нет финального перевода строки: {relative}')
            if path.suffix=='.py':
                ast.parse(text,filename=relative,feature_version=(3,10))
            if path.suffix=='.json':
                json.loads(text)
            # High-signal leakage checks, not a full secret scanner.
            if re.search(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',text):
                errors.append(f'Возможный секрет: {relative}')
            if path.suffix=='.md':
                for target in re.findall(r'\[[^\]\n]*\]\(([^)\s]+)\)',text):
                    if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:',target) or target.startswith('#'):
                        continue
                    local=unquote(target.split('#',1)[0])
                    if local and not (path.parent/local).exists():
                        errors.append(f'Битая локальная ссылка: {relative}: {target}')
        except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
            errors.append(f'{relative}: {exc}')
    return errors


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-manifest',action='store_true',help='Явно обновить список публикуемых файлов и SHA-256')
    args=parser.parse_args(argv)
    try:
        if args.refresh_manifest:
            refresh_manifest(ROOT)
        errors=validate()
    except (OSError,ValueError) as exc:
        errors=[str(exc)]
    if errors:
        print('\n'.join('ERROR: '+item for item in errors),file=sys.stderr)
        return 1
    print(f'Repository validation passed: {len(file_set(ROOT))} allowlisted files + manifest')
    return 0

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
