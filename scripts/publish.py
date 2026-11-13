#!/usr/bin/env python3
"""Explicit, new-public-repository publication via the user's GitHub CLI login."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

try:
    from .validate_repo import ROOT, MANIFEST, load_manifest, safe_path, validate
except ImportError:
    from validate_repo import ROOT, MANIFEST, load_manifest, safe_path, validate

DESCRIPTION = 'Русскоязычный Agent Skill для ясной редактуры без потери фактов и авторского голоса'


class PublishError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    # Use standard gh authentication; never read or print credential files/tokens.
    env=os.environ.copy()
    env.update(GH_HOST='github.com', GH_PROMPT_DISABLED='1', PYTHONUTF8='1')
    result=subprocess.run(args,cwd=cwd,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=180)
    if check and result.returncode:
        raise PublishError(f"Команда {args[0]} {args[1] if len(args)>1 else ''} завершилась ошибкой:\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def publish(owner: str, name: str, execute: bool, root: Path = ROOT) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',owner):
        raise PublishError('Некорректный GitHub login')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',name):
        raise PublishError('Некорректное имя репозитория')
    errors=validate(root)
    if errors:
        raise PublishError('Проверка пакета не пройдена:\n'+'\n'.join(errors))
    manifest=load_manifest(root)
    files=sorted(manifest['files'])+[MANIFEST]
    repository=f'{owner}/{name}'
    plan={'repository':repository,'visibility':'public','files':len(files),'executed':False,
          'note':'Существующие репозитории не изменяются. Сеть используется только с --execute.'}
    if not execute:
        return plan
    for tool in ('gh','git'):
        if not shutil.which(tool):
            raise PublishError(f'Не найден {tool}. Установите Git и GitHub CLI, затем выполните gh auth login --hostname github.com.')
    identity=json.loads(run(['gh','api','user','--jq','{login: .login, id: .id}']).stdout)
    if identity.get('login','').casefold()!=owner.casefold():
        raise PublishError(f"Активен аккаунт {identity.get('login')}, ожидался {owner}. Переключите аккаунт через gh auth switch.")
    account_id=identity.get('id')
    if not isinstance(account_id,int) or isinstance(account_id,bool) or account_id<=0:
        raise PublishError('GitHub не вернул корректный публичный ID аккаунта')
    exists=run(['gh','repo','view',repository,'--json','name'],check=False)
    if exists.returncode==0:
        raise PublishError(f'{repository} уже существует. Автоматическое изменение или перезапись запрещены; используйте отдельный обычный git workflow.')
    stage=Path(tempfile.mkdtemp(prefix='ste80-ru-publish-'))/'ste80-humanizer-ru'
    stage.mkdir()
    try:
        for relative in files:
            source=safe_path(root,relative)
            target=stage/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        # Revalidate the copied bytes and run offline checks before any remote write.
        run([sys.executable,'scripts/validate_repo.py'],cwd=stage)
        run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=stage)
        run([sys.executable,'scripts/run_evals.py'],cwd=stage)
        run(['git','init','-b','main'],cwd=stage)
        run(['git','config','user.name',owner],cwd=stage)
        run(['git','config','user.email',f'{account_id}+{owner}@users.noreply.github.com'],cwd=stage)
        run(['git','add','--',*files],cwd=stage)
        run(['git','-c','commit.gpgsign=false','commit','-m','feat: release Russian-first humanizer skill 1.0.0'],cwd=stage)
        local_sha=run(['git','rev-parse','HEAD'],cwd=stage).stdout.strip()
        run(['gh','repo','create',repository,'--public','--description',DESCRIPTION,
             '--source',str(stage),'--remote','origin','--push','--disable-wiki'],cwd=stage)
        remote=json.loads(run(['gh','repo','view',repository,'--json','url,isPrivate']).stdout)
        if remote.get('isPrivate') is not False:
            raise PublishError('Не удалось подтвердить публичную видимость')
        remote_sha=run(['gh','api',f'repos/{repository}/commits/main','--jq','.sha']).stdout.strip()
        if remote_sha!=local_sha:
            raise PublishError('Удалённый коммит отличается от локального; нужна ручная проверка')
        return {**plan,'executed':True,'verified_public':True,'commit':remote_sha,
                'url':remote['url'],'local_checkout':str(stage),'ci_status':'not_checked'}
    except (OSError,ValueError,subprocess.SubprocessError,PublishError) as exc:
        raise PublishError(f'{exc}\nРабочая копия сохранена: {stage}\nРепозиторий мог быть создан до сбоя. Проверьте его перед повтором; скрипт не удаляет и не перезаписывает репозитории.') from exc


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner',default='lev4ik52')
    parser.add_argument('--name',default='ste80-humanizer-ru')
    parser.add_argument('--execute',action='store_true',help='Явно создать новый PUBLIC-репозиторий и загрузить пакет')
    args=parser.parse_args(argv)
    try:
        result=publish(args.owner,args.name,args.execute)
    except (OSError,ValueError,subprocess.SubprocessError,PublishError) as exc:
        print(f'Публикация не подтверждена: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr,'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
