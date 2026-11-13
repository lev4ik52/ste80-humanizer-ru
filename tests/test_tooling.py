"""Packaging, validation and mocked publication; no real GitHub writes."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts import publish as pub
from scripts import validate_repo as val

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'ste80-humanizer-ru'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('__pycache__', '.git'))
        val.refresh_manifest(self.root)

    def test_valid_repository(self):
        self.assertEqual([],val.validate(self.root))

    def test_manifest_contains_hashes(self):
        data=val.load_manifest(self.root)
        expected=hashlib.sha256((self.root/'SKILL.md').read_bytes()).hexdigest()
        self.assertEqual(expected,data['files']['SKILL.md'])
        self.assertNotIn(val.MANIFEST,data['files'])

    def test_changed_file_rejected(self):
        with (self.root/'README.md').open('a',encoding='utf-8',newline='\n') as stream:
            stream.write('Изменение.\n')
        self.assertTrue(any('Manifest' in err for err in val.validate(self.root)))

    def test_explicit_refresh(self):
        with (self.root/'README.md').open('a',encoding='utf-8',newline='\n') as stream:
            stream.write('Изменение.\n')
        val.refresh_manifest(self.root)
        self.assertEqual([],val.validate(self.root))

    def test_unlisted_private_files_ignored(self):
        (self.root/'.env').write_text('PRIVATE_DATA=example\n',encoding='utf-8')
        (self.root/'personal.txt').write_text('Не публиковать\n',encoding='utf-8')
        self.assertEqual([],val.validate(self.root))
        self.assertNotIn('.env',val.file_set(self.root))
        self.assertNotIn('personal.txt',val.file_set(self.root))

    def test_missing_support_file_detected_after_refresh(self):
        (self.root/'tests/test_checker.py').unlink()
        val.refresh_manifest(self.root)
        self.assertTrue(any('test_checker.py' in err for err in val.validate(self.root)))

    def test_missing_changelog_reports_without_crashing(self):
        (self.root/'CHANGELOG.md').unlink()
        self.assertTrue(any('CHANGELOG' in err for err in val.validate(self.root)))

    def test_broken_local_link(self):
        with (self.root/'README.md').open('a',encoding='utf-8',newline='\n') as stream:
            stream.write('[Путь](nonexistent.md)\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('Битая локальная ссылка' in err for err in val.validate(self.root)))

    def test_invalid_frontmatter(self):
        p=self.root/'SKILL.md'
        p.write_text(p.read_text(encoding='utf-8').replace('name: ste80-humanizer-ru','name: wrong'),encoding='utf-8',newline='\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('имя скилла' in err for err in val.validate(self.root)))

    def test_broken_python(self):
        (self.root/'scripts/invalid.py').write_text('def broken(:\n',encoding='utf-8',newline='\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('invalid.py' in err for err in val.validate(self.root)))

    def test_invalid_json(self):
        (self.root/'evals/triggers.json').write_text('{\n',encoding='utf-8',newline='\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('triggers.json' in err for err in val.validate(self.root)))

    def test_secret_like_content(self):
        token='gh'+'p_'+'X'*36
        with (self.root/'README.md').open('a',encoding='utf-8',newline='\n') as stream:
            stream.write(token+'\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('Возможный секрет' in err for err in val.validate(self.root)))

    def test_missing_final_newline(self):
        (self.root/'README.en.md').write_text('text',encoding='utf-8')
        val.refresh_manifest(self.root)
        self.assertTrue(any('финального перевода' in err for err in val.validate(self.root)))

    def test_crlf_is_rejected(self):
        (self.root/'README.en.md').write_bytes(b'text\r\n')
        val.refresh_manifest(self.root)
        self.assertTrue(any('LF-' in err for err in val.validate(self.root)))

    def test_unsafe_paths(self):
        for name in ('../other','/tmp/file','C:/file','foo\\bar','.','./README.md','a/../README.md','a//b',''):
            with self.subTest(path=name),self.assertRaises(ValueError):
                val.safe_path(self.root,name)

    def test_good_path(self):
        self.assertEqual((self.root/'scripts/publish.py').resolve(),val.safe_path(self.root,'scripts/publish.py'))

    def test_safe_path_with_relative_root(self):
        relative=Path(os.path.relpath(self.root,Path.cwd()))
        self.assertEqual((self.root/'README.md').resolve(),val.safe_path(relative,'README.md'))

    def test_symlink_disallowed(self):
        p=self.root/'README.en.md'
        p.unlink()
        try:
            p.symlink_to(self.root/'README.md')
        except OSError:
            self.skipTest('Symlink privilege not available')
        with self.assertRaises(ValueError): val.safe_path(self.root,'README.en.md')

    def test_manifest_path_traversal(self):
        data=val.load_manifest(self.root)
        data['files']['../outside']='a'*64
        (self.root/val.MANIFEST).write_text(json.dumps(data)+'\n',encoding='utf-8',newline='\n')
        with self.assertRaises(ValueError): val.load_manifest(self.root)

    def test_manifest_bad_digest(self):
        data=val.load_manifest(self.root)
        data['files']['README.md']='not-a-hash'
        (self.root/val.MANIFEST).write_text(json.dumps(data)+'\n',encoding='utf-8',newline='\n')
        with self.assertRaises(ValueError): val.load_manifest(self.root)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'source'
        self.root.mkdir()
        (self.root/'README.md').write_text('Only this file\n',encoding='utf-8',newline='\n')
        (self.root/val.MANIFEST).write_text('{}\n',encoding='utf-8',newline='\n')
        (self.root/'private.txt').write_text('Do not publish\n',encoding='utf-8',newline='\n')
        self.stagebase=Path(self.tmp.name)/'stage'
        self.stagebase.mkdir()
        self.calls=[]
        for target,value in (
            ('validate',[]),('load_manifest',{'files':{'README.md':'x'}}),
            ('shutil.which','/mock/tool'),('tempfile.mkdtemp',str(self.stagebase))):
            patcher=patch('scripts.publish.'+target,return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_run(self,args,cwd=None,check=True):
        self.calls.append((args,cwd,check))
        stdout=''
        status=0
        if args[:3]==['gh','api','user']:
            stdout=json.dumps({'login':'lev4ik52','id':282543186})
        elif args[:3]==['gh','repo','view'] and args[-1]=='name':
            status=1
        elif args[:3]==['gh','repo','view']:
            stdout=json.dumps({'url':'https://github.com/lev4ik52/ste80-humanizer-ru','isPrivate':False})
        elif args[:3]==['git','rev-parse','HEAD'] or args[:2]==['gh','api']:
            stdout=SHA+'\n'
        return subprocess.CompletedProcess(args,status,stdout,'')

    def execute(self):
        return pub.publish('lev4ik52','ste80-humanizer-ru',True,self.root)

    def test_dry_run_has_no_subprocess(self):
        with patch('scripts.publish.run') as runner:
            result=pub.publish('lev4ik52','ste80-humanizer-ru',False,self.root)
        runner.assert_not_called()
        self.assertFalse(result['executed'])
        self.assertEqual('public',result['visibility'])

    def test_wrong_names_rejected_before_execution(self):
        for owner,name in (('../x','ok'),('lev4ik52','../other'),('','ok'),('name;rm','ok')):
            with self.subTest(owner=owner,name=name),self.assertRaises(pub.PublishError):
                pub.publish(owner,name,True,self.root)

    def test_validation_errors_block_publication(self):
        with patch('scripts.publish.validate',return_value=['bad']),patch('scripts.publish.run') as runner:
            with self.assertRaises(pub.PublishError): self.execute()
        runner.assert_not_called()

    def test_missing_tools(self):
        with patch('scripts.publish.shutil.which',return_value=None),patch('scripts.publish.run') as runner:
            with self.assertRaises(pub.PublishError): self.execute()
        runner.assert_not_called()

    def test_wrong_account(self):
        with patch('scripts.publish.run',return_value=subprocess.CompletedProcess([],0,'{"login":"other","id":123}','')):
            with self.assertRaisesRegex(pub.PublishError,'Активен аккаунт'): self.execute()

    def test_invalid_account_id(self):
        with patch('scripts.publish.run',return_value=subprocess.CompletedProcess([],0,'{"login":"lev4ik52","id":true}','')):
            with self.assertRaisesRegex(pub.PublishError,'корректный публичный ID'): self.execute()

    def test_existing_repo_aborts(self):
        def existing(args,cwd=None,check=True):
            if args[:3]==['gh','repo','view']:
                return subprocess.CompletedProcess(args,0,'{"name":"ste80-humanizer-ru"}','')
            return self.fake_run(args,cwd,check)
        with patch('scripts.publish.run',side_effect=existing):
            with self.assertRaisesRegex(pub.PublishError,'уже существует'): self.execute()
        self.assertFalse((self.stagebase/'ste80-humanizer-ru').exists())

    def test_success_verifies_public_and_sha(self):
        with patch('scripts.publish.run',side_effect=self.fake_run): result=self.execute()
        self.assertTrue(result['executed'])
        self.assertTrue(result['verified_public'])
        self.assertEqual(SHA,result['commit'])
        self.assertEqual('not_checked',result['ci_status'])

    def test_only_manifest_files_copied(self):
        with patch('scripts.publish.run',side_effect=self.fake_run): result=self.execute()
        staged=Path(result['local_checkout'])
        self.assertTrue((staged/'README.md').is_file())
        self.assertFalse((staged/'private.txt').exists())
        self.assertTrue((self.root/'private.txt').exists())
        self.assertFalse((self.root/'.git').exists())

    def test_no_force_or_delete_commands(self):
        with patch('scripts.publish.run',side_effect=self.fake_run): self.execute()
        for args,_,_ in self.calls:
            self.assertNotIn('--force',args)
            self.assertNotIn('delete',args)
            self.assertNotIn('token',args)
        creation=next(args for args,_,_ in self.calls if args[:3]==['gh','repo','create'])
        self.assertIn('--public',creation)

    def test_checks_precede_remote_write(self):
        with patch('scripts.publish.run',side_effect=self.fake_run): self.execute()
        args=[item[0] for item in self.calls]
        created=next(i for i,cmd in enumerate(args) if cmd[:3]==['gh','repo','create'])
        self.assertTrue(any('scripts/validate_repo.py' in cmd for cmd in args[:created]))
        self.assertTrue(any('unittest' in cmd for cmd in args[:created]))
        self.assertTrue(any('scripts/run_evals.py' in cmd for cmd in args[:created]))

    def test_noreply_commit_email(self):
        with patch('scripts.publish.run',side_effect=self.fake_run): self.execute()
        email=next(args[-1] for args,_,_ in self.calls if args[:3]==['git','config','user.email'])
        self.assertEqual('282543186+lev4ik52@users.noreply.github.com',email)

    def test_failed_check_does_not_create_remote(self):
        def fail_check(args,cwd=None,check=True):
            if 'scripts/validate_repo.py' in args:
                raise pub.PublishError('local check failed')
            return self.fake_run(args,cwd,check)
        with patch('scripts.publish.run',side_effect=fail_check):
            with self.assertRaisesRegex(pub.PublishError,'Рабочая копия сохранена'): self.execute()
        self.assertFalse(any(args[:3]==['gh','repo','create'] for args,_,_ in self.calls))

    def test_private_remote_is_not_success(self):
        def private(args,cwd=None,check=True):
            if args[-1]=='url,isPrivate':
                return subprocess.CompletedProcess(args,0,'{"isPrivate":true,"url":"example"}','')
            return self.fake_run(args,cwd,check)
        with patch('scripts.publish.run',side_effect=private):
            with self.assertRaisesRegex(pub.PublishError,'публичную видимость'): self.execute()

    def test_sha_mismatch_is_not_success(self):
        def different(args,cwd=None,check=True):
            if args[:2]==['gh','api'] and args[-1]=='.sha':
                return subprocess.CompletedProcess(args,0,'b'*40,'')
            return self.fake_run(args,cwd,check)
        with patch('scripts.publish.run',side_effect=different):
            with self.assertRaisesRegex(pub.PublishError,'коммит отличается'): self.execute()

    def test_cli_default_no_execute(self):
        with patch('scripts.publish.publish',return_value={'executed':False}) as action,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,pub.main([]))
        action.assert_called_once_with('lev4ik52','ste80-humanizer-ru',False)

    def test_cli_failure_exit(self):
        with patch('scripts.publish.publish',side_effect=pub.PublishError('test')),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2,pub.main(['--execute']))

    def test_command_uses_argument_list_and_timeout(self):
        with patch('scripts.publish.subprocess.run',return_value=subprocess.CompletedProcess([],0,'ok','')) as runner:
            pub.run(['git','status'])
        args,kwargs=runner.call_args
        self.assertEqual(['git','status'],args[0])
        self.assertNotIn('shell',kwargs)
        self.assertEqual(180,kwargs['timeout'])
        self.assertEqual('github.com',kwargs['env']['GH_HOST'])

    def test_command_failure_propagates(self):
        with patch('scripts.publish.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','failed')):
            with self.assertRaises(pub.PublishError): pub.run(['git','status'])


if __name__=='__main__':
    unittest.main()
