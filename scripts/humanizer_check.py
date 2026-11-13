#!/usr/bin/env python3
"""Offline, read-only Russian editorial diagnostics. Not an authorship detector."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable

VERSION = "1.1.0"
MAX_BYTES = 2_000_000
LIMITS = {"light": 45, "humanize": 32, "strict": 24}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    value: str


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    line: int
    column: int
    excerpt: str
    message: str


def protected_spans(text: str, extra: Iterable[str] = ()) -> list[Span]:
    """Conservative subset of Markdown/HTML; offsets refer to original text.

    Overlapping regions are kept only once, with outer structural blocks taking
    priority. This is deliberately not a complete Markdown parser.
    """
    found: list[Span] = []
    occupied = bytearray(len(text))

    def add(start: int, end: int, kind: str) -> None:
        if start < end and occupied.find(b"\x01", start, end) == -1:
            found.append(Span(start, end, kind, text[start:end]))
            occupied[start:end] = b"\x01" * (end - start)

    front = re.match(r"\A\ufeff?---[ \t]*\r?\n.*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)", text, re.S)
    if front:
        add(front.start(), front.end(), "frontmatter")
    offset = 0
    opening: tuple[int, str, int] | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)", line)
        if opening:
            if marker and marker[1][0] == opening[1] and len(marker[1]) >= opening[2] and not marker[2].strip():
                add(opening[0], offset + len(line), "code_block")
                opening = None
        elif marker:
            opening = (offset, marker[1][0], len(marker[1]))
        offset += len(line)
    if opening:
        add(opening[0], len(text), "code_block")

    # Explicit user protections are inserted before inferred inline spans.
    for phrase in extra:
        if not phrase:
            raise ValueError("Защищаемая фраза не должна быть пустой")
        for match in re.finditer(re.escape(phrase), text):
            add(match.start(), match.end(), "explicit")

    patterns = [
        ("code_indented", r"^(?: {4}|\t)[^\r\n]*(?:\r?\n|$)", re.M),
        ("blockquote", r"^ {0,3}>[^\r\n]*(?:\r?\n|$)", re.M),
        ("comment", r"<!--[\s\S]*?(?:-->|\Z)", 0),
        ("code_inline", r"(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", 0),
        ("reference", r"^ {0,3}\[[^\]\r\n]+\]:[^\r\n]*", re.M),
        ("link_target", r"\]\((?:[^()\\\r\n]|\\.|\([^()\r\n]*\))*\)", 0),
        ("placeholder", r"\{\{[^{}\r\n]+\}\}|\$\{[^{}\r\n]+\}|\{[\w.-]+\}|%(?:\([\w]+\)|\d+\$)?[-+0 #]*\d*(?:\.\d+)?[sdfi]", 0),
        ("html", r"</?[A-Za-z][^>]*>", 0),
        ("quote", r"«[^«»]*»|“[^“”]*”|„[^„“]*“|\"[^\"\r\n]*\"", 0),
        ("email", r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", 0),
        ("url", r"(?:https?://|www\.)[^\s<>\"«»]+", 0),
    ]
    for kind, pattern, flags in patterns:
        for match in re.finditer(pattern, text, flags):
            end = match.end()
            if kind == "url":
                while end > match.start() and text[end - 1] in ".,;:!?":
                    end -= 1
                while end > match.start() and text[end - 1] == ")" and text[match.start():end].count(")") > text[match.start():end].count("("):
                    end -= 1
            add(match.start(), end, kind)
    return sorted(found, key=lambda item: item.start)


def mask_spans(text: str, spans: Iterable[Span]) -> str:
    chars = list(text)
    for span in spans:
        for index in range(span.start, span.end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def line_column(text: str, offset: int) -> tuple[int, int]:
    return text.count("\n", 0, offset) + 1, offset - text.rfind("\n", 0, offset)


def normal_space(value: str) -> str:
    return re.sub(r"[ \t\u00a0\u202f]+", " ", value).strip()


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Approximate sentence boundaries, ignoring common Russian abbreviations."""
    result: list[tuple[int, int]] = []
    start = 0
    abbreviations = {"г", "гг", "ул", "д", "стр", "рис", "им", "руб", "коп", "т", "е", "п", "др", "см", "млн", "млрд", "тыс"}
    for endmark in re.finditer(r"[.!?]+(?=\s|$)|\n[ \t]*\n", text):
        end = endmark.end()
        if endmark[0] == ".":
            previous = re.search(r"([\w]+)$", text[max(start, endmark.start()-64):endmark.start()])
            token = previous[1] if previous else ""
            prefix = text[start:endmark.start()].strip() if endmark.start()-start < 24 else ""
            if token.casefold() in abbreviations or (len(token) == 1 and token.isupper()) or prefix.isdigit():
                continue
        if text[start:end].strip():
            result.append((start, end))
        start = end
    if text[start:].strip():
        result.append((start, len(text)))
    return result


