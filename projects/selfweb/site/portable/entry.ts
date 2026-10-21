import cfg from '../quartz'
import {parseMarkdown} from '../quartz/processors/parse'
import {filterContent} from '../quartz/processors/filter'
import {emitContent} from '../quartz/processors/emit'
import {glob} from '../quartz/util/glob'
import {slugifyFilePath,type FilePath} from '../quartz/util/path'
import {publicBuildId} from '../quartz/util/publicBuildId'
import type {BuildCtx} from '../quartz/util/ctx'
import path from 'node:path'
export async function render(directory:string,output:string) {
 const allFiles=await glob('**/*.*',directory,cfg.configuration.ignorePatterns)
 const ctx:BuildCtx={buildId:publicBuildId(directory,cfg.configuration),cfg,
 argv:{directory,output,verbose:false,serve:false,watch:false,port:0,wsPort:0,concurrency:1},
 allFiles,allSlugs:allFiles.map(fp=>slugifyFilePath(fp as FilePath)),incremental:false,virtualPages:[]}
 const paths=allFiles.filter(fp=>fp.endsWith('.md')).sort().map(fp=>path.join(directory,fp).split(path.sep).join('/') as FilePath)
 await emitContent(ctx,filterContent(ctx,await parseMarkdown(ctx,paths)))
}
