// Cloud-only compiler; excluded from the runtime component.
import fs from 'node:fs/promises'
import path from 'node:path'
import {createHash} from 'node:crypto'
import {createRequire} from 'node:module'
const require=createRequire(import.meta.url)
import * as esbuild from 'esbuild'
import {sassPlugin} from 'esbuild-sass-plugin'
import {Features} from 'lightningcss'
const mode=process.argv[2]
if(!['capture','runtime'].includes(mode)) throw new Error('Expected capture or runtime')
const output=path.resolve(process.argv[3]),portable=path.resolve('portable')
await fs.mkdir(path.dirname(output),{recursive:true})
const built=await esbuild.build({entryPoints:['portable/entry.ts'],outfile:output,bundle:true,keepNames:true,platform:'node',format:'esm',jsx:'automatic',jsxImportSource:'preact',packages:'external',metafile:true,
 plugins:[{name:'portable-resources',setup(build){
 build.onResolve({filter:/^(esbuild|lightningcss)$/},args=>{
 if(args.importer.endsWith('-capture.mjs'))return {path:args.path,external:true}
 return {path:path.join(portable,`${args.path==='esbuild'?'esbuild':'css'}-${mode}.mjs`)}
 })
 build.onLoad({filter:/resource-store\.mjs$/},async args=>({contents:(await fs.readFile(args.path,'utf8')).replace("process.env.QUARTZ_CAPTURE_RESOURCES === '1'", mode==='capture'?'true':'false'),loader:'js'}))
 build.onLoad({filter:/css-runtime\.mjs$/},async args=>({contents:(await fs.readFile(args.path,'utf8')).replace('MediaQueries:2',`MediaQueries:${Features.MediaQueries}`),loader:'js'}))
 build.onLoad({filter:/componentResources\.ts$/},async args=>{
 let source=await fs.readFile(args.path,'utf8')
 source=`import {guard as portableGuard} from ${JSON.stringify(path.join(portable,'resource-store.mjs'))}\n`+source
 if(source.split('addGlobalPageResources(ctx, componentResources)\n').length!==2 || source.split('} catch {\n          output = cssResource.content').length!==2)throw new Error('Upstream emitter changed: review portable derivation')
 source=source.replace('addGlobalPageResources(ctx, componentResources)\n','addGlobalPageResources(ctx, componentResources)\n      portableGuard([componentResources, resources])\n')
 source=source.replace('} catch {\n          output = cssResource.content','} catch (error) {\n          throw error\n          output = cssResource.content')
 return {contents:source,loader:'ts'}
 })
 }},sassPlugin({type:'css-text',cssImports:true}),{name:'inline',setup(build){build.onLoad({filter:/\.inline\.(ts|js)$/},async args=>{
 const contents=(await fs.readFile(args.path,'utf8')).replace('export default','').replace('export','')
 const result=await esbuild.build({stdin:{contents,loader:'ts',resolveDir:path.dirname(args.path),sourcefile:args.path},write:false,bundle:true,minify:true,platform:'browser',format:'esm'})
 return {contents:result.outputFiles[0].text,loader:'text'}
 })}}]})

const inputs={}
for(const name of Object.keys(built.metafile.inputs).sort())inputs[name]=createHash('sha256').update(await fs.readFile(name)).digest('hex')
await fs.writeFile(output+'.provenance.json',JSON.stringify({mode,compilers:{esbuild:esbuild.version,lightningcss:JSON.parse(await fs.readFile('node_modules/lightningcss/package.json','utf8')).version,sass:JSON.parse(await fs.readFile('node_modules/sass/package.json','utf8')).version},inputs,output_sha256:createHash('sha256').update(await fs.readFile(output)).digest('hex')},null,2)+'\n')
