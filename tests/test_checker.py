"""Regression tests for diagnostics, protection, CLI and known blind spots."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts.humanizer_check import (anchors, compare_texts, exit_code, lint_text,
    mask_spans, protected_spans, read_text, sentence_spans)
from scripts.run_evals import assess_case, load_cases, load_outputs

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/humanizer_check.py'

def codes(report):
    return {item['code'] for item in report['findings']}


class LintTests(unittest.TestCase):
    def test_intro(self):
        self.assertIn('RU01_FRAME', codes(lint_text('Важно отметить, что бот готов.')))
    def test_nominal(self):
        self.assertIn('RU02_NOMINAL', codes(lint_text('Сервис осуществляет отправку отчёта.')))
    def test_modality_stack(self):
        self.assertIn('RU04_HEDGE', codes(lint_text('Это может потенциально помочь.')))
    def test_noop_natural_dash(self):
        self.assertEqual([], lint_text('Главная задача — сохранить данные.')['findings'])
    def test_noop_slang(self):
        self.assertEqual([], lint_text('Ну всё, бот заработал 😅')['findings'])
    def test_real_triad(self):
        self.assertEqual([], lint_text('Бот читает заявки, пишет ответы и сохраняет отчёты.')['findings'])
    def test_polite_email(self):
        self.assertEqual([], lint_text('Здравствуйте, Анна! Отправляю файл. С уважением, Игорь.')['findings'])
    def test_contemporary_not_cliche(self):
        self.assertEqual([], lint_text('В современном искусстве есть разные направления.')['findings'])
    def test_quotes_not_linted(self):
        self.assertEqual([], lint_text('Он сказал: «Важно отметить, что это факт».')['findings'])
    def test_straight_quotes(self):
        self.assertEqual([], lint_text('Он написал: "Важно отметить, что ответ готов".')['findings'])
    def test_fence_not_linted(self):
        self.assertEqual([], lint_text('```text\nВажно отметить\n```\n')['findings'])
    def test_tilde_fence(self):
        self.assertEqual([], lint_text('~~~text\nВажно отметить\n~~~\n')['findings'])
    def test_long_fence_with_inner_ticks(self):
        text='````md\n```\nВажно отметить\n```\n````\n'
        self.assertEqual([], lint_text(text)['findings'])
    def test_unclosed_fence_is_protected(self):
        self.assertEqual([], lint_text('```\nВажно отметить')['findings'])
    def test_inline_code(self):
        self.assertEqual([], lint_text('Введите `важно отметить`.')['findings'])
    def test_multitick_inline_code(self):
        self.assertEqual([], lint_text('Пример: ``важно отметить `x` ``.')['findings'])
    def test_frontmatter(self):
        self.assertEqual([], lint_text('---\ntitle: важно отметить\n---\nТекст.')['findings'])
    def test_blockquote(self):
        self.assertEqual([], lint_text('> Важно отметить, что это цитата.\n')['findings'])
    def test_indented_code(self):
        self.assertEqual([], lint_text('    Важно отметить\n')['findings'])
    def test_html_attributes_not_linted(self):
        self.assertEqual([], lint_text('<div title="важно отметить">Текст</div>')['findings'])
    def test_html_body_linted(self):
        self.assertIn('RU01_FRAME', codes(lint_text('<p>Важно отметить, что ответ готов.</p>')))
    def test_html_comment(self):
        self.assertEqual([], lint_text('<!-- Важно отметить -->')['findings'])
    def test_placeholder(self):
        self.assertEqual([], lint_text('{{важно отметить}}')['findings'])
    def test_link_destination(self):
        self.assertEqual([], lint_text('[текст](https://example.org/ "важно отметить")')['findings'])
    def test_link_label_linted(self):
        self.assertIn('RU01_FRAME', codes(lint_text('[Важно отметить](https://example.org/)')))
    def test_line_and_column(self):
        result=lint_text('Текст.\n\n  Важно отметить, что бот готов.')['findings'][0]
        self.assertEqual((3,3),(result['line'],result['column']))
    def test_mask_keeps_offsets(self):
        text='Ввод\n```\nx\n```\nВывод'
        masked=mask_spans(text,protected_spans(text))
        self.assertEqual(len(text),len(masked))
        self.assertEqual(text.count('\n'),masked.count('\n'))
    def test_long_sentence(self):
        self.assertIn('RU10_LONG',codes(lint_text(('слово '*33)+'.')))
    def test_light_threshold(self):
        self.assertNotIn('RU10_LONG',codes(lint_text(('слово '*33)+'.',mode='light')))
    def test_strict_threshold(self):
        self.assertIn('RU10_LONG',codes(lint_text(('слово '*25)+'.',mode='strict')))
    def test_custom_threshold(self):
        self.assertIn('RU10_LONG',codes(lint_text('Три простых слова.',max_words=2)))
    def test_invalid_threshold(self):
        with self.assertRaises(ValueError): lint_text('Текст',max_words=0)
    def test_invalid_mode(self):
        with self.assertRaises(ValueError): lint_text('Текст',mode='fake')
    def test_repeated_opening(self):
        self.assertIn('RU16_REPEAT',codes(lint_text('Этот бот читает. Этот бот пишет. Этот бот отправляет.')))
    def test_decimal_is_not_sentence_break(self):
        self.assertEqual(1,len(sentence_spans('Цена 1.5 руб. за штуку.')))
    def test_abbreviation(self):
        self.assertEqual(1,len(sentence_spans('Адрес: г. Пермь.')))
    def test_empty_lint(self):
        self.assertEqual([],lint_text('')['findings'])
    def test_large_quote_corpus(self):
        self.assertEqual([],lint_text(('«Важно отметить»\n'*20000))['findings'])


class FidelityTests(unittest.TestCase):
    def test_number(self):
        self.assertIn('FID_NUMBER',codes(compare_texts('Цена 100 ₽.','Цена 200 ₽.')))
    def test_number_multiplicity(self):
        self.assertIn('FID_NUMBER',codes(compare_texts('10 и 10.','10.')))
    def test_negative_sign(self):
        self.assertIn('FID_NUMBER',codes(compare_texts('Температура −5 °C.','Температура 5 °C.')))
    def test_currency(self):
        self.assertIn('FID_UNIT',codes(compare_texts('100 ₽','100 $')))
    def test_prefix_currency(self):
        self.assertIn('FID_UNIT',codes(compare_texts('$100','€100')))
    def test_unit(self):
        self.assertIn('FID_UNIT',codes(compare_texts('5 кг','5 г')))
    def test_percent(self):
        self.assertIn('FID_UNIT',codes(compare_texts('Рост 30%.','Рост 30.')))
    def test_nonbreaking_spaces(self):
        self.assertNotIn('FID_NUMBER',codes(compare_texts('Цена 1\u00a0500 ₽.','Цена 1 500 ₽.')))
    def test_decimal_comma(self):
        self.assertIn('1,5',anchors('Объём 1,5 л')['numbers'])
    def test_million_quantity(self):
        self.assertIn(('1,5','млн долларов'),anchors('Цена 1,5 млн долларов')['quantities'])
    def test_scientific_number(self):
        self.assertIn('1e3',anchors('Порог 1e3')['numbers'])
    def test_time_change(self):
        self.assertIn('FID_NUMBER',codes(compare_texts('Встреча 14:30.','Встреча 14:35.')))
    def test_negation(self):
        self.assertIn('SEM_NEGATION',codes(compare_texts('Это не работает.','Это работает.')))
    def test_modality(self):
        self.assertIn('SEM_MODALITY',codes(compare_texts('Может помочь.','Поможет.')))
    def test_scope(self):
        self.assertIn('SEM_SCOPE',codes(compare_texts('До 30%.','30%.')))
    def test_causality(self):
        self.assertIn('SEM_CAUSALITY',codes(compare_texts('После запуска рост.','Благодаря запуску рост.')))
    def test_code_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('Вызовите `git status`.','Вызовите `git diff`.')))
    def test_placeholder_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('Привет, {name}!','Привет, {user}!')))
    def test_printf_placeholder(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('Всего %1$s','Всего %s')))
    def test_quote_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('«Открыть»','«Запустить»')))
    def test_url_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('https://example.org/a','https://example.org/b')))
    def test_url_trailing_punctuation(self):
        self.assertEqual([],compare_texts('Сайт: https://example.org.','https://example.org')['findings'])
    def test_email_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('help@example.org','support@example.org')))
    def test_reference_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('[a]: https://example.org/a','[a]: https://example.org/b')))
    def test_html_attr_changed(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('<div id="a">текст</div>','<div id="b">текст</div>')))
    def test_explicit_protection(self):
        self.assertIn('FID_PROTECTED',codes(compare_texts('Наш термин: деплой.','Наш термин: запуск.',protect=['деплой'])))
    def test_empty_protection(self):
        with self.assertRaises(ValueError): protected_spans('Текст',[''])
    def test_good_rewrite(self):
        self.assertEqual([],compare_texts('Важно отметить, что бот отправляет 5 отчётов.','Бот отправляет 5 отчётов.')['findings'])
    def test_blind_spot_reassigned_numbers(self):
        self.assertEqual([],compare_texts('А стоит 10 ₽, Б стоит 20 ₽.','А стоит 20 ₽, Б стоит 10 ₽.')['findings'])
    def test_blind_spot_actor(self):
        self.assertEqual([],compare_texts('Анна отправила письмо Ивану.','Иван отправил письмо Анне.')['findings'])
    def test_blind_spot_spelled_number(self):
        self.assertEqual([],compare_texts('Ответ через два дня.','Ответ через три дня.')['findings'])
    def test_never_certifies_semantics(self):
        self.assertEqual('manual_review_required',compare_texts('Текст','Текст')['status'])
    def test_default_review_does_not_fail(self):
        self.assertEqual(0,exit_code(lint_text('Важно отметить'), 'error'))
    def test_review_fail_option(self):
        self.assertEqual(1,exit_code(lint_text('Важно отметить'), 'review'))
    def test_errors_fail(self):
        self.assertEqual(1,exit_code(compare_texts('1','2'), 'error'))


class CliTests(unittest.TestCase):
    def call(self,*args,input=None):
        return subprocess.run([sys.executable,str(CLI),*args],input=input,capture_output=True,text=True,encoding='utf-8',cwd=ROOT)
    def test_stdin_json(self):
        result=self.call('lint','-','--format','json',input='Важно отметить, что всё готово.')
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertIn('RU01_FRAME',codes(json.loads(result.stdout)))
    def test_utf8_pipe_ignores_windows_locale(self):
        import os
        env = dict(os.environ, PYTHONIOENCODING='cp1251', PYTHONUTF8='0')
        result = subprocess.run([sys.executable, str(CLI), 'lint', '-', '--format', 'json'],
            input='Важно отметить, что всё готово.'.encode('utf-8'),
            capture_output=True, cwd=ROOT, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('RU01_FRAME', codes(json.loads(result.stdout.decode('utf-8'))))
    def test_stdin_utf8_bom(self):
        result = subprocess.run([sys.executable, str(CLI), 'lint', '-', '--format', 'json'],
            input='Важно отметить, что всё готово.'.encode('utf-8-sig'),
            capture_output=True, cwd=ROOT)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('RU01_FRAME', codes(json.loads(result.stdout.decode('utf-8'))))
    def test_invalid_utf8_stdin(self):
        result = subprocess.run([sys.executable, str(CLI), 'lint', '-'],
            input=b'\xff\xfe', capture_output=True, cwd=ROOT)
        self.assertEqual(2, result.returncode)
    def test_unchanged_utf8_pipe(self):
        result = self.call('lint', '-', '--format', 'json', input='Главная задача — сохранить данные.')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], json.loads(result.stdout)['findings'])
    def test_missing_file(self):
        self.assertEqual(2,self.call('lint','/nonexistent/file.txt').returncode)
    def test_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bad.txt';path.write_bytes(b'\xff\xff')
            self.assertEqual(2,self.call('lint',str(path)).returncode)
    def test_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'good.txt';path.write_text('Текст',encoding='utf-8-sig')
            self.assertEqual('Текст',read_text(str(path)))
    def test_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'input.md';path.write_text('Важно отметить',encoding='utf-8');before=path.read_bytes()
            self.call('lint',str(path))
            self.assertEqual(before,path.read_bytes())
    def test_comparison_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            a,b=Path(temp)/'a',Path(temp)/'b';a.write_text('1',encoding='utf-8');b.write_text('2',encoding='utf-8')
            self.assertEqual(1,self.call('compare',str(a),str(b)).returncode)
    def test_double_stdin_rejected(self):
        self.assertEqual(2,self.call('compare','-','-',input='Текст').returncode)
    def test_large_input_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'large';path.write_bytes(b'a'*2_000_001)
            self.assertEqual(2,self.call('lint',str(path)).returncode)
    def test_zero_threshold_rejected(self):
        self.assertEqual(2,self.call('lint','-','--max-words','0',input='Текст').returncode)
    def test_incomplete_candidates_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'outputs.json';path.write_text('{"outputs": {}}',encoding='utf-8')
            with self.assertRaises(ValueError):load_outputs(path,load_cases())


class EditorialFixtures(unittest.TestCase):
    pass


def make_fixture(case):
    def test(self):
        result=assess_case(case,case['reference'])
        self.assertTrue(result['automatic_checks_passed'], result)
        if 'bad_variant' in case:
            report=compare_texts(case['source'],case['bad_variant'])
            if case.get('manual_only_negative'):
                self.assertEqual([],report['findings'],'Known blind spot changed: update documentation and expectations')
            else:
                self.assertTrue(set(case['expected_bad_codes']).issubset(codes(report)),report)
    return test


for fixture in load_cases():
    setattr(EditorialFixtures,'test_'+fixture['id'].replace('-','_'),make_fixture(fixture))

if __name__=='__main__':
    unittest.main()
