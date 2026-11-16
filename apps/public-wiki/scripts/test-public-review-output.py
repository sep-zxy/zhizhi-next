"""Validate real Quartz output with a separately rendered synthetic template baseline.
Not a browser screenshot, production publication approval or real-user E2E.
"""
import argparse
import hashlib
import json
import shutil
import tempfile
from ahadiff.knowledge_platform.publish.projection import ProjectionError
from pathlib import Path
from html.parser import HTMLParser
from ahadiff.knowledge_platform.publish.artifacts import validate_public_artifacts
from ahadiff.knowledge_platform.publish.quartz import quartz_generated_files

p=argparse.ArgumentParser()
p.add_argument('--output',type=Path,required=True)
p.add_argument('--baseline',type=Path,required=True)
p.add_argument('--staging',type=Path,required=True)
p.add_argument('--report',type=Path,required=True)
a=p.parse_args()
assert a.output.resolve()!=a.baseline.resolve()
manifest=json.loads((a.staging/'publish-manifest.json').read_text())
assets={f.relative_to(a.baseline).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in a.baseline.rglob('*') if f.is_file() and f.suffix in {'.css','.js','.woff','.woff2','.ttf','.svg','.png','.ico'}}
policy={'sources':{'sqlite-doc':{'url':'https://www.sqlite.org/transactional.html','title':'SQLite 官方事务说明（测试引用）'}},'assets':{}}
result=validate_public_artifacts(a.output,manifest,forbidden_fingerprints=['PRIVATE_TITLE_SENTINEL','PRIVATE_ORIGINAL_SENTINEL','PRIVATE_TOOL_PERMISSION_SENTINEL','PRIVATE_ACCOUNT_SENTINEL','UNREVIEWED_TITLE_SENTINEL','INELIGIBLE_TITLE_SENTINEL'],trusted_template_assets=assets,generated_files=quartz_generated_files(a.staging),policy=policy)
class DOM(HTMLParser):
 def __init__(self): super().__init__();self.tags=[];self.text=[]
 def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))
 def handle_data(self,text): self.text.append(text)
home=DOM();home.feed((a.output/'index.html').read_text())
article=DOM();article.feed((a.output/'sqlite-cas.html').read_text())
assert any(tag=='input' and attrs.get('name')=='search' for tag,attrs in home.tags)
assert any(attrs.get('id')=='categories' for _,attrs in home.tags)
assert sum('public-entry-row' in attrs.get('class','').split() for _,attrs in home.tags)==8
assert '公开来源' in article.text and '相关条目与反链' in article.text
assert any(tag=='a' and attrs.get('href')=='https://www.sqlite.org/transactional.html' for tag,attrs in article.tags)
assert any(tag=='a' and '#1-各自保证什么' in attrs.get('href','') for tag,attrs in article.tags)
assert '引用本页' in ''.join(article.text)
with tempfile.TemporaryDirectory(prefix='public-leak-regression-') as temp:
    altered=Path(temp)/'output';shutil.copytree(a.output,altered)
    with (altered/'index.html').open('a') as file: file.write('PRIVATE_ORIGINAL_SENTINEL')
    try:
        validate_public_artifacts(altered,manifest,forbidden_fingerprints=['PRIVATE_ORIGINAL_SENTINEL'],trusted_template_assets=assets,generated_files=quartz_generated_files(a.staging),policy=policy)
    except ProjectionError:
        pass
    else:
        raise AssertionError('Injected private content was not rejected')
result['injected_private_output_rejected']=True
result.update({'scope':'real-quartz-render-with-synthetic-markdown-not-live-e2e','portal_entries':8,'search_control_present':True,'sources_toc_backlinks_present':True,'template_baseline':'independently rendered synthetic variant B','browser_visual_validation':'blocked: Chromium socket Operation not permitted'})
a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
