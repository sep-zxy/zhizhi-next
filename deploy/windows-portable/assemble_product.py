"""Cloud-only final assembly, exact input verification, and reproducible ZIP."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import zipfile
from build_bundle import sha256,json_write

IGNORED_PARTS={'__pycache__','.pytest_cache','.git'}

def write_launchers(bundle):
 for action in ['start','stop','diagnostics','publish',
                'local-test-start','local-test-initialize','local-test-pair',
                'owner-acceptance-start','owner-acceptance-initialize','owner-acceptance-pair','owner-acceptance-reuse',
                'owner-engineering-preview','owner-engineering-activate','owner-engineering-start',
                'owner-model-activate','owner-model-start','owner-pi-activate','owner-pi-start','owner-pi-once']:
  arguments=' %*' if action in {'local-test-initialize','local-test-pair',
                               'owner-acceptance-initialize','owner-acceptance-pair','owner-acceptance-reuse','owner-model-activate','owner-pi-activate',
                               'owner-engineering-preview','owner-engineering-activate','owner-engineering-start'} else ''
  text='@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python\\python.exe" -B -s -X utf8 "%~dp0app\\launcher.py" '+action+arguments+'\r\nset "EXITCODE=%ERRORLEVEL%"\r\nif not "%EXITCODE%"=="0" pause\r\nexit /b %EXITCODE%\r\n'
  if action=='owner-pi-once':text=text.replace('if not "%EXITCODE%"=="0" pause\r\n','')
  (bundle/(action+'.cmd')).write_bytes(text.encode('ascii'))
 # This wrapper passes only explicit operator arguments. It never calls a
 # model unless that operator adds the module's --model-smoke switch.
 (bundle/'codex-diagnostic.cmd').write_bytes(
  b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
  b'"%~dp0runtime\\python\\python.exe" -B -s -X utf8 -m '
  b'ahadiff.knowledge_platform.model.diagnostic_probe %*\r\nexit /b %ERRORLEVEL%\r\n')

def binary_kind(path):
 with path.open('rb') as f:
  magic=f.read(4)
  if magic==b'\x7fELF':return 'elf'
  if magic in [b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe']:return 'mach-o'
  if magic==b'\x00asm':return 'wasm'
  if magic[:2]==b'MZ':
   f.seek(0x3c);offset=struct.unpack('<I',f.read(4))[0];f.seek(offset)
   if f.read(4)!=b'PE\0\0':raise ValueError('Malformed PE file')
   machine=struct.unpack('<H',f.read(2))[0]
   return {0x8664:'pe-x64',0xaa64:'pe-arm64',0x14c:'pe-x86'}.get(machine,f'pe-{machine:x}')
 return 'data'

def copy_component(source,destination):
 source=Path(source)
 record=json.loads((source/'component.json').read_text(encoding='utf-8'))
 if destination.exists():raise ValueError('Component output already exists')
 expected=record['files']
 for relative,digest in expected.items():
  path=source/relative
  if not path.resolve().is_relative_to(source.resolve()) or path.is_symlink() or not path.is_file():raise ValueError('Invalid component path')
  if sha256(path)!=digest:raise ValueError('Component checksum mismatch: '+relative)
 for path in source.rglob('*'):
  if path.is_symlink():raise ValueError('Component symlink forbidden')
  if path.is_file():
   relative=path.relative_to(source).as_posix()
   if relative!='component.json' and relative not in expected:raise ValueError('Unlisted component file: '+relative)
   kind=binary_kind(path)
   if kind in {'elf','mach-o','pe-arm64','pe-x86'}:raise ValueError('Wrong platform binary: '+relative+' '+kind)
 shutil.copytree(source,destination)
 return {k:record[k] for k in ('name','version','source_commit','entrypoints','runtime_requirements','known_limits') if k in record}

def assemble(args):
 repo=args.repo.resolve();bundle=args.output.resolve()/'bundle'
 commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
 if sys.platform=='win32':raise ValueError('Cloud assembly only')
 if not (bundle/'runtime/python/python.exe').is_file():raise ValueError('Prepare Windows runtimes first')
 app=bundle/'app';app.mkdir(exist_ok=True)
 components={}
 if args.pi:components['pi']=copy_component(args.pi,app/'pi')
 if args.wiki:
  components['wiki']=copy_component(args.wiki,app/'wiki')
  if not args.template_assets or not args.template_assets.is_file():raise ValueError('Independent approved template assets are required')
  shutil.copy2(args.template_assets,bundle/'provenance/wiki-template-assets.json')
 if getattr(args,'private_wiki',None):
  source=args.private_wiki.resolve()
  if not (source/'index.html').is_file():raise ValueError('Private Wiki must be independently prebuilt in GitHub')
  for path in source.rglob('*'):
   if path.is_symlink() or not path.resolve().is_relative_to(source):raise ValueError('Private Wiki output link forbidden')
  shutil.copytree(source,app/'wiki-private')
  components['wiki-private']={'name':'private-wiki-ui','source_commit':commit,'entrypoints':{'http':'/wiki/'},'known_limits':['Same-origin authentication and Windows E2E remain separate acceptance gates.']}
 # Only version-controlled runtime source, no local fixtures/cache/credentials.
 paths=subprocess.check_output(['git','ls-files','-z','projects/growth/src/ahadiff'],cwd=repo).decode().split('\0')
 for relative in filter(None,paths):
  source=repo/relative
  if source.is_symlink():raise ValueError('Backend source symlink')
  target=app/'backend'/Path(relative).relative_to('projects/growth/src')
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 shutil.copy2(repo/'projects/growth/LICENSE',app/'backend/LICENSE')
 viewer=repo/'projects/growth/viewer/dist'
 if not (viewer/'index.html').is_file():raise ValueError('Viewer must be prebuilt in the cloud')
 shutil.copytree(viewer,app/'viewer')
 for file in (repo/'deploy/windows-portable/runtime').glob('*.py'):shutil.copy2(file,app/file.name)
 # uv cross-target installation creates host console wrappers; never ship those.
 wrappers=bundle/'runtime/python/Lib/site-packages/bin'
 if wrappers.exists():shutil.rmtree(wrappers)
 write_launchers(bundle)
 json_write(bundle/'config/launch.json',{'schema_version':1,'owner':None,'identities_file':None,'model_config':None,'desktop_config':None,'pi_environment_file':None,'platform_port':8766,'run_worker':False,'auto_commit':False,'providers':[]})
 shutil.copy2(repo/'deploy/windows-portable/README.md',bundle/'README.md')
 for name in ['node-SHASUMS256.txt','windows-wheel-plan.json','base-runtime-binary-audit.json']:
  evidence=args.output.resolve()/'evidence'/name
  if evidence.is_file():shutil.copy2(evidence,bundle/'provenance'/name)
 json_write(bundle/'provenance/upstream-sources.json',{'git_source':'https://github.com/git-for-windows/git/tree/v2.56.0.windows.2','git_release':'https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.2','python_source':'https://www.python.org/downloads/release/python-31316/','node_source':'https://nodejs.org/dist/v22.23.3/','licenses':'Original bundled license and notice files are retained throughout runtime/ and app/.'})
 json_write(bundle/'provenance/assembly.json',{'assembly_source_commit':commit,'components':components,'backend_source_commit':commit,'backend_source_mode':'tracked Python runtime sources, no Windows build step','windows_tested':False,'live_e2e':'not_run','asr':'separate_component_pending_validation'})
 for directory in ['data','temp','cache','reports/logs','reports/screenshots','reports/receipts','browser-profile']:(bundle/directory).mkdir(parents=True,exist_ok=True)
 seal(bundle,args.output.resolve())

def seal(bundle,output):
 files={};binaries={};cases=set()
 for file in sorted(bundle.rglob('*')):
  if file.is_symlink():raise ValueError('Distribution symlink')
  if not file.is_file():continue
  relative=file.relative_to(bundle).as_posix()
  if relative=='provenance/distribution.json':continue
  if any(part in IGNORED_PARTS for part in file.parts):raise ValueError('Development cache in bundle')
  if relative.casefold() in cases:raise ValueError('Windows case collision')
  cases.add(relative.casefold())
  if relative.startswith(('data/','temp/','cache/','reports/','browser-profile/')):raise ValueError('Runtime/private data must not ship in base artifact')
  kind=binary_kind(file)
  # Official MinGit includes .NET AnyCPU PE32 assemblies and getprocaddr32.exe.
  # Keep its upstream archive intact and record these, rather than mislabeling
  # managed PE32 modules as Linux binaries or removing needed upstream files.
  if kind in {'elf','mach-o','pe-arm64'}:raise ValueError('Wrong platform binary: '+relative)
  if kind!='data':binaries[relative]=kind
  if relative!='config/launch.json':files[relative]=sha256(file)
 json_write(bundle/'provenance/binary-audit.json',binaries)
 files['provenance/binary-audit.json']=sha256(bundle/'provenance/binary-audit.json')
 json_write(bundle/'provenance/distribution.json',{'schema_version':1,'target':'windows-x64','mutable_paths':['config/launch.json','data','temp','cache','reports','browser-profile'],'files':files})
 archive=output/'zhizhi-windows-x64-portable.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for file in sorted(bundle.rglob('*')):
   if file.is_file():
    info=zipfile.ZipInfo(file.relative_to(bundle).as_posix(),date_time=(2026,10,20,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
    z.writestr(info,file.read_bytes())
 (output/'SHA256SUMS.txt').write_text(sha256(archive)+'  '+archive.name+'\n',encoding='ascii')
 print(json.dumps({'zip':str(archive),'sha256':sha256(archive),'bytes':archive.stat().st_size,'files':len(files),'windows_tested':False}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--pi',type=Path);p.add_argument('--wiki',type=Path);p.add_argument('--private-wiki',type=Path);p.add_argument('--template-assets',type=Path);a=p.parse_args();assemble(a)
