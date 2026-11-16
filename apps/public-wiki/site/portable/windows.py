"""Cloud-only conversion of tested component to exact locked Windows x64 native payloads."""
import pathlib,json,hashlib,shutil,tarfile,base64,sys
source,target,native=map(pathlib.Path,sys.argv[1:4])
assert not target.exists();shutil.copytree(source,target)
lock=json.loads((target/'site/package-lock.json').read_text())
modules=target/'site/node_modules';removed=[]
for scope in ['@img','@napi-rs']:
 for package in (modules/scope).iterdir():
  if any(token in package.name for token in ['linux','darwin','freebsd','android','wasm','win32']):
   removed.append(str(package.relative_to(target)));shutil.rmtree(package)
records=[]
for record in json.loads((native/'pack.json').read_text()):
 name=record['name'];version=record['version'];relative='node_modules/'+name
 assert name in ['@img/sharp-win32-x64','@napi-rs/simple-git-win32-x64-msvc']
 expected=lock['packages'][relative];archive=native/record['filename']
 assert version==expected['version']
 integrity='sha512-'+base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
 assert integrity==expected['integrity']==record['integrity']
 destination=modules/name;destination.mkdir(parents=True)
 with tarfile.open(archive) as tar:
  for member in tar.getmembers():
   p=pathlib.PurePosixPath(member.name)
   assert p.parts[0]=='package' and '..' not in p.parts and not member.issym() and not member.islnk()
   if member.isdir():continue
   assert member.isfile()
   output=destination.joinpath(*p.parts[1:]);output.parent.mkdir(parents=True,exist_ok=True)
   output.write_bytes(tar.extractfile(member).read())
 records.append({'name':name,'version':version,'integrity':integrity,'official_source':expected['resolved']})
(target/'windows-native-provenance.json').write_text(json.dumps({'installed':records,'removed_other_os':removed},indent=2)+'\n')
dependencies=json.loads((target/'dependency-provenance.json').read_text())
for relative in removed:dependencies.pop(relative.removeprefix('site/'),None)
for record in records:dependencies['node_modules/'+record['name']]={'version':record['version'],'integrity':record['integrity']}
(target/'dependency-provenance.json').write_text(json.dumps(dependencies,indent=2)+'\n')
component=json.loads((target/'component.json').read_text());component['runtime_requirements']['platform']='win32-x64'
component['runtime_requirements']['native_packages']=records
component['licenses']=[p.relative_to(target).as_posix() for p in sorted(target.rglob('*')) if p.is_file() and any(word in p.name.lower() for word in ['license','licence','notice','ofl','copying','copyright'])]
assert all((target/p).is_file() for p in component['licenses'])
component['files']={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file() and p!=target/'component.json'}
(target/'component.json').write_text(json.dumps(component,ensure_ascii=False,indent=2)+'\n')
print('Windows component:',target,len(component['files']))
