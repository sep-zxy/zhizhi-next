import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile
spec=importlib.util.spec_from_file_location('build_bundle',Path(__file__).with_name('build_bundle.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class AssemblyTests(unittest.TestCase):
 def test_path_traversal_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'bad.zip'
   with zipfile.ZipFile(p,'w') as z:z.writestr('../escape','bad')
   with self.assertRaises(ValueError):m.safe_extract(p,Path(d)/'out')
   self.assertFalse((Path(d)/'escape').exists())
 def test_case_collision_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'bad.zip'
   with zipfile.ZipFile(p,'w') as z:z.writestr('A','one');z.writestr('a','two')
   with self.assertRaises(ValueError):m.safe_extract(p,Path(d)/'out')
 def test_windows_dependency_closure(self):
  repo=Path(__file__).resolve().parents[2]
  plan=m.wheel_plan(repo/'projects/growth/uv.lock')
  names={p['name'] for p in plan}
  self.assertTrue({'pywin32','tree-sitter','cryptography','webvtt-py','wechatpy'}<=names)
  self.assertNotIn('torch',names)
  self.assertTrue(all('win_amd64' in p['filename'] or 'none-any' in p['filename'] for p in plan))
 def test_existing_wrong_hash_never_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'file';p.write_bytes(b'old')
   with self.assertRaises(ValueError):m.download('https://www.python.org/file',p,'0'*64)
   self.assertEqual(p.read_bytes(),b'old')

# These are cloud-only tests. Windows smoke checks remain separate.
class RuntimeContractTests(unittest.TestCase):
 def load_launcher(self):
  spec=importlib.util.spec_from_file_location('portable_launcher',Path(__file__).parent/'runtime/launcher.py')
  mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
 def test_manifest_detects_changed_file(self):
  import json
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);(mod.ROOT/'provenance').mkdir();(mod.ROOT/'app').mkdir();(mod.ROOT/'runtime').mkdir()
   f=mod.ROOT/'app/program.js';f.write_text('original')
   (mod.ROOT/'provenance/distribution.json').write_text(json.dumps({'files':{'app/program.js':m.sha256(f)}}))
   mod.verify_distribution();f.write_text('changed')
   with self.assertRaises(RuntimeError):mod.verify_distribution()
 def test_manifest_detects_extra_code(self):
  import json
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);(mod.ROOT/'provenance').mkdir();(mod.ROOT/'app').mkdir();(mod.ROOT/'runtime').mkdir()
   (mod.ROOT/'provenance/distribution.json').write_text(json.dumps({'files':{}}))
   (mod.ROOT/'app/extra.js').write_text('x')
   with self.assertRaises(RuntimeError):mod.verify_distribution()
 def test_reject_outside_path(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d)
   with self.assertRaises(ValueError):mod.within('../outside')
 def test_reject_link(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);(mod.ROOT/'real').mkdir();(mod.ROOT/'link').symlink_to(mod.ROOT/'real')
   with self.assertRaises(ValueError):mod.within('link/file')
 def test_reject_unknown_windows_reparse_tag_on_every_existing_ancestor(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);parent=mod.ROOT/'data';parent.mkdir();file=parent/'state.json';file.write_text('synthetic')
   original=Path.lstat
   for suspect in [file,parent,mod.ROOT,mod.ROOT.parent]:
    def attributes(path):
     if path==suspect:return SimpleNamespace(st_file_attributes=mod.stat.FILE_ATTRIBUTE_REPARSE_POINT,st_reparse_tag=0x8000FFFE)
     return original(path)
    with self.subTest(suspect=suspect),patch.object(Path,'lstat',attributes),\
         patch.object(Path,'is_symlink',return_value=False),\
         patch.object(Path,'is_junction',return_value=False,create=True),\
         self.assertRaisesRegex(ValueError,'reparse'):
     mod.within(file)
 def test_uninspectable_existing_ancestor_fails_closed(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);parent=mod.ROOT/'data';parent.mkdir();original=Path.lstat
   for error in [PermissionError('synthetic denied'),OSError('synthetic unavailable')]:
    def attributes(path):
     if path==parent:raise error
     return original(path)
    with self.subTest(error=type(error).__name__),patch.object(Path,'lstat',attributes),self.assertRaises(OSError):
     mod.within(parent/'not-created.json')
 def test_new_descendant_is_allowed_only_after_ancestors_are_checked(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);expected=mod.ROOT/'data'/'not-created.json'
   self.assertEqual(mod.within(expected),expected)
   self.assertFalse(expected.parent.exists())
 def test_versioned_git_root_stays_inside_original_authorized_root(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.EXPECTED_ROOT=Path(d)/'approved';mod.EXPECTED_ROOT.mkdir()
   mod.ROOT=mod.EXPECTED_ROOT;mod.validate_root()
   mod.ROOT=mod.EXPECTED_ROOT/'git-delivery'/'releases'/('a'*12+'-12-1')
   mod.ROOT.mkdir(parents=True);mod.validate_root()
   for path in [mod.EXPECTED_ROOT/'arbitrary',mod.EXPECTED_ROOT/'other-clone'/'releases'/('a'*12+'-12-1'),
                Path(d)/'outside'/'releases'/('a'*12+'-12-1'),
                mod.EXPECTED_ROOT/'releases'/'not-a-pinned-version']:
    mod.ROOT=path
    with self.assertRaises(RuntimeError):mod.validate_root()
 def test_versioned_git_root_rejects_links(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.EXPECTED_ROOT=Path(d)/'approved';mod.EXPECTED_ROOT.mkdir()
   outside=Path(d)/'outside';outside.mkdir()
   (mod.EXPECTED_ROOT/'linked').symlink_to(outside)
   mod.ROOT=mod.EXPECTED_ROOT/'linked'/'releases'/('a'*12+'-12-1')
   with self.assertRaises(ValueError):mod.validate_root()
 def test_platform_and_viewer_use_distinct_health_contracts(self):
  mod=self.load_launcher();proc=Mock();proc.poll.return_value=None
  for service,path,body in [('platform','/health',b'{"service":"zhizhi-knowledge","status":"ready","authentication_status":"configured"}'),
                            ('viewer','/healthz',b'{"ok":true}')]:
   with self.subTest(service=service),patch.object(mod.urllib.request,'urlopen') as opening:
    response=opening.return_value.__enter__.return_value
    response.status=200;response.read.return_value=body
    mod.await_health(proc,8766,service)
    opening.assert_called_once_with('http://127.0.0.1:8766'+path,timeout=1)
 def test_wrong_service_body_or_degraded_health_is_not_ready(self):
  mod=self.load_launcher();proc=Mock();proc.poll.return_value=None
  for body in [b'{"ok":true}',b'{"service":"zhizhi-knowledge","status":"degraded"}']:
   with self.subTest(body=body),patch.object(mod.urllib.request,'urlopen') as opening,patch.object(mod.time,'sleep'):
    response=opening.return_value.__enter__.return_value
    response.status=200;response.read.return_value=body
    with self.assertRaisesRegex(RuntimeError,'startup timed out'):mod.await_health(proc,8766,'platform')
 def test_waiting_auth_health_only_accepted_for_explicit_identity_free_shell(self):
  mod=self.load_launcher();proc=Mock();proc.poll.return_value=None
  with patch.object(mod.urllib.request,'urlopen') as opening,patch.object(mod.time,'sleep'):
   response=opening.return_value.__enter__.return_value
   response.status=200
   response.read.return_value=b'{"service":"zhizhi-knowledge","status":"waiting_auth","authentication_status":"waiting_auth"}'
   mod.await_health(proc,8766,'platform','waiting_auth')
   with self.assertRaisesRegex(RuntimeError,'startup timed out'):mod.await_health(proc,8766,'platform','configured')
 def test_identity_free_shell_command_is_credential_free_and_no_workers(self):
  mod=self.load_launcher()
  with tempfile.TemporaryDirectory() as d:
   mod.ROOT=Path(d);(mod.ROOT/'app/wiki-private').mkdir(parents=True)
   (mod.ROOT/'app/wiki-private/index.html').write_text('synthetic fixture')
   args,port,mode=mod.platform_command({'owner':None,'providers':[],'run_worker':False,'auto_commit':False})
   self.assertEqual(mode,'waiting_auth');self.assertEqual(port,8766)
   self.assertIn('--private-web-root',args)
   for flag in ['--owner','--identities-file','--run-worker','--provider','--model-config','--auto-commit']:
    self.assertNotIn(flag,args)
   for field,value in [('owner','someone'),('run_worker',True),('model_config','model.json'),
                       ('channel_config','channel.json'),('desktop_config','desktop.json'),
                       ('pi_environment_file','pi.json'),('providers',['codex']),('auto_commit',True)]:
    with self.subTest(field=field),self.assertRaisesRegex(ValueError,'Identity-free'):
     mod.platform_command({field:value})

if __name__=='__main__':unittest.main()
