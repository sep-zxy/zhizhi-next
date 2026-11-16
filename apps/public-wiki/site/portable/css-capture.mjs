import * as real from 'lightningcss'
import {save} from './resource-store.mjs'
export const Features=real.Features
export function transform(options) { const res=real.transform(options);save('css',options,res.code.toString());return res }