RULES = [
    ("RU01_FRAME", r"\b(?:важно отметить(?:,? что)?|стоит отметить(?:,? что)?|в современном мире|ни для кого не секрет|давайте погрузимся)\b", "Проверьте, добавляет ли вступление информацию; не удаляйте значимый контекст."),
    ("RU02_NOMINAL", r"\b(?:осуществля(?:ет|ют|ется|ются|л|ла|ли)|производ(?:ит|ят|ится|ятся|ил|ила|или))\s+(?:проверку|отправку|оплату|обработку)\b", "Возможно, действие можно выразить глаголом, сохранив субъект и вид."),
    ("RU03_HYPE", r"\b(?:революционн\w* решени\w*|уникальн\w* возможност\w*|беспрецедентн\w* эффективност\w*)\b", "Отделите пустой эпитет от фактического обещания. Не подставляйте выдуманные преимущества."),
    ("RU04_HEDGE", r"\b(?:может\s+потенциально|могут\s+потенциально|возможно[, ]+вероятно|потенциально\s+может)\b", "Проверьте повтор оговорок. Реальную неопределённость сохраните."),
    ("RU14_WRAPPER", r"\b(?:надеюсь,?\s+(?:это|эта информация)\s+(?:поможет|было полезно)|конечно!\s+вот)\b", "Возможно, это ненужная обёртка ответа. Не удаляйте намеренную вежливость в письме."),
    ("RU01_META", r"\bв данной статье\s+(?:мы\s+)?(?:рассмотрим|рассказываем)\b", "Проверьте, нужна ли читателю информация о структуре документа."),
    ("RU03_VAGUE", r"\b(?:выйти на новый уровень|вывести\s+\w+\s+на новый уровень|открывает новые горизонты)\b", "Уберите пустую формулу, не выдумывая конкретику вместо неё."),
    ("EN01_FRAME", r"\b(?:in today'?s fast-paced world|let'?s delve into|it is important to note that)\b", "Проверьте пустое английское вступление; это не признак авторства."),
]


