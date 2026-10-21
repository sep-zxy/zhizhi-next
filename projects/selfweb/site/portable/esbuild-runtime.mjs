import {lookup} from './resource-store.mjs'
export async function transform(input,options) { return {code:lookup('js',[input,options])} }
export function build() { throw new Error('Runtime compilation is forbidden') }
export default {transform,build}
