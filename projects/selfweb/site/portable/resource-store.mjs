import fs from 'node:fs'
import {createHash} from 'node:crypto'
const file = process.env.QUARTZ_RESOURCE_STORE
if (!file) throw new Error('Missing compiled resource store')
const capture = process.env.QUARTZ_CAPTURE_RESOURCES === '1'
const store = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {}
const key = (kind,input) => kind + ':' + createHash('sha256').update(JSON.stringify(input)).digest('hex')
export function lookup(kind,input) {
 const k=key(kind,input)
 if (!(k in store)) throw new Error('Unapproved dynamic code resource: '+k)
 return store[k]
}
export function save(kind,input,code) {
 store[key(kind,input)]=code
 fs.writeFileSync(file,JSON.stringify(store,null,2)+'\n')
 return code
}
export function guard(input) {
 const serial=JSON.parse(JSON.stringify(input,(_,v)=>v instanceof Set?[...v]:v))
 if(capture) save('resources',serial,true)
 else lookup('resources',serial)
}
