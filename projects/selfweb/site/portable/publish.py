"""Runtime-only local publication: fixed Git projection -> Node render -> readonly final gate.
No deployment, accounts, model calls, dependency installation or compilation.
"""
import argparse,json,os,pathlib,subprocess
from ahadiff.knowledge_platform.publish.projection import project_git_revision
from ahadiff.knowledge_platform.publish.revalidate import bind_domain_publication,revalidate_public_projection
from ahadiff.knowledge_platform.publish.artifacts import validate_public_artifacts
from ahadiff.knowledge_platform.publish.quartz import quartz_generated_files

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 for name in ['node','repository','domain-database','policy','template-assets','staging','output','report']:
  parser.add_argument('--'+name,required=True,type=pathlib.Path)
 for name in ['revision','owner-id','public-base-url']:parser.add_argument('--'+name,required=True)
 parser.add_argument('--domain-policy-version',default='1')
 args=parser.parse_args()
 child_environment={}
 if 'ZHIZHI_PORTABLE_ROOT' in os.environ or 'ZHIZHI_PORTABLE_STATE' in os.environ:
  from ahadiff.knowledge_platform.portable import portable_root,require_portable_path,portable_child_environment
  root=portable_root()
  def confined(value,*,state=False):
   # Reject even in-root redirects, before resolve erases evidence of a junction.
   absolute=pathlib.Path(os.path.abspath(value))
   for part in [absolute,*absolute.parents]:
    if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('Portable symlinks and junctions are forbidden')
   return require_portable_path(absolute,state=state)
  for name in ['node','repository','domain_database','policy','template_assets']:
   setattr(args,name,confined(getattr(args,name)))
  for name in ['staging','output','report']:setattr(args,name,confined(getattr(args,name),state=True))
  confined(pathlib.Path(__file__))
  if args.node!=confined(root/'runtime/node/node.exe'):raise ValueError('Only the packaged official Node runtime is allowed')
  child_environment=portable_child_environment()
  for name,value in child_environment.items():confined(os.environ.get(name,value))
 staging,output,report=[p.resolve() for p in [args.staging,args.output,args.report]]
 component=pathlib.Path(__file__).resolve().parent
 if any(p.exists() for p in [staging,output,report]):raise ValueError('Staging, output and report must all be new')
 for left,right in [(staging,output),(report,output),(report,staging),(report,component),(staging,component),(output,component)]:
  if left.is_relative_to(right) or right.is_relative_to(left):raise ValueError('Publication paths must be independent')
 policy=json.loads(args.policy.read_text(encoding='utf-8-sig'))
 template=json.loads(args.template_assets.read_text(encoding='utf-8-sig'))
 domain,resolver=bind_domain_publication(args.domain_database,args.owner_id,args.repository,domain_policy_version=args.domain_policy_version)
 try:
  projected=project_git_revision(args.repository,args.revision,staging,policy=policy,publication_resolver=resolver)
  assert domain.store.connection.total_changes==0
 finally:domain.close()
 # Renderer sees only reviewed public material. Never inherit credentials or Node loader options.
 env={k:os.environ[k] for k in ['PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','LANG','LC_ALL'] if k in os.environ}
 env.update(child_environment)
 env['TZ']='UTC'
 output.parent.mkdir(parents=True,exist_ok=True)
 subprocess.run([str(args.node.resolve()),str(component/'render.mjs'),'--staging',str(staging),'--output',str(output),'--public-base-url',args.public_base_url],env=env,check=True)
 manifest=projected['public_manifest']
 if json.loads((staging/'publish-manifest.json').read_text())!=manifest:raise ValueError('Staging manifest changed')
 domain,resolver=bind_domain_publication(args.domain_database,args.owner_id,args.repository,domain_policy_version=args.domain_policy_version)
 try:
  revalidate_public_projection(manifest,repository=args.repository,policy=policy,publication_resolver=resolver)
  assert domain.store.connection.total_changes==0
 finally:domain.close()
 result=validate_public_artifacts(output,manifest,policy=policy,forbidden_fingerprints=policy.get('forbidden_fingerprints',[]),trusted_template_assets=template['assets'],generated_files=quartz_generated_files(staging))
 report.parent.mkdir(parents=True,exist_ok=True)
 report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 print('Local publication passed final readonly gate. No deployment performed.')
if __name__=='__main__':main()
