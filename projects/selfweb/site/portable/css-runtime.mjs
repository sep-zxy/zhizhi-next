import {lookup} from './resource-store.mjs'
export const Features={MediaQueries:2}
export function transform(options) { return {code:Buffer.from(lookup('css',options))} }
