import csv,json,hashlib,gzip,argparse
from pathlib import Path
from collections import Counter
from urllib.parse import urlsplit,urlunsplit
parser=argparse.ArgumentParser(description='Offline SF export versus frozen post-R6 production snapshot; no network or database writes.')
parser.add_argument('--snapshot',type=Path,required=True)
parser.add_argument('--sf-dir',type=Path,required=True)
parser.add_argument('--sf-archive',type=Path,required=True)
parser.add_argument('--baseline',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();OUT=args.output;OUT.mkdir(parents=True,exist_ok=True);SF=args.sf_dir
def read(name):return list(csv.DictReader((SF/name).open(encoding='utf-8-sig')))
def norm(url):
 p=urlsplit(url);return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',p.query,''))
data=json.loads((gzip.decompress(args.snapshot.read_bytes()) if args.snapshot.suffix=='.gz' else args.snapshot.read_bytes()).decode())
old=list(csv.DictReader(args.baseline.open()))
reference={norm(r['url']) for r in old};pages={}
for p in data['pages']:
 u=norm(p['final_url'])
 if p['status']==200 and 'html' in p['content_type'] and (u not in pages or norm(p['url'])==u):pages[u]=p
common=reference & set(pages);images=read('all_image_inlinks2026-11-04.csv');outlinks=read('all_outlinks2026-11-04.csv')
sf_images=[r for r in images if norm(r['Source']) in common];sf_links={(norm(r['Source']),norm(r['Destination'])) for r in outlinks if r['Type']=='Hyperlink' and norm(r['Source']) in common and urlsplit(r['Destination']).scheme in ('http','https')}
ours_links={(norm(r['source__final_url']),norm(r['destination'])) for r in data['links'] if norm(r['source__final_url']) in common and urlsplit(r['destination']).scheme in ('http','https')}
our_images=[(u,img) for u in common for img in pages[u]['facts'].get('images',[])]
sfpairs={(norm(r['Source']),norm(r['Destination'])) for r in sf_images};ourpairs={(u,norm(i['src'])) for u,i in our_images if i.get('src')}
resource_map={(r['purpose'],norm(r['url'])):r for r in data['resources']}
sf_targets={norm(r['Destination']) for r in outlinks if r['Type'] in ('CSS','JavaScript','Image')};sf_external={norm(r['Destination']) for r in outlinks if urlsplit(r['Destination']).hostname not in ('vandersteenhoven.nl','www.vandersteenhoven.nl') and urlsplit(r['Destination']).scheme in ('http','https') and r['Type']=='Hyperlink'}
errors=read('all_error_(4xx_5xx_no_response)_inlinks2026-11-04.csv')
error_comparison=[]
for u in sorted({r['Destination'] for r in errors}):
 sr=next(r for r in errors if r['Destination']==u);purpose='resource' if sr['Type'] in ('JavaScript','CSS','Image') else 'external' if urlsplit(u).hostname not in ('vandersteenhoven.nl','www.vandersteenhoven.nl') else 'page'
 target=resource_map.get((purpose,norm(u)));page=next((p for p in data['pages'] if norm(p['url'])==norm(u)),None)
 error_comparison.append({'url':u,'purpose':purpose,'sf_status':sr['Status Code'],'our_status':target['http_status'] if target else page['status'] if page else None,'our_outcome':target['outcome'] if target else 'page_measured' if page else 'not_in_measurement'})
large={norm(r['Destination']) for r in sf_images if int(r.get('Size (Bytes)') or 0)>102400}
large_comparison=[{'url':u,'sf_bytes':next(int(r['Size (Bytes)']) for r in sf_images if norm(r['Destination'])==u),'our_bytes':resource_map.get(('resource',u),{}).get('measurements',{}).get('wire_bytes'),'outcome':resource_map.get(('resource',u),{}).get('outcome')} for u in sorted(large)]
rows=[]
for u in sorted(common):
 f=pages[u]['facts'];im=f.get('images',[])
 rows.append({'url':u,'missing_description':not any(f.get('meta_descriptions',[])),'missing_h1':not f.get('h1'),'multiple_titles':len(f.get('titles',[]))>1,'first_title_chars':len((f.get('titles') or [''])[0]),'images':len(im),'images_empty_alt':sum(i.get('alt_present') and i.get('alt')=='' for i in im),'images_missing_alt':sum(not i.get('alt_present') for i in im),'self_canonical':len(f.get('canonicals',[]))==1 and norm(f['canonicals'][0])==u,'main_words':f.get('main_content',{}).get('word_count'),'main_selection':f.get('main_content',{}).get('selection'),'readability':f.get('main_content',{}).get('readability',{}).get('status')})
with (OUT/'url-comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
result={'date':'2026-11-05','release':'da1518a','batch_id':data['batch_id'],'status':data['status'],'measurement_snapshot_sha256':hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),'sf_archive_sha256':hashlib.sha256(args.sf_archive.read_bytes()).hexdigest(),'reference_pages':len(reference),'successful_html_final_urls':len(pages),'common_pages':len(common),'missing_reference_pages':sorted(reference-set(pages)),
 'common_checks':{'missing_description':{'sf':59,'ours':sum(r['missing_description'] for r in rows)},'missing_h1':{'sf':22,'ours':sum(r['missing_h1'] for r in rows)},'multiple_titles':{'sf':1,'ours':sum(r['multiple_titles'] for r in rows)},'title_over60':{'sf':32,'ours':sum(r['first_title_chars']>60 for r in rows)},'image_references':{'sf':len(sf_images),'ours':len(our_images)},'image_pairs':{'sf':len(sfpairs),'ours':len(ourpairs),'missing':len(sfpairs-ourpairs),'extra':len(ourpairs-sfpairs)},'hyperlink_pairs':{'sf':len(sf_links),'ours':len(ours_links),'missing':len(sf_links-ours_links),'extra':len(ours_links-sf_links)},'empty_alt':{'sf':len([r for r in sf_images if not r['Alt Text']]),'ours':sum(r['images_empty_alt'] for r in rows)},'self_canonical':{'sf':59,'ours':sum(r['self_canonical'] for r in rows)},'missing_alt':{'ours':sum(r['images_missing_alt'] for r in rows)}},
 'resource_coverage':{'sf_direct_targets':len(sf_targets),'measured_inventory_overlap':len([u for u in sf_targets if ('resource',u) in resource_map]),'missing_targets':sorted(u for u in sf_targets if ('resource',u) not in resource_map),'external_sf_hyperlink_targets':len(sf_external),'external_overlap':len([u for u in sf_external if ('external',u) in resource_map]),'external_missing':sorted(u for u in sf_external if ('external',u) not in resource_map),'sf_resource_outcomes':dict(Counter(resource_map[('resource',u)]['outcome'] for u in sf_targets if ('resource',u) in resource_map)),'sf_external_outcomes':dict(Counter(resource_map[('external',u)]['outcome'] for u in sf_external if ('external',u) in resource_map))},
 'external_statuses':dict(Counter(str(r['http_status']) for r in data['resources'] if r['purpose']=='external')),'resource_control_coverage':data['analyses'][0]['coverage'].get('resource_checks',{}),'resource_outcomes':dict(Counter(r['purpose']+':'+r['outcome'] for r in data['resources'])),'error_comparison':error_comparison,'large_image_comparison':large_comparison,
 'finding_counts':dict(Counter(f['rule'] for f in data['findings'])),'common_finding_pages':{rule:len({norm(f['result__source_record__data__final_url']) for f in data['findings'] if f['rule']==rule and norm(f['result__source_record__data__final_url'] or '') in common}) for rule in sorted({f['rule'] for f in data['findings']})},'main_run_check_coverage':data['analyses'][0]['coverage'].get('advanced_checks',{}),'common_raw_h1_after_h2':sum(any(h['level']==2 for h in pages[u]['facts'].get('heading_sequence',[])[:next((i for i,h in enumerate(pages[u]['facts'].get('heading_sequence',[])) if h['level']==1),0)]) for u in common),'common_content':{'selection':dict(Counter(r['main_selection'] for r in rows)),'readability':dict(Counter(r['readability'] for r in rows))},'common_schema':dict(Counter(pages[u]['facts'].get('structured_data',{}).get('schema_state','unknown') for u in common)), 'common_image_geometry':{'with_declared_dimensions':sum(bool(i.get('width') and i.get('height')) for u,i in our_images),'with_element_path':sum(bool(i.get('element_path')) for u,i in our_images)},'metadata':data['metadata'],'warnings':data['warnings'],'dossiers':data['dossiers'],
 'browser_summary':[{'id':r['id'],'batch_id':r['batch_id'],'status':r['status'],'coverage':r['coverage'],'visit_status':dict(Counter(v['status'] for v in r['visits'])),'http_status':dict(Counter(str(v['http_status']) for v in r['visits'])),'resources':r['resources'],'discovered':r['discovered'],'accessibility':dict(Counter(a['rule'] for v in r['visits'] for a in v['accessibility'])),'timed_out':sum(v['coverage'].get('timed_out',False) for v in r['visits']),'scroll_complete':sum(v['coverage'].get('lazy_load',{}).get('scroll_complete',False) for v in r['visits']),'image_occurrences':sum(len(v['coverage'].get('images',[])) for v in r['visits']),'title_widths':[{'url':v['url'],'measurement':v['metrics'].get('title_width')} for v in r['visits']]} for r in data['browsers']],
 'missing_link_pairs':[list(p) for p in sorted(sf_links-ours_links)],'missing_image_pairs':[list(p) for p in sorted(sfpairs-ourpairs)]}
external_comparison=[{'url':u,'sf_initial_status':next((r['Status Code'] for r in outlinks if norm(r['Destination'])==u),''),'our_final_status':resource_map.get(('external',u),{}).get('http_status'),'outcome':resource_map.get(('external',u),{}).get('outcome')} for u in sorted(sf_external)]
result['external_status_comparison']=external_comparison
for filename,records in [('external-comparison.csv',external_comparison),('large-images.csv',large_comparison),('sf-errors-comparison.csv',error_comparison)]:
 with (OUT/filename).open('w') as handle:
  writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
(OUT/'comparison-summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
print(json.dumps({k:result[k] for k in ('status','common_pages','common_checks','resource_outcomes','resource_coverage')},indent=2))