def lint_text(text: str, mode: str = "humanize", max_words: int | None = None, protect: Iterable[str] = ()) -> dict:
    if mode not in LIMITS:
        raise ValueError(f"Неизвестный режим: {mode}")
    threshold = LIMITS[mode] if max_words is None else max_words
    if threshold < 1:
        raise ValueError("Порог длины должен быть положительным")
    spans = protected_spans(text, protect)
    masked = mask_spans(text, spans)
    findings: list[Finding] = []

    def record(code: str, start: int, end: int, message: str) -> None:
        line, col = line_column(text, start)
        findings.append(Finding(code, "review", line, col, text[start:end][:160], message))

    for code, pattern, message in RULES:
        for match in re.finditer(pattern, masked, re.I):
            record(code, match.start(), match.end(), message)
    sentences = sentence_spans(masked)
    for start, end in sentences:
        words = re.findall(r"\b[\w]+(?:[-’'][\w]+)*\b", masked[start:end])
        if len(words) > threshold:
            first = start + len(masked[start:end]) - len(masked[start:end].lstrip())
            record("RU10_LONG", first, end, f"Около {len(words)} слов, ориентир {threshold}. Разделяйте только без потери связей.")
    openings: list[tuple[str, int, int]] = []
    for start, end in sentences:
        words = re.findall(r"\b[^\W\d_]+\b", masked[start:end].casefold())
        openings.append((" ".join(words[:2]) if len(words) >= 2 else "", start, end))
    for index in range(2, len(openings)):
        key, start, end = openings[index]
        if key and key == openings[index-1][0] == openings[index-2][0] and (index == 2 or key != openings[index-3][0]):
            record("RU16_REPEAT", start, end, "Три предложения начинаются одинаково. Сохраните намеренную анафору и параллельные шаги.")
    findings.sort(key=lambda item: (item.line, item.column, item.code))
    return {
        "tool": "textrefine-ru", "version": VERSION, "operation": "lint", "mode": mode,
        "status": "review_suggested" if findings else "no_heuristic_findings",
        "findings": [asdict(item) for item in findings],
        "protected_regions": len(spans),
        "limitations": "Эвристика не определяет авторство, грамотность целиком или сохранение смысла. Ноль сигналов не является гарантией качества.",
    }


NUMBER = re.compile(r"(?<!\w)[+−-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)*(?:[eE][+-]?\d+)?\+?(?!\w)")
UNIT = re.compile(r"[ \t\u00a0\u202f]*(%|₽|\$|€|£|°\s?[CcСсFf]|(?:млн|млрд|тыс\.?)(?:\s+(?:₽|\$|€|руб(?:лей|ля|ль|\.)?|доллар(?:ов|а)?|евро))?|руб(?:лей|ля|ль|\.)?|доллар(?:ов|а)?|евро|кг|мг|г|км|мм|см|м|мс|секунд(?:а|ы|у)?|минут(?:а|ы|у)?|час(?:ов|а)?|дн(?:я|ей)|день|недел(?:я|и|ь|ю)|месяц(?:ев|а)?|лет|год(?:а|ов)?|[КМГТ]Б|KB|MB|GB|TB|с|ч)(?!\w)", re.I)
CONTROL = {
    "SEM_NEGATION": r"\b(?:не|ни|нет|нельзя|никогда|без)\b",
    "SEM_MODALITY": r"\b(?:может|могут|возможно|вероятно|обычно|иногда|примерно|обязан|должен|должны|рекомендуется|гарантированно)\b",
    "SEM_SCOPE": r"\b(?:если|кроме|только|пока|до|после|не менее|не более|при условии)\b",
    "SEM_CAUSALITY": r"\b(?:из-за|поэтому|вследствие|благодаря|потому что|так как)\b",
}


def anchors(text: str, protect: Iterable[str] = ()) -> dict[str, Counter]:
    spans = protected_spans(text, protect)
    visible = mask_spans(text, spans)
    numbers: Counter = Counter()
    quantities: Counter = Counter()
    for match in NUMBER.finditer(visible):
        value = normal_space(match[0])
        numbers[value] += 1
        unit = UNIT.match(visible, match.end())
        prefix = re.search(r"([$€₽£])[ \t]*$", visible[max(0, match.start()-5):match.start()])
        if unit:
            quantities[(value, normal_space(unit[1]))] += 1
        if prefix:
            quantities[(value, prefix[1])] += 1
    return {
        "protected": Counter((span.kind, span.value) for span in spans),
        "numbers": numbers,
        "quantities": quantities,
        **{code: Counter(normal_space(m[0]).casefold() for m in re.finditer(pattern, visible, re.I)) for code, pattern in CONTROL.items()},
    }


def differences(before: Counter, after: Counter) -> dict:
    def serial(counter: Counter) -> list[dict]:
        return [{"value": list(key) if isinstance(key, tuple) else key, "count": count}
                for key, count in sorted(counter.items(), key=lambda item: str(item[0]))]
    return {"removed": serial(before-after), "added": serial(after-before)}


