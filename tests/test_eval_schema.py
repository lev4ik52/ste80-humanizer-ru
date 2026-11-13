"""Validate evaluation data before accepting a fixture or candidate run."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.run_evals import load_cases, load_triggers
from scripts import validate_repo as val


class EvalSchemaTests(unittest.TestCase):
    def check_payload(self, payload, loader):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cases.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            return loader(path)

    def test_current_cases_and_triggers(self):
        self.assertEqual(50, len(load_cases()))
        self.assertEqual(12, len(load_triggers()))

    def test_invalid_case_fields(self):
        changes = [
            ('mode', 'unknown'), ('genre', 'unknown'), ('source', ' '),
            ('manual_criteria', []), ('must_include', ['']),
            ('bad_variant', 42), ('expected_bad_codes', ['TYPO']),
            ('manual_only_negative', 'false'), ('expected_bad_codes', []),
            ('manual_only_negative', True),
        ]
        for key, value in changes:
            with self.subTest(field=key, value=value):
                case = copy.deepcopy(load_cases()[0])
                case[key] = value
                with self.assertRaises(ValueError):
                    self.check_payload([case], load_cases)

    def test_incomplete_negative_example(self):
        case = copy.deepcopy(load_cases()[0])
        del case['expected_bad_codes']
        with self.assertRaises(ValueError):
            self.check_payload([case], load_cases)

    def test_custom_valid_case(self):
        case = copy.deepcopy(load_cases()[0])
        case.update(id='custom-case', genre='auto', mode='light')
        self.assertEqual([case], self.check_payload([case], load_cases))

    def test_invalid_triggers(self):
        for payload in ([], {}, [None], [{'prompt': ' ', 'activate': True}],
                [{'prompt': 'Текст', 'activate': 1}],
                [{'prompt': 'Текст', 'activate': 'false'}],
                [{'prompt': 'Текст', 'activate': True}] * 2):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.check_payload(payload, load_triggers)

    def test_false_trigger_is_valid(self):
        payload = [{'prompt': 'Посчитай 2 + 2', 'activate': False}]
        self.assertEqual(payload, self.check_payload(payload, load_triggers))

    def test_repository_validation_checks_schema(self):
        from unittest.mock import patch
        with patch('scripts.validate_repo.load_triggers', side_effect=ValueError('Invalid trigger schema')):
            self.assertTrue(any('Invalid trigger schema' in error for error in val.validate()))


if __name__ == '__main__':
    unittest.main()
