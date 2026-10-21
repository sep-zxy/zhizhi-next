import importlib.util
from pathlib import Path
import tempfile
import unittest
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

if __name__=='__main__':unittest.main()
