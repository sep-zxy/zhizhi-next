import * as real from 'esbuild'
import {save} from './resource-store.mjs'
export async function transform(input,options) { const res=await real.transform(input,options);save('js',[input,options],res.code);return res }
export function build() { throw new Error('Portable parser must be single threaded') }
export default {transform,build}
