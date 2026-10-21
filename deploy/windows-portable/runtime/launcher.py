"""Prepackaged Windows launcher; no installation, compilation, or authentication."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROOT = Path(r'C:\Code\zhizhi-e2e-20261020')
BROWSER = None
CHILDREN = []
LOGS = []
JOB = None
SERVICES = {}

def within(path):
 p=Path(path)
 if not p.is_absolute():p=ROOT/p
 for ancestor in [p,*p.parents]:
  if ancestor.is_symlink() or (hasattr(ancestor,'is_junction') and ancestor.is_junction()):raise ValueError('Links/junctions are forbidden in application paths')
 resolved=p.resolve()
 if not resolved.is_relative_to(ROOT):raise ValueError('Path is outside portable root')
 return resolved

def write_json(name,data):
 p=within(name);p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def isolated_environment():
 systemroot=os.environ.get('SystemRoot',r'C:\Windows')
 env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE'}}
 paths={'HOME':'data/home','USERPROFILE':'data/home','APPDATA':'data/appdata','LOCALAPPDATA':'data/localappdata','TEMP':'temp','TMP':'temp','TMPDIR':'temp','XDG_CONFIG_HOME':'data/config','XDG_CACHE_HOME':'cache','XDG_DATA_HOME':'data/shared','XDG_STATE_HOME':'data/state','PI_CODING_AGENT_DIR':'data/pi-interactive','PI_CODING_AGENT_SESSION_DIR':'data/pi-interactive/sessions','HF_HOME':'cache/huggingface','HUGGINGFACE_HUB_CACHE':'cache/huggingface/hub','JITI_FS_CACHE':'cache/jiti'}
 for key,value in paths.items():
  p=within(value);p.mkdir(parents=True,exist_ok=True);env[key]=str(p)
 env.update(PATH=os.pathsep.join([str(ROOT/'runtime/node'),str(ROOT/'runtime/git/cmd'),str(ROOT/'runtime/python'),str(Path(systemroot)/'System32')]),ZHIZHI_PORTABLE_ROOT=str(ROOT),ZHIZHI_PORTABLE_STATE=str(ROOT/'data'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONUTF8='1',PI_OFFLINE='1',PI_SKIP_VERSION_CHECK='1',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0')
 return env

def run(command,**kwargs):
 return subprocess.run(command,check=True,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=30,**kwargs).stdout.strip()

def diagnose(env):
 result={'status':'checking','platform':platform.platform(),'python':sys.version.split()[0],'architecture':platform.machine(),'root':str(ROOT),'checks':{},'unified_platform':'waiting_auth','live_e2e':'not_run'}
 if sys.platform!='win32' or platform.machine().upper() not in {'AMD64','X86_64'}:raise RuntimeError('This artifact requires Windows x64')
 if os.path.normcase(str(ROOT))!=os.path.normcase(str(EXPECTED_ROOT)):raise RuntimeError(f'Extract only into {EXPECTED_ROOT}')
 for name in ['cryptography','tree_sitter','tree_sitter_c_sharp','tree_sitter_go','tree_sitter_java','tree_sitter_javascript','tree_sitter_php','tree_sitter_ruby','tree_sitter_rust','tree_sitter_typescript','pydantic_core','psycopg','win32api','sqlite3']:
  importlib.import_module(name);result['checks'][name]='import_ok'
 from ahadiff.core.sqlite_util import assert_sqlite_runtime_supported
 import sqlite3
 assert_sqlite_runtime_supported();result['sqlite']=sqlite3.sqlite_version
 result['node']=run([str(ROOT/'runtime/node/node.exe'),'--version'],env=env,cwd=ROOT)
 result['git']=run([str(ROOT/'runtime/git/cmd/git.exe'),'--version'],env=env,cwd=ROOT)
 result['pi_version']=run([str(ROOT/'runtime/node/node.exe'),'--no-experimental-strip-types',str(ROOT/'app/pi/cli-native.mjs'),'--version'],env=env,cwd=within('data/pi-interactive'))
 if result['pi_version']!='1.3.0':raise RuntimeError('Unexpected Pi runtime version')
 result['asr']='blocked_missing_asr_resources'
 result['wiki']='packaged_not_yet_published'
 result['portable_preflight']=json.loads(run([sys.executable,'-B','-s','-X','utf8','-m','ahadiff.knowledge_platform.portable','--state-directory',str(ROOT/'data'),'--write-path',str(ROOT/'temp'),'--write-path',str(ROOT/'reports')],env=env,cwd=ROOT))
 result['status']='runtime_ready_not_live_e2e';write_json('reports/diagnostics.json',result)
 return result

def free_port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def spawn(command,name,env,cwd=None,new_console=False):
 log=within('reports/logs/'+name+'.log');log.parent.mkdir(parents=True,exist_ok=True)
 stream=log.open('a',encoding='utf-8');LOGS.append(stream)
 flags=subprocess.CREATE_NEW_CONSOLE if new_console and sys.platform=='win32' else 0
 # The helper waits on a pipe before spawning any process. Assign it to an
 # unnamed kill-on-close job first, then release it. Browser descendants inherit
 # ownership even when their original browser process exits.
 helper=[sys.executable,'-B','-s','-X','utf8',str(ROOT/'app/launcher.py'),'_child',json.dumps(command), 'console' if new_console else 'log']
 proc=subprocess.Popen(helper,cwd=cwd or ROOT,env=env,stdin=subprocess.PIPE,stdout=None if new_console else stream,stderr=None if new_console else subprocess.STDOUT,creationflags=flags)
 CHILDREN.append(proc)
 try:
  JOB.assign(proc)
  proc.stdin.write(b'GO\n');proc.stdin.flush();proc.stdin.close()
 except BaseException:
  proc.terminate();proc.wait(timeout=5);raise
 if name in {'platform','viewer'}:SERVICES[name]=proc
 return proc

def await_health(proc,port):
 for _ in range(120):
  if proc.poll() is not None:raise RuntimeError('Application exited before health check; see package-local logs')
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz',timeout=1) as r:
    if r.status==200:return
  except OSError:pass
  time.sleep(.5)
 raise RuntimeError('Application startup timed out')

def browser_path():
 # Read only standard vendor executable paths, never personal profile contents.
 for base in [os.environ.get('ProgramFiles(x86)'),os.environ.get('ProgramFiles')]:
  if base:
   for rel in ['Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe']:
    p=Path(base)/rel
    if p.is_file():return p
 return None

def load_config():
 file=within('config/launch.json')
 return json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}

def start(env):
 import msvcrt
 lock_path=within('data/launcher.lock');lock_path.parent.mkdir(parents=True,exist_ok=True)
 lock=lock_path.open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
 try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:raise RuntimeError('This package is already running')
 stop=within('data/stop.request')
 if stop.exists():stop.unlink()
 config=load_config();status={'viewer':'starting','unified_platform':'waiting_auth','pi':'waiting_auth','dynamic_wiki':'not_run','live_e2e':'not_run'}
 identities=config.get('identities_file')
 viewer_env=dict(env)
 if identities:
  identity=within(identities)
  if not identity.is_file():raise RuntimeError('Approved identity configuration is missing')
  owner=config.get('owner')
  if not isinstance(owner,str) or not owner:raise RuntimeError('Approved owner is required')
  port=config.get('platform_port',8766)
  if type(port) is not int or not 1024 <= port <= 65535:raise ValueError('Invalid platform port')
  args=[sys.executable,'-B','-s','-X','utf8','-m','ahadiff.knowledge_platform','--state-directory',str(ROOT/'data/platform'),'--identities-file',str(identity),'--owner',owner,'--host','127.0.0.1','--port',str(port)]
  for field,flag in [('model_config','--model-config'),('channel_config','--channel-config'),('asr_model_directory','--asr-model-directory')]:
   if config.get(field):args.extend([flag,str(within(config[field]))])
  if config.get('run_worker') is True:args.append('--run-worker')
  if config.get('auto_commit') is True:args.append('--auto-commit')
  for provider in config.get('providers',[]):args.extend(['--provider',provider])
  proc=spawn(args,'platform',env);await_health(proc,port)
  status.update(unified_platform='running_auth_required',platform_url=f'http://127.0.0.1:{port}')
  # Desktop config is an authorized deployment input, not generated credentials.
  if config.get('desktop_config'):
   desktop=within(config['desktop_config'])
   if json.loads(desktop.read_text(encoding='utf-8')).get('service_url')!=status['platform_url']:raise ValueError('Desktop service_url must match this package loopback platform')
   viewer_env['AHADIFF_KNOWLEDGE_CONFIG']=str(desktop)
 workspace=within('data/viewer-workspace');(workspace/'.ahadiff').mkdir(parents=True,exist_ok=True)
 viewer_env['AHADIFF_VIEWER_DIST']=str(ROOT/'app/viewer')
 port=free_port();proc=spawn([sys.executable,'-B','-s','-X','utf8','-m','ahadiff','serve','--repo-root',str(workspace),'--port',str(port),'--no-browser'],'viewer',viewer_env,workspace)
 await_health(proc,port);url=f'http://127.0.0.1:{port}/'
 status.update(viewer='running',viewer_url=url,viewer_mode='unified_configured_not_verified' if viewer_env.get('AHADIFF_KNOWLEDGE_CONFIG') else 'standalone_not_unified')
 if identities and config.get('pi_environment_file'):
  pi_config=json.loads(within(config['pi_environment_file']).read_text(encoding='utf-8'))
  permitted={'ZHIZHI_MODEL_PROVIDER','ZHIZHI_MODEL_ID','ZHIZHI_AGENT_TOKEN','ZHIZHI_USER_TOKEN','ZHIZHI_CHINA_TERMS_VERIFIED','ZHIZHI_MAX_OUTPUT_TOKENS'}
  if set(pi_config)-permitted or any(not isinstance(v,str) for v in pi_config.values()):raise ValueError('Unexpected Pi environment field')
  pi_env=dict(env);pi_env.update(pi_config);pi_env['ZHIZHI_API_URL']=status['platform_url']
  provider=pi_env.get('ZHIZHI_MODEL_PROVIDER')
  if provider in {'openai','zai','zai-coding-cn'} and pi_env.get('ZHIZHI_MODEL_ID'):
   probe=subprocess.run([str(ROOT/'runtime/node/node.exe'),'--no-experimental-strip-types',str(ROOT/'app/pi/cli-native.mjs'),'auth','check','--provider',provider,'--json','--no-refresh'],cwd=within('data/pi-interactive'),env=pi_env,capture_output=True,text=True,encoding='utf-8',timeout=20)
   try:auth=json.loads(probe.stdout)
   except ValueError:auth={}
   if probe.returncode==0 and auth.get('status')=='ready' and auth.get('provider')==provider:
    spawn([str(ROOT/'runtime/node/node.exe'),'--no-experimental-strip-types',str(ROOT/'app/pi/launch-interactive.mjs')],'pi',pi_env,new_console=True)
    status['pi']='started_auth_checked_not_live_e2e'
   else:status['pi']='waiting_auth'
  else:status['pi']='waiting_auth'
 write_json('reports/status.json',status)
 print(json.dumps(status,ensure_ascii=False,indent=2),flush=True)
 print('Runtime/UI startup is NOT unified-platform or live-account E2E acceptance.',flush=True)
 browser=BROWSER
 if browser:
  spawn([str(browser),'--user-data-dir='+str(within('browser-profile')),'--disk-cache-dir='+str(within('cache/browser')),'--no-first-run','--no-default-browser-check','--disable-background-mode',url],'browser',env)
 else:print('No existing Edge/Chrome found. No browser will be installed. URL: '+url,flush=True)
 print('Use stop.cmd or Ctrl+C to stop this package. Authorization status is in reports/status.json.',flush=True)
 try:
  while not stop.exists():
   for name,service in SERVICES.items():
    if service.poll() is not None:
     status['unified_platform' if name=='platform' else name]='failed'
     status['failure']={'service':name,'exit_code':service.returncode}
     write_json('reports/status.json',status)
     raise RuntimeError(name+' exited; see package-local logs')
   time.sleep(.5)
 finally:
  if JOB is not None:JOB.close()
  for child in reversed(CHILDREN):
   if child.poll() is None:
    child.terminate()
    try:child.wait(timeout=8)
    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
  for stream in LOGS:stream.close()
  status['viewer']='stopped';status['unified_platform']='stopped' if identities else 'waiting_auth';write_json('reports/status.json',status)
  lock.close()

def verify_distribution():
 manifest=within('provenance/distribution.json')
 record=json.loads(manifest.read_text(encoding='utf-8'))
 for relative,expected in record['files'].items():
  path=within(relative)
  if not path.is_file():raise RuntimeError('Distribution file missing: '+relative)
  digest=hashlib.sha256()
  with path.open('rb') as file:
   for block in iter(lambda:file.read(1024*1024),b''):digest.update(block)
  if digest.hexdigest()!=expected:raise RuntimeError('Distribution hash mismatch: '+relative)
 for prefix in ['app','runtime']:
  for path in within(prefix).rglob('*'):
   if path.is_file() and path.relative_to(ROOT).as_posix() not in record['files']:raise RuntimeError('Unlisted distribution code file')


def publish(env):
 config=load_config()
 if not config.get('publication_config'):raise RuntimeError('An explicitly approved publication configuration is required')
 settings=json.loads(within(config['publication_config']).read_text(encoding='utf-8'))
 required={'repository','revision','domain_database','owner_id','policy','template_assets','staging','output','report','public_base_url'}
 if set(settings)!=required:raise ValueError('Publication configuration fields do not match the approved contract')
 arguments=[sys.executable,'-B','-s','-X','utf8',str(ROOT/'app/wiki/publish.py'),'--node',str(ROOT/'runtime/node/node.exe')]
 paths={'repository','domain_database','policy','template_assets','staging','output','report'}
 for key in sorted(required):
  value=settings[key]
  if not isinstance(value,str) or not value:raise ValueError('Invalid publication configuration value')
  if key in paths:value=str(within(value))
  arguments.extend(['--'+key.replace('_','-'),value])
 for key in ['staging','output']:
  if not within(settings[key]).is_relative_to(within('data/publication')):raise ValueError('Publication output must remain in data/publication')
  if within(settings[key]).exists():raise ValueError('Publication output must be a new directory')
 if not within(settings['report']).is_relative_to(within('data/reports')):raise ValueError('Internal publication report must remain in data/reports')
 proc=spawn(arguments,'publication',env)
 code=proc.wait()
 if code:raise RuntimeError('Publication failed closed; see package-local publication log')
 print('Generated and verified new local pages. No public deployment was performed.',flush=True)


def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','diagnostics','publish']);args=parser.parse_args()
 if sys.platform!='win32':raise RuntimeError('Windows runtime only')
 within(ROOT)
 if os.path.normcase(str(ROOT))!=os.path.normcase(str(EXPECTED_ROOT)):raise RuntimeError('Unexpected extraction directory')
 if args.action=='stop':
  if not within('data').is_dir():print('This package is not running.');return
  within('data/stop.request').write_text('stop\n',encoding='ascii');print('Stop requested for this package only.');return
 verify_distribution()
 global BROWSER, JOB
 BROWSER=browser_path()
 from windows_job import OwnedJob
 JOB=OwnedJob()
 env=isolated_environment();os.environ.clear();os.environ.update(env)
 result=diagnose(env)
 if args.action=='diagnostics':print(json.dumps(result,ensure_ascii=False,indent=2));return
 if args.action=='publish':publish(env);return
 start(env)

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='_child':
  if sys.platform!='win32' or sys.stdin.buffer.readline()!=b'GO\n':sys.exit(2)
  command=json.loads(sys.argv[2])
  if sys.argv[3]=='console':
   with open('CONIN$','rb',buffering=0) as inp,open('CONOUT$','wb',buffering=0) as out:
    sys.exit(subprocess.call(command,stdin=inp,stdout=out,stderr=out))
  sys.exit(subprocess.call(command,stdin=subprocess.DEVNULL))
 try:main()
 except KeyboardInterrupt:print('Stopped.')
 except Exception as error:
  # No configuration contents or subprocess output are echoed into reports.
  report={'status':'blocked','error_type':type(error).__name__,'message':str(error),'live_e2e':'not_run'}
  print(json.dumps(report,ensure_ascii=False,indent=2))
  if sys.platform=='win32' and os.path.normcase(str(ROOT))==os.path.normcase(str(EXPECTED_ROOT)):
   try:write_json('reports/startup-error.json',report)
   except Exception:pass
  sys.exit(2)
 finally:
  if JOB is not None:JOB.close()
  for child in reversed(CHILDREN):
   if child.poll() is None:
    child.terminate()
    try:child.wait(timeout=8)
    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
  for stream in LOGS:
   if not stream.closed:stream.close()
