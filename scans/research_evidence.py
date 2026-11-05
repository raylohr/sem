"""Bounded JSON-LD, simple nested Microdata/RDFa and main-text evidence."""
import json
from urllib.parse import urljoin
from .main_content import MainText,BLOCKS
from .structured_data import evaluate,known_context,term,strict_json


class Research:
    def __init__(self):
        self.main=MainText();self.blocks=[];self.entities=[];self.complete=True;self.text_values=[]
    def start(self,parser,tag,attrs,node,location):
        self.main.start(parser,tag,attrs,node,location)
        if tag=='script' and attrs.get('type','').split(';')[0].strip().lower()=='application/ld+json':
            if len(self.blocks)<20 and node:
                block={'text':'','truncated':False,'closed':False,**location};self.blocks.append(block);node['research_json']=block
            else:self.complete=False
        parent_nodes=parser.stack[:-1] if node else parser.stack
        for format in ('microdata','rdfa'):
            owner=next((n.get(format+'_entity') for n in reversed(parent_nodes) if n.get(format+'_entity') is not None),None)
            vocab=attrs.get('vocab') or next((n.get('rdfa_vocab') for n in reversed(parent_nodes) if n.get('rdfa_vocab')),None)
            prefixes=attrs.get('prefix','').split()
            schema_prefix=any(prefixes[i]=='schema:' and known_context(prefixes[i+1]) for i in range(len(prefixes)-1)) or any(n.get('schema_prefix') for n in parent_nodes)
            if node:
                node['schema_prefix']=schema_prefix
                if vocab:node['rdfa_vocab']=vocab
            props=attrs.get('itemprop' if format=='microdata' else 'property','').split()[:20]
            if format=='rdfa' and not props and attrs.get('rel') and (owner or vocab):props=attrs['rel'].split()[:20]
            is_entity=('itemscope' in attrs) if format=='microdata' else bool(attrs.get('typeof'))
            if format=='rdfa' and not (owner or vocab or attrs.get('typeof') or any(p.startswith('schema:') for p in props)):continue
            if is_entity:
                if len(self.entities)>=200:self.complete=False;continue
                raw_types=attrs.get('itemtype' if format=='microdata' else 'typeof','').split()[:10]
                schema_vocab=bool(vocab and known_context(vocab)) or bool(raw_types) and all(t.startswith(('https://schema.org/','http://schema.org/')) or t.startswith('schema:') and schema_prefix for t in raw_types)
                data={'@type':raw_types}
                entity={'format':format,'locator':location['locator'],'element_path':location['element_path'],'data':data}
                identifier=attrs.get('itemid' if format=='microdata' else 'about') or attrs.get('resource') or (attrs.get('href') if format=='rdfa' else None)
                if identifier:data['@id']=identifier[:4096]
                if format=='microdata' and not all(t.startswith(('https://schema.org/','http://schema.org/')) for t in raw_types):entity['unsupported']='Microdata itemtype buiten ondersteunde absolute schema.org-URL-vorm'
                if attrs.get('itemref'):entity['unsupported']='Microdata itemref niet uitgewerkt'
                if format=='rdfa' and not schema_vocab:entity['unsupported']='RDFa-vocab/prefix buiten schema.org-profiel'
                self.entities.append(entity)
                if node:node[format+'_entity']=entity
                pending_props=next((n.get('rdfa_pending') for n in reversed(parent_nodes) if n.get('rdfa_pending')),[]) if format=='rdfa' and not props else []
                for prop in props or pending_props:self.add(owner,prop,data)
                continue
            if format=='rdfa' and attrs.get('rel') and node and props:
                node['rdfa_pending']=props
            if owner and props:
                if format=='rdfa' and any(':' not in p for p in props) and not (vocab and known_context(vocab)):owner['unsupported']='RDFa vocab ontbreekt voor kale property-namen'
                value=next((attrs[key] for key in ('content','href','resource','src','datetime','value') if key in attrs),None)
                if value is not None:
                    for prop in props:self.add(owner,prop,value[:4096])
                    if len(value)>4096:self.complete=False
                elif node and not (format=='rdfa' and attrs.get('rel')):
                    if len(self.text_values)>=1000:self.complete=False;continue
                    capture={'text':'','truncated':False,'owner':owner,'props':props};node.setdefault('research_values',[]).append(capture);self.text_values.append(capture)
    def add(self,owner,prop,value):
        if owner is None:return
        name=term(prop)
        if name is None:owner['unsupported']='veld uit andere woordenlijst';return
        data=owner['data']
        if len(data)>100:self.complete=False;return
        if name not in data:data[name]=value
        elif isinstance(data[name],list):
            if len(data[name])<100:data[name].append(value)
            else:self.complete=False
        else:data[name]=[data[name],value]
    def data(self,parser,text):
        self.main.data(parser,text)
        for node in parser.stack:
            block=node.get('research_json')
            if block is not None:
                remaining=65536-len(block['text'])
                if len(text)>remaining:block['truncated']=True
                if remaining>0:block['text']+=text[:remaining]
            if not any(n['excluded'] or n['tag'] in ('script','style') for n in parser.stack):
                for capture in node.get('research_values',[]):
                    remaining=4096-len(capture['text'])
                    if len(text)>remaining:capture['truncated']=True
                    if remaining>0:capture['text']+=text[:remaining]
    def end(self,parser,tag):
        if tag in BLOCKS:self.main.data(parser,'\n')
        if tag=='script':
            for node in reversed(parser.stack):
                if node.get('research_json') is not None:node['research_json']['closed']=True;break
    def finish(self,parser,fields,base):
        syntax=[]
        for capture in self.text_values:
            if capture['truncated']:self.complete=False
            for prop in capture['props']:self.add(capture['owner'],prop,' '.join(capture['text'].split()))
        entities=list(self.entities)
        for block in self.blocks:
            entry={'format':'jsonld','locator':block['locator'],'element_path':block['element_path']}
            if block['truncated'] or not block['closed']:
                syntax.append({**entry,'state':'unknown','reason':'afgekapt of niet gesloten script'});self.complete=False;continue
            try:
                value=strict_json(block['text'])
                if not isinstance(value,(dict,list)):raise ValueError('JSON-LD topniveau geen object of array')
                syntax.append({**entry,'state':'pass'})
                if isinstance(value,list):
                    # A top-level array needs an explicit context on each object.
                    for item in value[:100]:entities.append({**entry,'data':item})
                else:entities.append({**entry,'data':value})
            except RecursionError:
                syntax.append({**entry,'state':'unknown','reason':'decoder-nestingsgrens; geen bewezen syntaxisfout'});self.complete=False
            except ValueError as exc:
                syntax.append({**entry,'state':'issue','reason':'ongeldige JSON-LD JSON-syntaxis','line':getattr(exc,'lineno',None),'column':getattr(exc,'colno',None)})
        self.main.finish(fields,base,parser.structure_complete)
        fields['structured_data']=evaluate(entities,syntax,self.complete and parser.structure_complete,base)
