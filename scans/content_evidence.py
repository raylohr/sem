"""Bounded static naming and content evidence; no accessibility-tree claims."""
from collections import defaultdict
from .html_evidence import EvidenceParser


def clean(value):
    return ' '.join(value.split())


class ContentEvidenceParser(EvidenceParser):
    def __init__(self):
        super().__init__()
        from .research_evidence import Research
        self.research=Research()
        self.content_headings=[];self.social=[]
        self.headings_complete=True;self.social_complete=True
        self.asset_candidates=[];self.assets_complete=True;self.inline_styles=[]

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs={key:value or '' for key,value in attrs}
        locator=f'html:{self.getpos()[0]}:{self.getpos()[1]}'
        node=self.stack[-1] if self.stack and self.stack[-1]['locator']==locator else None
        excluded=any(n['excluded'] for n in self.stack) or tag in ('svg','math','template','noscript')
        if excluded:
            if tag in ('svg','math'):
                for ancestor in self.stack:
                    if ancestor.get('naming_link') is not None:ancestor['naming_link']['name_uncertain']=True
            return
        parent_nodes=self.stack[:-1] if node else self.stack
        hidden=any(n.get('hidden_content') for n in parent_nodes) or 'hidden' in attrs or attrs.get('aria-hidden','').lower()=='true' or 'inert' in attrs or any(token in ''.join(attrs.get('style','').lower().split()) for token in ('display:none','visibility:hidden'))
        if node:node['hidden_content']=hidden
        for label in self.labels[-1:]:
            if label['locator']==locator:
                label['name_override']=attrs.get('aria-label','')[:512]
                label['name_uncertain']=len(attrs.get('id',''))>200 or bool(attrs.get('aria-labelledby')) or len(attrs.get('aria-label',''))>512
        location={key:(node or self.images.get(locator) or {}).get(key,'') for key in ('locator','element_path','region')}
        location['locator']=locator
        if not location['element_path']:
            parent=self.stack[-1] if self.stack else None
            counts=parent['children'] if parent else self.roots
            location['element_path']=((parent['element_path'] if parent else '')+f'/{tag}[{counts[tag]}]')[-1024:]
            location['region']=parent['region'] if parent else 'document'
        self.research.start(self,tag,attrs,node,location)
        from .resource_discovery import tag_resources
        tag_resources(self,tag,attrs,location)
        if tag=='style' and node:
            if len(self.inline_styles)<20:
                block={'text':'','truncated':False,'location':location};self.inline_styles.append(block);node['captures'].append((block,65536))
            else:self.assets_complete=False
        if tag in ('h1','h2','h3','h4','h5','h6'):
            if len(self.content_headings)<200:
                heading={**location,'level':int(tag[1]),'text':'','truncated':False,'hidden':hidden}
                self.content_headings.append(heading)
                if node:node['captures'].append((heading,1024))
            else:self.headings_complete=False
        if tag=='meta':
            name=(attrs.get('property') or attrs.get('name','')).strip().lower()
            if name.startswith(('og:','twitter:')):
                if len(self.social)<200:
                    # Void elements are not on the stack; metadata has the same source location.
                    parent=self.stack[-1] if self.stack else None
                    counts=parent['children'] if parent else self.roots
                    path=((parent['element_path'] if parent else '')+f'/meta[{counts["meta"]}]')[-1024:]
                    tags=[n['tag'] for n in self.stack]
                    self.social.append({'name':name[:100],'value':attrs.get('content','')[:4096],
                        'truncated':len(attrs.get('content',''))>4096,'locator':locator,'element_path':path,
                        'in_head':False if 'body' in tags else True if 'head' in tags else None})
                else:self.social_complete=False
        link=self.links.get(locator)
        if link is not None:
            link.update(visible_text='',content_name='',images=[],hidden=hidden,name_uncertain=False,text_truncated=False,
                        aria_truncated=len(attrs.get('aria-label',''))>512 or len(attrs.get('aria-labelledby',''))>1024 or len(attrs.get('aria-labelledby','').split())>20,
                        title_truncated=len(attrs.get('title',''))>512)
            if tag=='area':link['area_alt']=attrs.get('alt','')[:512]
            if node:node['naming_link']=link
        image=self.images.get(locator)
        if image is not None:
            parent_link=next((n.get('naming_link') for n in reversed(self.stack) if n.get('naming_link') is not None),None)
            role=attrs.get('role','').lower()
            image.update(alt_present='alt' in attrs,alt=attrs.get('alt','')[:500],role=role[:50],hidden=hidden,
                         aria_label=attrs.get('aria-label','')[:512],aria_labelledby=attrs.get('aria-labelledby','')[:1024],
                         linked=parent_link is not None,link_locator=parent_link['locator'] if parent_link else '')
            if parent_link is not None:
                if len(parent_link['images'])<20:
                    parent_link['images'].append({'locator':locator,'alt_present':'alt' in attrs,'alt':image['alt'],'src':image.get('raw_src','')})
                else:parent_link['name_uncertain']=True
                if not hidden and role not in ('presentation','none'):
                    value=attrs.get('aria-label','').strip() or attrs.get('alt','')
                    parent_link['content_name']=(parent_link['content_name']+' '+value+' ')[:2048]
                    if ('alt' not in attrs and not attrs.get('aria-label','').strip()) or len(value)>500 or attrs.get('aria-labelledby'):parent_link['name_uncertain']=True
        if tag in ('img','svg','input','button','iframe'):
            # Complex referenced subtrees need browser confirmation instead of an invented name.
            for ancestor in self.stack:
                for entry,limit in ancestor['captures']:
                    if 'id' in entry:entry['name_uncertain']=True
        if tag in ('svg','input','button','iframe'):
            for ancestor in parent_nodes:
                if ancestor.get('naming_link') is not None:ancestor['naming_link']['name_uncertain']=True

    def handle_endtag(self,tag):
        self.research.end(self,tag)
        super().handle_endtag(tag)

    def handle_data(self,data):
        self.research.data(self,data)
        for node in self.stack:
            if node['tag']=='style':
                for block,limit in node['captures']:
                    if 'location' in block:
                        remaining=limit-len(block['text']);block['text']+=data[:max(0,remaining)]
                        if len(data)>remaining:block['truncated']=True
        super().handle_data(data)
        if any(n['excluded'] or n['tag'] in ('script','style') or n.get('hidden_content') for n in self.stack):return
        for node in self.stack:
            link=node.get('naming_link')
            if link is not None:
                for key in ('visible_text','content_name'):
                    remaining=2048-len(link[key])
                    if len(data)>remaining:link['text_truncated']=True
                    if remaining>0:link[key]+=data[:remaining]

    def finish(self, fields, base):
        self.research.finish(self,fields,base)
        from .resource_discovery import finish_resources
        finish_resources(self,fields,base)
        labels=defaultdict(list)
        for label in self.labels:labels[label['id']].append(label)
        links=[]
        for link in self.links.values():
            name='';basis='contents';known=not (link.get('name_uncertain') or link.get('text_truncated'))
            refs=link.get('aria_labelledby',[])
            if refs:
                targets=[labels.get(ref,[]) for ref in refs]
                known=self.complete and all(len(items)==1 and not items[0]['truncated'] and not items[0].get('name_uncertain') for items in targets)
                name=clean(' '.join((items[0].get('name_override') or items[0]['text']) for items in targets if len(items)==1));basis='aria-labelledby'
            elif clean(link.get('aria_label','')):
                name=clean(link['aria_label']);basis='aria-label';known=True
            else:
                name=clean(link.get('area_alt') or link.get('content_name',''))
                if not name and clean(link.get('title_attribute','')):
                    name=clean(link['title_attribute']);basis='title';known=True
            if link.get('hidden') or link.get('aria_truncated') or (basis=='title' and link.get('title_truncated')):known=False
            link.update(static_name=name[:512],name_basis=basis,name_status='known' if known and len(name)<=512 else 'unknown',
                        visible_text=clean(link.get('visible_text',''))[:512])
            links.append({key:link[key] for key in ('locator','element_path','region','raw_url','static_name','name_basis','name_status','visible_text','images','hidden') if key in link})
            link.pop('content_name',None)
        for image in self.images.values():
            image['alt_interpretation']=('missing_attribute' if not image['alt_present'] else 'text_present_unverified' if clean(image['alt']) else
                'explicitly_decorative' if image['role'] in ('presentation','none') or image['hidden'] else 'empty_needs_review')
        for heading in self.content_headings:heading['text']=clean(heading['text'])
        fields.update(content_audit={'version':'content-evidence-v1','complete':self.complete,'headings_complete':self.headings_complete,
                                    'social_complete':self.social_complete,'naming_basis':'bounded static HTML; no CSS, JS or accessibility-tree evaluation'},
                      heading_evidence=self.content_headings,link_name_evidence=links,social_metadata=self.social)
        for link in fields['link_details']:link.update(self.links.get(link['locator'],{}))
        for image in fields['images']:image.update(self.images.get(image['locator'],{}))
