#!/usr/bin/env python3
"""Check editorial fixtures or supplied model outputs; never invoke a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

try:
    from .humanizer_check import compare_texts, read_text
except ImportError:
    from humanizer_check import compare_texts, read_text

ROOT = Path(__file__).resolve().parents[1]
MODES = {"light", "humanize", "strict", "review"}
GENRES = {"auto", "docs", "web", "social", "email", "business", "ux", "creative"}
BAD_CODES = {"FID_PROTECTED", "FID_NUMBER", "FID_UNIT", "SEM_NEGATION",
             "SEM_MODALITY", "SEM_SCOPE", "SEM_CAUSALITY"}


def load_cases(path: Path = ROOT / "evals/cases.json") -> list[dict]:
    cases = json.loads(read_text(str(path)))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Нужен непустой массив сценариев")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Сценарий должен быть объектом")
        for key in ("id", "source", "reference", "genre", "mode"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"Некорректное поле {key}")
        if case["id"] in ids:
            raise ValueError(f"Повтор ID: {case['id']}")
        ids.add(case["id"])
        if case["mode"] not in MODES or case["genre"] not in GENRES:
            raise ValueError(f"Неизвестный режим или жанр: {case['id']}")
        for key in ("must_include", "must_not_include", "manual_criteria"):
            if not isinstance(case.get(key), list) or not all(isinstance(x, str) and x.strip() for x in case[key]):
                raise ValueError(f"Некорректное поле {key}: {case['id']}")
        if not case["manual_criteria"]:
            raise ValueError(f"Нужны критерии ручной проверки: {case['id']}")
        negative_keys = {"bad_variant", "expected_bad_codes", "manual_only_negative"}
        if negative_keys.intersection(case):
            if (not negative_keys.issubset(case)
                    or not isinstance(case["bad_variant"], str) or not case["bad_variant"].strip()
                    or type(case["manual_only_negative"]) is not bool
                    or not isinstance(case["expected_bad_codes"], list)
                    or not all(isinstance(code, str) and code in BAD_CODES for code in case["expected_bad_codes"])):
                raise ValueError(f"Некорректный отрицательный пример: {case['id']}")
            if bool(case["expected_bad_codes"]) == case["manual_only_negative"]:
                raise ValueError(f"Коды не соответствуют типу отрицательного примера: {case['id']}")
    return cases


def load_triggers(path: Path = ROOT / "evals/triggers.json") -> list[dict]:
    triggers = json.loads(read_text(str(path)))
    if not isinstance(triggers, list) or not triggers:
        raise ValueError("Нужен непустой массив trigger-сценариев")
    prompts: set[str] = set()
    for item in triggers:
        if (not isinstance(item, dict) or not isinstance(item.get("prompt"), str)
                or not item["prompt"].strip() or type(item.get("activate")) is not bool):
            raise ValueError("Некорректный trigger-сценарий")
        if item["prompt"] in prompts:
            raise ValueError("Повтор prompt в trigger-сценариях")
        prompts.add(item["prompt"])
    return triggers


def assess_case(case: dict, output: str) -> dict:
    if not isinstance(output, str) or not output.strip():
        raise ValueError(f"Пустой или нестроковый результат: {case['id']}")
    report = compare_texts(case["source"], output)
    failures = [item["code"] for item in report["findings"] if item["severity"] == "error"]
    failures += [f"missing:{phrase}" for phrase in case["must_include"] if phrase not in output]
    failures += [f"forbidden:{phrase}" for phrase in case["must_not_include"] if phrase.casefold() in output.casefold()]
    return {"id": case["id"], "automatic_checks_passed": not failures, "failures": failures,
            "review_signals": [item["code"] for item in report["findings"] if item["severity"] == "review"],
            "manual_review_required": True}


def load_outputs(path: Path, cases: list[dict]) -> tuple[dict, dict]:
    payload = json.loads(read_text(str(path)))
    if not isinstance(payload, dict) or not isinstance(payload.get("outputs"), dict):
        raise ValueError("Файл должен содержать объект outputs с ключами ru-01 ... ru-50")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata должен быть объектом")
    required = {case["id"] for case in cases}
    received = set(payload["outputs"])
    if required != received:
        raise ValueError(f"Неполный/лишний набор: missing={sorted(required-received)}, extra={sorted(received-required)}")
    return payload["outputs"], metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/cases.json")
    parser.add_argument("--candidates", type=Path, help="Результаты отдельного прогона модели; скрипт модель не запускает")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        outputs, metadata = load_outputs(args.candidates, cases) if args.candidates else ({case["id"]: case["reference"] for case in cases}, {})
        results = [assess_case(case, outputs[case["id"]]) for case in cases]
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        print(f"Ошибка сценариев: {exc}", file=sys.stderr)
        return 2
    passed = sum(result["automatic_checks_passed"] for result in results)
    report = {
        "run_type": "supplied_output_invariants" if args.candidates else "reference_fixture_self_test",
        "model_invoked": False, "metadata": metadata, "total": len(results), "passed": passed,
        "failed": len(results)-passed, "manual_review_required": True, "results": results,
        "limitations": "Проверяются формальные инварианты, не естественность и не преимущество скилла. Для качества нужен отдельный слепой просмотр.",
    }
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['run_type']}: {passed}/{len(results)} automatic checks passed")
        for result in results:
            if result["failures"]:
                print(result["id"] + ": " + ", ".join(result["failures"]))
        print(report["limitations"])
    return int(passed != len(results))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
