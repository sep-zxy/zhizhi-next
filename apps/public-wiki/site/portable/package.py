"""Cloud-only assembly. Run through uv. Source snapshot stays exact; derived JS is recorded."""
import pathlib, shutil, json, hashlib, sys, subprocess, tarfile, base64
site=pathlib.Path(__file__).resolve().parent.parent
out=pathlib.Path(sys.argv[1]).resolve()
assert not out.exists(), 'Refuse existing component'
def copy(src,rel):
 target=out/rel;target.parent.mkdir(parents=True,exist_ok=True)
 if src.is_dir(): shutil.copytree(src,target,symlinks=False)
 else: shutil.copyfile(src,target)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
copy(site/'portable/render.mjs','render.mjs')
copy(site/'portable/publish.py','publish.py')
copy(site/'portable/runtime.generated.mjs','site/runtime.mjs')
copy(site/'portable/runtime.generated.mjs.provenance.json','compilation-provenance.json')
copy(pathlib.Path(sys.argv[2]),'resources.json')
for name in ['quartz.config.yaml','quartz.lock.json','package.json','package-lock.json','LICENSE.txt']:
 if (site/name).exists():copy(site/name,'site/'+name)
copy(site/'quartz/static','site/quartz/static')
copy(site/'quartz/.quartz-cache/fonts','site/quartz/.quartz-cache/fonts')
copy(site.parent/'docs/pruning-v1','docs/pruning-v1')
copy(site.parent/'docs/build-assets','docs/build-assets')
snapshot=json.loads((site/'.quartz/vendored-source-manifest.json').read_text())
copy(site/'.quartz/vendored-source-manifest.json','site/.quartz/vendored-source-manifest.json')
for file in snapshot['files']:copy(site/file,'site/'+file)
for name in snapshot['plugins']:copy(site/'.quartz/plugins'/name/'dist','site/.quartz/plugins/'+name+'/dist')
# The unused Typst compiler is never distributed. Keep original source/hash; derive dist only.
latex=out/'site/.quartz/plugins/latex/dist/index.js'
original=digest(latex)
text=latex.read_text();needle="import rehypeTypst from '@myriaddreamin/rehype-typst';"
assert text.count(needle)==1
latex.write_text(text.replace(needle,"function rehypeTypst() { throw new Error('Typst compilation is unavailable in runtime-only renderer'); }"))
derivation={'site/.quartz/plugins/latex/dist/index.js':{'original_sha256':original,'derived_sha256':digest(latex),'reason':'Configured engine is katex; unused Typst compiler fails closed'}}
lock=json.loads((site/'package-lock.json').read_text())
blocked=('esbuild','@esbuild/','lightningcss','sass','typescript','tsx','tsup','@myriaddreamin/','@parcel/watcher')
packages={}
required=set()
def require_package(name):
 rel='node_modules/'+name
 if rel in required:return
 required.add(rel)
 for child in lock['packages'].get(rel,{}).get('dependencies',{}):require_package(child)
for name in ['@quartz-community/utils','hast-util-from-html']:require_package(name)
for rel,info in lock['packages'].items():
 if not rel or ((info.get('dev') or info.get('devOptional')) and rel not in required):continue
 name=rel.split('node_modules/')[-1]
 if any(name==x or name.startswith(x) for x in blocked):continue
 source=site/rel
 if not source.is_dir():continue
 # Copy only the package's own files; nested dependencies are handled by lock entries.
 target=out/'site'/rel;target.mkdir(parents=True,exist_ok=True)
 for item in source.iterdir():
  if item.name in ['node_modules','.bin']:continue
  if item.is_symlink():raise RuntimeError('Dependency symlink: '+str(item))
  copy(item,str((target/item.name).relative_to(out)))
 packages[rel]={'version':info.get('version'),'integrity':info.get('integrity')}
(out/'dependency-provenance.json').write_text(json.dumps(packages,indent=2)+'\n')
(out/'derived-artifacts.json').write_text(json.dumps(derivation,indent=2)+'\n')
# Strip package scripts so this distribution cannot accidentally expose development launchers.
rootpkg=out/'site/package.json'
rootdata=json.loads(rootpkg.read_text());rootdata['scripts']={};rootpkg.write_text(json.dumps(rootdata,indent=2)+'\n')
component={'name':'zhizhi-wiki-renderer','version':'1.0.0','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=site,text=True).strip(),'entrypoints':{'render':'render.mjs','publish':'publish.py'},'files':{},'licenses':['site/LICENSE.txt','docs/pruning-v1/licenses.json','docs/build-assets/NotoSansSC-OFL.txt'],'runtime_requirements':{'node':'>=22.23.3','tested_node_versions':['22.23.3','24.19.0'],'platform':'linux-x64-test; Windows artifact assembled separately','python_gate':'Domain readonly final certificate verification required'},'known_limits':['No Typst compiler. Unknown code resources fail closed.','Linux fixture tests do not establish Windows compatibility.','Output is unverified until independent Python publication gate succeeds.']}
component['licenses']=[p.relative_to(out).as_posix() for p in sorted(out.rglob('*')) if p.is_file() and any(word in p.name.lower() for word in ['license','licence','notice','ofl','copying','copyright'])]
component['files']={p.relative_to(out).as_posix():digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
(out/'component.json').write_text(json.dumps(component,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'directory':str(out),'files':len(component['files']),'bytes':sum(p.stat().st_size for p in out.rglob('*') if p.is_file())}))
