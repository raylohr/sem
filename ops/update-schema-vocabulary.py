"""Build an offline pinned vocabulary; never downloads at application runtime."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');p.add_argument('--version',required=True);p.add_argument('--published',required=True);p.add_argument('--source-url',required=True);a=p.parse_args()
raw=Path(a.source).read_bytes()
if len(raw)>20*1024**2:raise SystemExit('Vocabulary exceeds build limit')
data=json.loads(raw);types={};properties={};members={}
def ids(value):
    rows=value if isinstance(value,list) else [value] if value else []
    return [r['@id'][7:] for r in rows if isinstance(r,dict) and r.get('@id','').startswith('schema:')]
for row in data['@graph']:
    if not row['@id'].startswith('schema:'):continue
    name=row['@id'][7:];kind=row.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
    if 'rdfs:Class' in kind:types[name]={'parents':ids(row.get('rdfs:subClassOf'))}
    elif 'rdf:Property' in kind:properties[name]={'domains':ids(row.get('schema:domainIncludes')),'ranges':ids(row.get('schema:rangeIncludes'))}
    else:
        parents=[t[7:] for t in kind if t.startswith('schema:')]
        if parents:members[name]=parents
result={'version':a.version,'published':a.published,'source':a.source_url,'source_sha256':hashlib.sha256(raw).hexdigest(),'types':types,'properties':properties,'members':members}
Path(a.output).write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n')
print('Pinned vocabulary:',a.version,'types',len(types),'properties',len(properties),'members',len(members))
