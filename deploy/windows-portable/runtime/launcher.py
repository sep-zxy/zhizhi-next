"""Prepackaged Windows launcher; no installation, compilation, or authentication."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
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
  try:attributes=ancestor.lstat()
  except FileNotFoundError:attributes=None
  # Symlink/junction helpers do not cover every Windows reparse tag. Reject
  # the attribute itself on every existing ancestor, including unknown tags.
  # Other stat errors propagate: inability to inspect a path is not approval.
  if attributes is not None and getattr(attributes,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
   raise ValueError('Windows reparse points are forbidden in application paths')
  if ancestor.is_symlink() or (hasattr(ancestor,'is_junction') and ancestor.is_junction()):raise ValueError('Links/junctions are forbidden in application paths')
 resolved=p.resolve()
 if not resolved.is_relative_to(ROOT):raise ValueError('Path is outside portable root')
 return resolved

def validate_root():
 root=within(ROOT)
 approved=EXPECTED_ROOT.resolve()
 # Preserve the previous single-root package for rollback. New Git candidates
 # run in a version directory, still strictly inside the already approved root.
 if root==approved:return
 if (root.parent==approved/'git-delivery'/'releases'
     and re.fullmatch(r'[0-9a-f]{12}-[0-9]+-[0-9]+',root.name)):return
 raise RuntimeError('Unexpected portable root; use the approved root or its immutable Git release directory')

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
 validate_root()
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

def await_health(proc,port,service,authentication_status='configured'):
 paths={'platform':'/health','viewer':'/healthz'}
 if service not in paths:raise ValueError('Unknown service health contract')
 if authentication_status not in {'configured','waiting_auth'}:raise ValueError('Unknown authentication mode')
 for _ in range(120):
  if proc.poll() is not None:raise RuntimeError('Application exited before health check; see package-local logs')
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}{paths[service]}',timeout=1) as r:
    if r.status==200:
     record=json.loads(r.read(4097))
     expected_status='ready' if authentication_status=='configured' else 'waiting_auth'
     if (service=='platform' and record.get('service')=='zhizhi-knowledge'
         and record.get('status')==expected_status
         and record.get('authentication_status')==authentication_status):return
     if service=='viewer' and record.get('ok') is True:return
  except (OSError,ValueError):pass
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

def load_config(local_test=False,owner_acceptance=False):
 if local_test and owner_acceptance:raise ValueError('Identity policies cannot be mixed')
 file=within('data/owner-acceptance/launch.json' if owner_acceptance else
             'data/local-test/launch.json' if local_test else 'config/launch.json')
 if (local_test or owner_acceptance) and not file.is_file():raise RuntimeError('Explicit separate identity initialization is required')
 return json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}

def owner_desktop_config(port):
 file=within('data/owner-acceptance/desktop.json')
 if not file.is_file():raise RuntimeError('Owner acceptance desktop configuration is missing')
 record=json.loads(file.read_text(encoding='utf-8'))
 if (not isinstance(record,dict)
     or set(record)!={'service_url','identity_file','identity_format','device_id',
                      'input_classification','providers','model'}
     or record.get('service_url')!=f'http://127.0.0.1:{port}'
     or record.get('identity_file')!='identity.json'
     or record.get('identity_format')!='zhizhi-owner-acceptance-dpapi-v1'
     or not isinstance(record.get('device_id'),str)
     or not re.fullmatch(r'[0-9a-f]{32}',record['device_id'])
     or record.get('input_classification')!='personal'
     or record.get('providers')!=[]
     or record.get('model')!={'enabled':False}
     or record['model']['enabled'] is not False):
  raise ValueError('Owner acceptance base launcher requires its exact model-disabled desktop policy')
 if not within('data/owner-acceptance/desktop').is_dir():raise RuntimeError('Owner acceptance desktop state is missing')
 return file

def platform_command(config,local_test=False,owner_acceptance=False,learning_viewer_port=None):
 if not isinstance(config,dict):raise ValueError('Invalid launch configuration')
 if local_test and owner_acceptance:raise ValueError('Identity policies cannot be mixed')
 if 'learning_viewer_url' in config or 'learning_viewer_port' in config:
  raise ValueError('Learning navigation is assigned by the launcher, not launch configuration')
 if local_test:
  if (set(config)!={'mode','local_test_identity','platform_port'}
      or config.get('mode')!='local-test'
      or config.get('local_test_identity')!='data/local-test/identity.json'):
   raise ValueError('Local-test configuration cannot include production settings')
 elif owner_acceptance:
  if (set(config)!={'mode','owner_dpapi_identity','platform_port'}
      or config.get('mode')!='owner-acceptance'
      or config.get('owner_dpapi_identity')!='data/owner-acceptance/identity.json'):
   raise ValueError('Owner acceptance configuration cannot include production settings')
 elif 'mode' in config or 'local_test_identity' in config or 'owner_dpapi_identity' in config:
  raise ValueError('Isolated identity requires its separate explicit launcher')
 port=config.get('platform_port',8766)
 if type(port) is not int or not 1024 <= port <= 65535:raise ValueError('Invalid platform port')
 if learning_viewer_port is not None:
  if (local_test or type(learning_viewer_port) is not int
      or not 1024 <= learning_viewer_port <= 65535 or learning_viewer_port==port):
   raise ValueError('Invalid independently assigned learning viewer port')
 private_web=within('app/wiki-private')
 if not within('app/wiki-private/index.html').is_file():raise RuntimeError('Prebuilt private Wiki UI is missing')
 args=[sys.executable,'-B','-s','-X','utf8','-m','ahadiff.knowledge_platform',
       '--state-directory',str(within('data/owner-acceptance/platform' if owner_acceptance else
                                     'data/local-test/platform' if local_test else 'data/platform')),'--host','127.0.0.1',
       '--port',str(port),'--private-web-root',str(private_web)]
 if learning_viewer_port is not None:
  args.extend(['--learning-viewer-url',f'http://127.0.0.1:{learning_viewer_port}/'])
 if owner_acceptance:
  identity=within('data/owner-acceptance/identity.json')
  if not identity.is_file() or not within('data/owner-acceptance/platform').is_dir():
   raise RuntimeError('Explicit owner acceptance initialization is incomplete')
  owner_desktop_config(port)
  args.extend(['--owner-dpapi-identity',str(identity),'--owner','portable-operational-acceptance',
               '--owner-browser-actor','portable-acceptance-person'])
  return args,port,'configured'
 if local_test:
  identity=within('data/local-test/identity.json')
  if not identity.is_file() or not within('data/local-test/platform').is_dir():
   raise RuntimeError('Explicit local-test initialization is incomplete')
  args.extend(['--local-test-identity',str(identity)])
  return args,port,'configured'
 identities=config.get('identities_file')
 if not identities:
  privileged=['owner','model_config','channel_config','asr_model_directory',
              'desktop_config','pi_environment_file','run_worker','auto_commit','providers']
  if any(config.get(field) for field in privileged):
   raise ValueError('Identity-free shell cannot enable privileged configuration')
  return args,port,'waiting_auth'
 identity=within(identities)
 if not identity.is_file():raise RuntimeError('Approved identity configuration is missing')
 owner=config.get('owner')
 if not isinstance(owner,str) or not owner:raise RuntimeError('Approved owner is required')
 args.extend(['--identities-file',str(identity),'--owner',owner])
 for field,flag in [('model_config','--model-config'),('channel_config','--channel-config'),('asr_model_directory','--asr-model-directory')]:
  if config.get(field):args.extend([flag,str(within(config[field]))])
 if config.get('run_worker') is True:args.append('--run-worker')
 if config.get('auto_commit') is True:args.append('--auto-commit')
 for provider in config.get('providers',[]):args.extend(['--provider',provider])
 return args,port,'configured'

def start(env,local_test=False,owner_acceptance=False):
 import msvcrt
 lock_path=within('data/launcher.lock');lock_path.parent.mkdir(parents=True,exist_ok=True)
 lock=lock_path.open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
 try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:raise RuntimeError('This package is already running')
 stop=within('data/stop.request')
 if stop.exists():stop.unlink()
 config=load_config(local_test,owner_acceptance);status={'viewer':'starting','unified_platform':'waiting_auth','pi':'waiting_auth','dynamic_wiki':'not_run','live_e2e':'not_run'}
 identities=config.get('identities_file')
 configured=local_test or owner_acceptance or bool(identities)
 viewer_env=dict(env)
 # Allocate once before platform startup. A collision or later bind failure is
 # fatal, never a reason to scan ports or advertise a different viewer URL.
 viewer_port=None if local_test else free_port()
 args,port,authentication_status=platform_command(config,local_test,owner_acceptance,viewer_port)
 proc=spawn(args,'platform',env);await_health(proc,port,'platform',authentication_status)
 status.update(unified_platform='running_auth_required' if configured else 'waiting_auth',
               platform_url=f'http://127.0.0.1:{port}',
               private_wiki_url=f'http://127.0.0.1:{port}/wiki/',
               private_wiki='authentication_configured_not_verified' if configured else 'shell_running_not_authenticated')
 if local_test:
  status.update(fixture=True,mode='local-test',browser_authentication='pairing_required',
                viewer='disabled_in_local_test',pi='disabled_in_local_test')
 if owner_acceptance:
  status.update(fixture=False,mode='owner-acceptance',browser_authentication='pairing_required',
                pi='disabled_in_owner_acceptance',model='disabled',worker='disabled')
  viewer_env['AHADIFF_KNOWLEDGE_CONFIG']=str(owner_desktop_config(port))
 if identities:
  # Desktop config is an authorized deployment input, not generated credentials.
  if config.get('desktop_config'):
   desktop=within(config['desktop_config'])
   if json.loads(desktop.read_text(encoding='utf-8')).get('service_url')!=status['platform_url']:raise ValueError('Desktop service_url must match this package loopback platform')
   viewer_env['AHADIFF_KNOWLEDGE_CONFIG']=str(desktop)
 if not local_test:
  workspace=within('data/owner-acceptance/desktop' if owner_acceptance else 'data/viewer-workspace');(workspace/'.ahadiff').mkdir(parents=True,exist_ok=True)
  viewer_env['AHADIFF_VIEWER_DIST']=str(ROOT/'app/viewer')
  proc=spawn([sys.executable,'-B','-s','-X','utf8','-m','ahadiff','serve','--repo-root',str(workspace),'--port',str(viewer_port),'--no-browser'],'viewer',viewer_env,workspace)
  await_health(proc,viewer_port,'viewer')
  status.update(viewer='running',viewer_url=f'http://127.0.0.1:{viewer_port}/',viewer_mode='unified_configured_not_verified' if viewer_env.get('AHADIFF_KNOWLEDGE_CONFIG') else 'standalone_not_unified')
 url=status['private_wiki_url']
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
  profile='data/owner-acceptance/browser-profile' if owner_acceptance else 'data/local-test/browser-profile' if local_test else 'browser-profile'
  cache='data/owner-acceptance/browser-cache' if owner_acceptance else 'data/local-test/browser-cache' if local_test else 'cache/browser'
  spawn([str(browser),'--user-data-dir='+str(within(profile)),'--disk-cache-dir='+str(within(cache)),'--no-first-run','--no-default-browser-check','--disable-background-mode',url],'browser',env)
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
  status['viewer']='disabled_in_local_test' if local_test else 'stopped';status['private_wiki']='stopped';status['unified_platform']='stopped' if configured else 'waiting_auth';write_json('reports/status.json',status)
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


def parse_arguments(argv=None):
 parser=argparse.ArgumentParser()
 parser.add_argument('action',choices=['start','stop','diagnostics','publish',
                                     'local-test-start','local-test-initialize','local-test-pair',
                                     'owner-acceptance-start','owner-acceptance-initialize','owner-acceptance-pair'])
 parser.add_argument('--approve-local-test-identity',action='store_true')
 parser.add_argument('--approve-owner-acceptance-identity',action='store_true')
 parser.add_argument('--port',type=int)
 parser.add_argument('--pairing-code')
 args=parser.parse_args(argv)
 if args.action=='local-test-initialize':
  if not args.approve_local_test_identity:parser.error('Explicit --approve-local-test-identity is required')
  if args.port is not None and not 1024 <= args.port <= 65535:parser.error('Invalid local-test port')
  if args.approve_owner_acceptance_identity:parser.error('Identity approvals cannot be mixed')
 elif args.action=='owner-acceptance-initialize':
  if not args.approve_owner_acceptance_identity:parser.error('Explicit --approve-owner-acceptance-identity is required')
  if args.port is not None and not 1024 <= args.port <= 65535:parser.error('Invalid owner acceptance port')
  if args.approve_local_test_identity:parser.error('Identity approvals cannot be mixed')
 elif args.approve_local_test_identity or args.approve_owner_acceptance_identity or args.port is not None:
  parser.error('Initialization options require their separate explicit initializer')
 if args.action=='local-test-pair':
  if not args.pairing_code or not re.fullmatch(r'[A-F0-9]{20}',args.pairing_code):
   parser.error('A 20-character uppercase pairing code is required')
 elif args.pairing_code is not None:
  parser.error('Pairing code is only allowed for local-test-pair')
 return args

def run_local_test_identity(args):
 # Module validates Windows, portable paths and encrypted identity independently.
 # Import only after explicit action validation, distribution verification and
 # environment isolation; standard start never imports or initializes it.
 if args.action=='local-test-initialize' and args.approve_local_test_identity:
  arguments=['initialize','--approve-local-test-identity','--port',str(args.port or 8766)]
 elif args.action=='local-test-pair':
  arguments=['pair','--pairing-code',args.pairing_code]
 else:raise ValueError('No explicit local-test identity action')
 module=importlib.import_module('ahadiff.knowledge_platform.local_test_identity')
 previous=sys.argv
 try:
  sys.argv=['ahadiff.knowledge_platform.local_test_identity',*arguments]
  module.main()
 finally:sys.argv=previous

def run_owner_acceptance_identity(args):
 # No credential or pairing code in argv/environment/output. The approved
 # backend module independently validates the exact root, identity and DPAPI.
 if args.action=='owner-acceptance-initialize' and args.approve_owner_acceptance_identity:
  name='ahadiff.knowledge_platform.owner_identity'
  arguments=['--approve-owner-acceptance-identity','--port',str(args.port or 8766)]
 elif args.action=='owner-acceptance-pair':
  name='ahadiff.knowledge_platform.owner_pairing'
  arguments=['--owner-dpapi-identity',str(within('data/owner-acceptance/identity.json'))]
 else:raise ValueError('No explicit owner acceptance identity action')
 module=importlib.import_module(name)
 previous=sys.argv
 try:
  sys.argv=[name,*arguments]
  # owner_pairing reads its one-time code directly from the existing stdin.
  module.main()
 finally:sys.argv=previous

def main():
 args=parse_arguments()
 if sys.platform!='win32':raise RuntimeError('Windows runtime only')
 within(ROOT)
 validate_root()
 if args.action=='stop':
  if not within('data').is_dir():print('This package is not running.');return
  within('data/stop.request').write_text('stop\n',encoding='ascii');print('Stop requested for this package only.');return
 verify_distribution()
 if args.action in {'local-test-initialize','local-test-pair'}:
  env=isolated_environment();os.environ.clear();os.environ.update(env)
  run_local_test_identity(args)
  return
 if args.action in {'owner-acceptance-initialize','owner-acceptance-pair'}:
  env=isolated_environment();os.environ.clear();os.environ.update(env)
  run_owner_acceptance_identity(args)
  return
 global BROWSER, JOB
 BROWSER=browser_path()
 from windows_job import OwnedJob
 JOB=OwnedJob()
 env=isolated_environment();os.environ.clear();os.environ.update(env)
 result=diagnose(env)
 if args.action=='diagnostics':print(json.dumps(result,ensure_ascii=False,indent=2));return
 if args.action=='publish':publish(env);return
 start(env,local_test=args.action=='local-test-start',owner_acceptance=args.action=='owner-acceptance-start')

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
  if sys.platform=='win32':
   try:validate_root();write_json('reports/startup-error.json',report)
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
