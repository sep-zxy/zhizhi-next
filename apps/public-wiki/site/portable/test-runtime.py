"""Cloud-only black-box acceptance; never shipped in Windows runtime."""
import pathlib,subprocess,json,hashlib,sys,os,shutil
component,a,b,work=map(pathlib.Path,sys.argv[1:5]);work.mkdir(parents=True,exist_ok=False)
def run(staging,name,ok=True,env=None):
 result=subprocess.run(['node',str(component/'render.mjs'),'--staging',str(staging),'--output',str(work/name),'--public-base-url','https://example.org'],capture_output=True,text=True,env=env)
 (work/(name+'.log')).write_text(result.stdout+result.stderr)
 assert (result.returncode==0)==ok,(name,result.stdout,result.stderr)
 return work/name
outputs=[run(a,'content-a'),run(b,'content-b')]
sets=[{p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()} for out in outputs]
assert sets[0]!=sets[1]
fixed=[p for p in sets[0] if pathlib.Path(p).suffix in ['.css','.js','.woff','.woff2','.ttf','.svg','.png','.ico']]
assert all(sets[0][p]==sets[1][p] for p in fixed)
# Inputs and output must fail closed, without editing the accepted fixtures.
run(a,'content-a',False)
evil=work/'evil';shutil.copytree(a,evil);(evil/'payload.js').write_text('console.log(1)');run(evil,'reject-code',False)
(evil/'payload.js').unlink();(evil/'payload.md').write_text('<script>alert(1)</script>');run(evil,'reject-markdown',False)
# Unlisted files cannot influence module resolution.
extra=component/'unexpected.mjs';extra.write_text('export default 1')
try:run(a,'reject-extra',False)
finally:extra.unlink()
# Even a coherently modified store cannot activate capture mode in the runtime.
store=component/'resources.json';manifest=component/'component.json'
original_store=store.read_bytes();original_manifest=manifest.read_bytes()
try:
 store.write_text('{}');m=json.loads(original_manifest);m['files']['resources.json']=hashlib.sha256(store.read_bytes()).hexdigest();manifest.write_text(json.dumps(m))
 run(a,'reject-unknown-resource',False,{**os.environ,'QUARTZ_CAPTURE_RESOURCES':'1'})
 assert store.read_text()=='{}'
finally:store.write_bytes(original_store);manifest.write_bytes(original_manifest)
for p in (component/'site/node_modules').rglob('package.json'):
 name=json.loads(p.read_text()).get('name','')
 assert not any(x in name for x in ['esbuild','lightningcss','typescript','typst-ts-node-compiler'])
 assert name not in ['sass','sass-embedded','tsx','tsup']
report={'positive_dynamic_builds':2,'negative_cases':5,'files':[len(s) for s in sets],'fixed_resources':len(fixed),'different_content':True,'compiler_packages_absent':True,'unknown_resources_fail_closed':True,'windows_execution':'not_run'}
(work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
