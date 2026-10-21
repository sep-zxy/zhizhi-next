"""Cloud unit tests for portable publication boundaries; no credentials or builds."""
import importlib.util,pathlib,tempfile,os,sys,json,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('portable_publish',pathlib.Path(__file__).with_name('publish.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class Validated(Exception):pass
class Paths(unittest.TestCase):
 def test_boundaries(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp)/'bundle';root.mkdir();state=root/'data';state.mkdir();component=root/'app/wiki';component.mkdir(parents=True);(component/'publish.py').write_text('')
   (root/'runtime/node').mkdir(parents=True);(root/'runtime/node/node.exe').write_text('')
   for name in ['policy','template']:(root/(name+'.json')).write_text('{}')
   env={'ZHIZHI_PORTABLE_ROOT':str(root),'ZHIZHI_PORTABLE_STATE':str(state)}
   for key in ['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME']:env[key]=str(root/'cache')
   params={'node':root/'runtime/node/node.exe','repository':state/'vault.git','domain-database':state/'domain.db','policy':root/'policy.json','template-assets':root/'template.json','staging':state/'stage','output':state/'out','report':state/'report.json','revision':'a'*40,'owner-id':'fixture','public-base-url':'https://example.org'}
   def check(changes={},exception=ValueError,environment=None):
    args=['publish.py']
    for key,value in {**params,**changes}.items():args+=['--'+key,str(value)]
    with patch.dict(os.environ,env if environment is None else environment,clear=True),patch.object(sys,'argv',args),patch.object(module,'__file__',str(component/'publish.py')),patch.object(module,'bind_domain_publication',side_effect=Validated):
     with self.assertRaises(exception):module.main()
   check(exception=Validated)
   for key in ['node','repository','domain-database','policy','template-assets','staging','output','report']:check({key:pathlib.Path(tmp)/'outside'})
   check({'node':root/'other.exe'})
   check({'report':component/'report.json'})
   check({'output':root/'outside-state'})
   check(environment={**env,'ZHIZHI_PORTABLE_ROOT':''})
   check(environment={'ZHIZHI_PORTABLE_STATE':str(state)})
   link=root/'link';link.symlink_to(state,target_is_directory=True);check({'staging':link/'staging'})
   check(environment={**env,'HOME':str(link)})
if __name__=='__main__':unittest.main()
