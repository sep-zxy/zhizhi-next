"""Cloud-only Windows assembly. Never run this program on the user's Windows host."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from packaging.markers import Marker, default_environment
from packaging.tags import cpython_tags, compatible_tags
from packaging.utils import parse_wheel_filename

PYTHON_VERSION = '3.13.16'
NODE_VERSION = '22.23.3'
GIT_VERSION = '2.56.0.2'
RUNTIMES = {
 'python': {'filename':f'python-{PYTHON_VERSION}-embed-amd64.zip','url':f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip','sha256':'97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297','source':'https://www.python.org/downloads/release/python-31316/'},
 'git': {'filename':f'MinGit-{GIT_VERSION}-64-bit.zip','url':f'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.2/MinGit-{GIT_VERSION}-64-bit.zip','sha256':'da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be','source':'https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.2'},
 'node': {'filename':f'node-v{NODE_VERSION}-win-x64.zip','url':f'https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip','source':f'https://nodejs.org/dist/v{NODE_VERSION}/SHASUMS256.txt'},
}

def sha256(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def json_write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def download(url, dest, expected):
 dest=Path(dest)
 if dest.exists():
  if sha256(dest)!=expected:raise ValueError(f'Existing download checksum mismatch: {dest.name}')
  return
 if not url.startswith(('https://files.pythonhosted.org/','https://www.python.org/','https://nodejs.org/','https://github.com/git-for-windows/git/releases/download/')):
  raise ValueError('Unapproved download origin')
 dest.parent.mkdir(parents=True,exist_ok=True)
 temporary=dest.with_suffix(dest.suffix+'.partial')
 try:
  with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
   shutil.copyfileobj(response,output)
  if sha256(temporary)!=expected:raise ValueError(f'Download checksum mismatch: {dest.name}')
  temporary.rename(dest)
 except BaseException:
  if temporary.exists():temporary.unlink()
  raise  # No mirror, retry, downgrade, authentication, or denial bypass.

def safe_extract(archive,destination,prefix=''):
 destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(archive) as z:
  seen=set()
  for item in z.infolist():
   name=item.filename
   if prefix:
    if not name.startswith(prefix):raise ValueError('Unexpected archive prefix')
    name=name[len(prefix):]
   if not name:continue
   p=PurePosixPath(name)
   if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:raise ValueError('Unsafe archive path')
   if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Archive symlink forbidden')
   normalized=name.rstrip('/').casefold()
   if normalized in seen:raise ValueError('Case-insensitive archive collision')
   seen.add(normalized)
   target=destination.joinpath(*p.parts)
   if item.is_dir():target.mkdir(parents=True,exist_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True)
    with z.open(item) as src,target.open('wb') as out:shutil.copyfileobj(src,out)

def wheel_plan(lockfile,extras=('captions','wechat')):
 packages=tomllib.loads(Path(lockfile).read_text())['package']
 env=default_environment();env.update(sys_platform='win32',os_name='nt',platform_system='Windows',platform_machine='AMD64',python_version='3.13',python_full_version=PYTHON_VERSION,extra='')
 valid=list(cpython_tags((3,13),platforms=['win_amd64']))+list(compatible_tags((3,13),interpreter='cp313',platforms=['win_amd64']))
 rank={tag:i for i,tag in enumerate(valid)}
 visited=set();selected={};queue=[('ahadiff',None,tuple(extras))]
 while queue:
  name,version,features=queue.pop()
  candidates=[p for p in packages if p['name']==name and (version is None or p['version']==version) and (not p.get('resolution-markers') or any(Marker(m).evaluate(env) for m in p['resolution-markers']))]
  if len(candidates)!=1:raise ValueError(f'Ambiguous locked package {name}')
  p=candidates[0];key=(name,p['version'],features)
  if key in visited:continue
  visited.add(key);selected[name]=p
  dependencies=list(p.get('dependencies',[]))
  for feature in features:
   if feature not in p.get('optional-dependencies',{}):raise ValueError(f'Missing extra {name}[{feature}]')
   dependencies+=p['optional-dependencies'][feature]
  for dep in dependencies:
   if dep.get('marker') and not Marker(dep['marker']).evaluate(env):continue
   queue.append((dep['name'],dep.get('version'),tuple(dep.get('extra',[]))))
 result=[]
 for name,p in sorted(selected.items()):
  if name=='ahadiff':continue
  options=[]
  for w in p.get('wheels',[]):
   filename=w['url'].split('/')[-1];tags=parse_wheel_filename(filename)[3]
   matches=[rank[t] for t in tags if t in rank]
   if matches:options.append((min(matches),w))
  if not options:raise ValueError(f'No compatible binary wheel: {name} {p["version"]}')
  w=min(options,key=lambda x:x[0])[1]
  result.append({'name':name,'version':p['version'],'filename':w['url'].split('/')[-1],'url':w['url'],'sha256':w['hash'].removeprefix('sha256:'),'size':w['size']})
 return result

def prepare(repo,output,fetch=False):
 output=Path(output);downloads=output/'downloads';downloads.mkdir(parents=True,exist_ok=True)
 plan=wheel_plan(Path(repo)/'projects/growth/uv.lock')
 json_write(output/'evidence/windows-wheel-plan.json',{'target':'cp313-win_amd64','extras':['captions','wechat'],'lock_sha256':sha256(Path(repo)/'projects/growth/uv.lock'),'wheels':plan})
 if fetch:
  for i,p in enumerate(plan,1):
   print(f'[{i}/{len(plan)}] {p["filename"]}',flush=True)
   download(p['url'],downloads/'wheels'/p['filename'],p['sha256'])
 return plan

def prepare_runtimes(repo,output):
 output=Path(output);bundle=output/'bundle';plan=prepare(repo,output)
 if (bundle/'runtime').exists():raise ValueError('Runtime output already exists; use a fresh output directory or inspect before resuming')
 checks=(output/'evidence/node-SHASUMS256.txt').read_text()
 entry=next(line.split()[0] for line in checks.splitlines() if line.split()[-1]==RUNTIMES['node']['filename'])
 RUNTIMES['node']['sha256']=entry
 for name,info in RUNTIMES.items():
  file=output/'downloads'/info['filename']
  if sha256(file)!=info['sha256']:raise ValueError(f'Runtime checksum mismatch: {name}')
  safe_extract(file,bundle/'runtime'/name,prefix=f'node-v{NODE_VERSION}-win-x64/' if name=='node' else '')
 # Ship node.exe and license, not npm installers or development package manager.
 node=bundle/'runtime/node'
 for item in node.iterdir():
  if item.name not in {'node.exe','LICENSE','README.md','CHANGELOG.md'}:
   if item.is_dir():shutil.rmtree(item)
   else:item.unlink()
 target=bundle/'runtime/python/Lib/site-packages'
 command=['uv','pip','install','--no-config','--no-index','--no-deps','--only-binary',':all:','--python-version','3.13','--python-platform','x86_64-pc-windows-msvc','--target',str(target)]
 command += [str(output/'downloads/wheels'/p['filename']) for p in plan]
 subprocess.run(command,check=True)
 # ._pth ignores ambient Python configuration. Explicit paths plus sitecustomize
 # enable vendored pywin32 .pth bootstrap, never user-site or developer venv.
 (bundle/'runtime/python/python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../../app\n../../app/backend\nimport site\n',encoding='utf-8')
 (bundle/'runtime/python/sitecustomize.py').write_text('import sys\nsys.dont_write_bytecode = True\nimport site, pathlib\nsite.ENABLE_USER_SITE = False\nsite.addsitedir(str(pathlib.Path(__file__).parent / "Lib" / "site-packages"))\n',encoding='utf-8')
 json_write(bundle/'provenance/runtime-sources.json',RUNTIMES)
 json_write(bundle/'provenance/windows-wheels.json',plan)
 licenses={}
 for info in target.glob('*.dist-info'):
  from email.parser import BytesParser
  meta=BytesParser().parsebytes((info/'METADATA').read_bytes())
  licenses[meta.get('Name') or info.name]={'version':meta.get('Version'),'expression':meta.get('License-Expression'),'declared_license':meta.get('License'),'license_files':[str(p.relative_to(bundle)) for p in info.rglob('*') if p.is_file() and ('license' in p.name.lower() or 'copying' in p.name.lower() or 'notice' in p.name.lower())]}
 json_write(bundle/'provenance/python-licenses.json',licenses)
 print('Windows runtime files prepared; Windows execution NOT tested.',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','fetch-wheels','prepare-runtimes']);p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if sys.platform=='win32':raise SystemExit('Cloud assembly only. Windows must run the final artifact.')
 if a.action=='prepare-runtimes':prepare_runtimes(a.repo,a.output)
 else:prepare(a.repo,a.output,a.action=='fetch-wheels')
if __name__=='__main__':main()
