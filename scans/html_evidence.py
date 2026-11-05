"""Bounded source-HTML locations and attributes; no browser-repaired DOM claims."""
from collections import Counter
from html.parser import HTMLParser

VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class EvidenceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.roots=Counter();self.nodes=0;self.complete=True;self.structure_complete=True
        self.metadata=[];self.links={};self.images={};self.resources=[];self.labels=[];self.documents=Counter()

    def handle_starttag(self,tag,attrs):
        self.nodes+=1
        if self.nodes>100000 or len(self.stack)>=128:
            self.complete=False;self.structure_complete=False
            return
        attrs={key:value or '' for key,value in attrs};parent=self.stack[-1] if self.stack else None
        counts=parent['children'] if parent else self.roots;counts[tag]+=1
        raw_path=(parent['element_path'] if parent else '')+f'/{tag}[{counts[tag]}]'
        path=raw_path[-1024:]
        if len(raw_path)>1024:self.complete=False;self.structure_complete=False
        locator=f'html:{self.getpos()[0]}:{self.getpos()[1]}'
        excluded=bool(parent and parent['excluded']) or tag in ('svg','math','template','noscript')
        region=parent['region'] if parent else 'document'
        role=attrs.get('role','').lower()
        if tag in ('header','footer','main','nav') or role in ('banner','contentinfo','main','navigation'):
            if not ((tag=='nav' or role=='navigation') and region in ('header','footer')):
                region={'banner':'header','contentinfo':'footer','navigation':'nav'}.get(role,tag)
        location={'locator':locator,'element_path':path,'region':region,'element_type':tag}
        node={**location,'tag':tag,'children':Counter(),'excluded':excluded,'captures':[]}
        names=[n['tag'] for n in self.stack]
        if not excluded:
            if tag in ('html','head','body'):self.documents[tag]+=1
            kind=tag if tag in ('title','h1','h2') else 'description' if tag=='meta' and attrs.get('name','').lower()=='description' else 'canonical' if tag=='link' and 'canonical' in attrs.get('rel','').lower().split() else None
            if kind:
                if len(self.metadata)<500:
                    in_head=False if 'body' in names else True if 'head' in names else False if self.documents.get('head') else None
                    entry={**location,'kind':kind,'in_head':in_head,
                           'raw_value':attrs.get('href','')[:4096] if kind=='canonical' else attrs.get('content','')[:4096] if kind=='description' else '',
                           'text':'','truncated':False}
                    self.metadata.append(entry)
                    if kind in ('title','h1','h2'):node['captures'].append((entry,4096))
                else:self.complete=False
            if tag in ('a','area') and attrs.get('href'):
                if len(self.links)<2000:
                    self.links[locator]={**location,'raw_url':attrs['href'][:4096],'target':attrs.get('target','')[:80],
                        'aria_label':attrs.get('aria-label','')[:512],'aria_labelledby':attrs.get('aria-labelledby','')[:1024].split()[:20],
                        'title_attribute':attrs.get('title','')[:512],'reference_type':'hyperlink'}
                else:self.complete=False
            if tag=='img':
                if len(self.images)<200:
                    self.images[locator]={**location,'width':attrs.get('width','')[:64],'height':attrs.get('height','')[:64],
                        'srcset':attrs.get('srcset','')[:4096],'sizes':attrs.get('sizes','')[:1024],'loading':attrs.get('loading','')[:32],
                        'title_attribute':attrs.get('title','')[:512],'raw_src':attrs.get('src','')[:4096]}
                else:self.complete=False
            resource=attrs.get('src') if tag in ('script','img','iframe','source') else attrs.get('href') if tag=='link' else attrs.get('action') if tag=='form' else None
            if resource and len(self.resources)<200:
                self.resources.append({**location,'raw_url':resource[:4096],'reference_type':'form_action' if tag=='form' else 'resource',
                    'rel':attrs.get('rel','').lower().split()[:20],'as':attrs.get('as','')[:64]})
            elif resource:self.complete=False
            if attrs.get('id'):
                if len(self.labels)<1000:
                    label={**location,'id':attrs['id'][:200],'text':'','truncated':False};self.labels.append(label);node['captures'].append((label,512))
                else:self.complete=False
        if tag not in VOID:self.stack.append(node)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index]['tag']==tag:
                del self.stack[index:];break

    def handle_data(self,data):
        if any(n['tag'] in ('script','style','noscript') for n in self.stack):return
        for node in self.stack:
            for entry,limit in node['captures']:
                remaining=limit-len(entry['text'])
                if len(data)>remaining:entry['truncated']=True
                if remaining>0:entry['text']+=data[:remaining]


def enrich(html,fields,base):
    from urllib.parse import urljoin
    from .content_evidence import ContentEvidenceParser
    parser=ContentEvidenceParser();parser.feed(html)
    for entry in parser.metadata+parser.labels:entry['text']=' '.join(entry['text'].split())
    fields.update(html_audit={'version':'html-evidence-v1','complete':parser.complete,'nodes':min(parser.nodes,100001)},
        document_structure=dict(parser.documents),metadata_evidence=parser.metadata,aria_references=parser.labels,
        resource_details=[dict(item,url=urljoin(base,item['raw_url'])) for item in parser.resources])
    for link in fields['link_details']:link.update(parser.links.get(link['locator'],{}))
    for image in fields['images']:image.update(parser.images.get(image['locator'],{}))

    parser.finish(fields,base)
