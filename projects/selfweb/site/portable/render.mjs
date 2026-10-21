// Runtime-only entry. The Python publisher owns projection review and final readonly gate.
import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
const root=path.dirname(fileURLToPath(import.meta.url))
const args=process.argv.slice(2),opts={}
for(let i=0;i<args.length;i+=2){
 if(!['--staging','--output','--public-base-url'].includes(args[i])||!args[i+1]||opts[args[i]])throw new Error('Invalid renderer arguments')
 opts[args[i]]=args[i+1]
}
if(Object.keys(opts).length!==3)throw new Error('Require staging, output and public-base-url')
function noRedirect(value) {
 let current=path.resolve(value)
 while(true){
  try { if(fs.lstatSync(current).isSymbolicLink())throw new Error('Portable symlink or junction rejected') }
  catch(error){if(error.code!=='ENOENT')throw error}
  const parent=path.dirname(current);if(parent===current)break;current=parent
 }
 return path.resolve(value)
}
if('ZHIZHI_PORTABLE_ROOT' in process.env || 'ZHIZHI_PORTABLE_STATE' in process.env){
 if(!process.env.ZHIZHI_PORTABLE_ROOT||!process.env.ZHIZHI_PORTABLE_STATE)throw new Error('Portable root and state required')
 const packageRoot=fs.realpathSync(noRedirect(process.env.ZHIZHI_PORTABLE_ROOT))
 const state=fs.realpathSync(noRedirect(process.env.ZHIZHI_PORTABLE_STATE))
 const same=(a,b)=>process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b
 const inside=(a,b)=>same(a,b)|| (process.platform==='win32'?a.toLowerCase():a).startsWith((process.platform==='win32'?b.toLowerCase():b)+path.sep)
 if(!inside(state,packageRoot)||!inside(root,packageRoot)||!same(fs.realpathSync(process.execPath),path.join(packageRoot,'runtime/node/node.exe')))throw new Error('Packaged runtime and state required')
 for(const value of [opts['--staging'],opts['--output']])if(!inside(noRedirect(value),state))throw new Error('Publication path escapes portable state')
 for(const key of ['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME'])if(!process.env[key]||!inside(noRedirect(process.env[key]),packageRoot))throw new Error('Portable environment path rejected: '+key)
}
const staging=fs.realpathSync(opts['--staging']),output=path.join(fs.realpathSync(path.dirname(path.resolve(opts['--output']))),path.basename(opts['--output']))
const within=(a,b)=>a===b||a.startsWith(b+path.sep)
if(within(output,staging)||within(staging,output)||within(output,root)||fs.existsSync(output))throw new Error('Output must be a new independent directory')
if(!fs.existsSync(path.join(staging,'publish-manifest.json')))throw new Error('Audited projection manifest required')
const component=JSON.parse(fs.readFileSync(path.join(root,'component.json'),'utf8'))
function componentFiles(dir,prefix='') {
 const files=[]
 for(const item of fs.readdirSync(dir,{withFileTypes:true})){
  const relative=prefix+item.name
  if(item.isSymbolicLink())throw new Error('Component symlink rejected')
  if(item.isDirectory())files.push(...componentFiles(path.join(dir,item.name),relative+'/'))
  else if(relative!=='component.json')files.push(relative)
 }
 return files.sort()
}
if(JSON.stringify(componentFiles(root))!==JSON.stringify(Object.keys(component.files).sort()))throw new Error('Unexpected or missing component files')
for(const [relative,expected]of Object.entries(component.files)){
 if(path.isAbsolute(relative)||relative.split('/').includes('..'))throw new Error('Unsafe component path')
 const file=path.join(root,relative)
 if(fs.lstatSync(file).isSymbolicLink()||!within(fs.realpathSync(file),root))throw new Error('Component symlink rejected')
 if(createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==expected)throw new Error('Component integrity mismatch: '+relative)
}
function inspect(dir){for(const item of fs.readdirSync(dir,{withFileTypes:true})){
 const file=path.join(dir,item.name)
 if(item.isSymbolicLink())throw new Error('Projection symlink rejected')
 if(item.isDirectory())inspect(file)
 else if(/\.(?:js|mjs|cjs|ts|tsx|css|scss|html?)$/i.test(item.name))throw new Error('Dynamic code input rejected')
 else if(item.name.endsWith('.md')&&/<\s*(?:script|style|iframe|object|embed)\b|\bon\w+\s*=|javascript\s*:/i.test(fs.readFileSync(file,'utf8')))throw new Error('Executable Markdown rejected')
}}
inspect(staging)
process.chdir(path.join(root,'site'))
process.env.QUARTZ_OFFLINE_PLUGINS='1'
process.env.QUARTZ_PUBLIC_BASE_URL=opts['--public-base-url']
process.env.QUARTZ_RESOURCE_STORE=path.join(root,'resources.json')
delete process.env.QUARTZ_CAPTURE_RESOURCES
process.env.TZ='UTC'
// Any unexpected network request is a build failure, never an online fallback.
globalThis.fetch=async()=>{throw new Error('Portable renderer forbids network fetch')}
const {render}=await import('./site/runtime.mjs')
await render(staging.split(path.sep).join('/'),output.split(path.sep).join('/'))
console.log('Rendered unverified output. Final Python readonly publication gate is still mandatory.')