def compare_texts(before: str, after: str, protect: Iterable[str] = ()) -> dict:
    # Materialize once because callers may pass a generator.
    protection = tuple(protect)
    left, right = anchors(before, protection), anchors(after, protection)
    findings: list[dict] = []
    rules = [
        ("protected", "FID_PROTECTED", "error", "Изменились защищённые фрагменты; нужна проверка буквального совпадения."),
        ("numbers", "FID_NUMBER", "error", "Изменился набор числовых записей. Сверьте значение и принадлежность чисел."),
        ("quantities", "FID_UNIT", "error", "Изменились распознанные связки чисел и единиц. Извлечение эвристическое."),
        *[(code, code, "review", "Изменились сигнальные слова. Проверьте смысл вручную: это может быть корректный синоним.") for code in CONTROL],
    ]
    for key, code, severity, message in rules:
        if left[key] != right[key]:
            findings.append({"code": code, "severity": severity, "message": message, **differences(left[key], right[key])})
    return {
        "tool": "textrefine-ru", "version": VERSION, "operation": "compare",
        "status": "manual_review_required", "findings": findings,
        "hard_differences": sum(item["severity"] == "error" for item in findings),
        "limitations": "Сверьте каждое утверждение в обе стороны. Совпадение чисел не проверяет их связь с объектами; имена, числа словами и семантика полностью не распознаются.",
    }


def read_text(path: str) -> str:
    if path == "-":
        # Redirected stdin follows the same UTF-8 contract as files. On Windows
        # the locale's text decoder can silently corrupt a UTF-8 pipe.
        if not sys.stdin.isatty() and hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError(f"Ввод больше {MAX_BYTES} байт")
            return raw.decode("utf-8-sig")
        data = sys.stdin.read(MAX_BYTES + 1)
        if len(data.encode("utf-8")) > MAX_BYTES:
            raise ValueError(f"Ввод больше {MAX_BYTES} байт")
        return data
    source = Path(path)
    with source.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"Файл больше {MAX_BYTES} байт: {path}")
    # UTF-8 BOM is transport metadata, not source prose.
    return raw.decode("utf-8-sig")


def exit_code(report: dict, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    levels = {"error"} if fail_on == "error" else {"error", "review"}
    return int(any(item["severity"] in levels for item in report["findings"]))


def render_text(report: dict) -> str:
    lines = [f"{report['operation']}: {report['status']}"]
    for item in report["findings"]:
        location = f" {item['line']}:{item['column']}" if "line" in item else ""
        lines.append(f"[{item['severity']}] {item['code']}{location}: {item['message']}")
        if "excerpt" in item:
            lines.append("  " + item["excerpt"].replace("\n", " "))
        else:
            lines.append("  " + json.dumps({k: item[k] for k in ("removed", "added")}, ensure_ascii=False))
    if not report["findings"]:
        lines.append("Формальных сигналов не найдено.")
    lines.append(report["limitations"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint", help="Поиск редакторских рисков")
    lint.add_argument("path", nargs="?", default="-")
    lint.add_argument("--mode", choices=tuple(LIMITS), default="humanize")
    lint.add_argument("--max-words", type=int)
    compare = sub.add_parser("compare", help="Сравнение защищённых фрагментов и сигнальных слов")
    compare.add_argument("before")
    compare.add_argument("after")
    for item in (lint, compare):
        item.add_argument("--format", choices=("text", "json"), default="text")
        item.add_argument("--protect", action="append", default=[], help="Буквально сохраняемый фрагмент; можно повторять")
        item.add_argument("--fail-on", choices=("none", "error", "review"), default="error")
    args = parser.parse_args(argv)
    try:
        if args.command == "lint":
            report = lint_text(read_text(args.path), args.mode, args.max_words, args.protect)
        else:
            if args.before == args.after == "-":
                raise ValueError("Нельзя читать оба сравниваемых текста из одного stdin")
            report = compare_texts(read_text(args.before), read_text(args.after), args.protect)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Ошибка ввода: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_text(report))
    return exit_code(report, args.fail_on)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
