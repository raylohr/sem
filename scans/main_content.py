"""Versioned bounded main-text selection and conservative Dutch readability indicators."""
import hashlib
import re
from urllib.parse import urlsplit

WORD=re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*",re.UNICODE)
NL=set('de het een en van in is op voor met dat die te zijn aan niet om ook als bij door wordt of deze maar naar dit heeft kunnen'.split())
EN=set('the a an and of in is on for with that which to are at not also as by from this but has can your you their it'.split())
LEGAL=('jurisprudentie','arbeidsovereenkomst','rechtbank','gerechtshof','wetsartikel','ondernemingsrecht','bestuursrecht','ecli:','burgerlijk wetboek','rechtsvordering')
BLOCKS={'p','div','section','article','main','h1','h2','h3','h4','h5','h6','li','td','tr','br'}


class Buffer:
    def __init__(self):self.parts=[];self.size=0;self.truncated=False
    def add(self,text):
        remaining=100000-self.size
        if len(text)>remaining:self.truncated=True
        if remaining>0:self.parts.append(text[:remaining]);self.size+=len(text[:remaining])
    def text(self):return ''.join(self.parts)


class MainText:
    def __init__(self):self.body=Buffer();self.candidates=[]
    def start(self,parser,tag,attrs,node,location):
        if node and (tag in ('main','article') or attrs.get('role')=='main'):
            if len(self.candidates)<10:
                item={'kind':'main' if tag=='main' or attrs.get('role')=='main' else 'article','location':location,'buffer':Buffer()}
                self.candidates.append(item);node['main_text']=item
            else:parser.complete=False
        if tag in BLOCKS:self.data(parser,'\n')
    def data(self,parser,text):
        if any(n['excluded'] or n['tag'] in ('script','style','aside','head','title') or n.get('hidden_content') or n['region'] in ('header','footer','nav') for n in parser.stack):return
        self.body.add(text)
        for node in parser.stack:
            if node.get('main_text'):node['main_text']['buffer'].add(text)
    def finish(self,fields,base,complete):
        main=[c for c in self.candidates if c['kind']=='main'];articles=[c for c in self.candidates if c['kind']=='article']
        chosen=(main or articles or [None])[0]
        buffer=chosen['buffer'] if chosen else self.body
        raw=buffer.text();text=' '.join(raw.split());tokens=WORD.findall(text);words=len(tokens);lower=text.casefold()
        declared=fields.get('language','').split('-')[0].lower();counts={language:sum(t.casefold() in vocab for t in tokens) for language,vocab in [('nl',NL),('en',EN)]}
        inferred='nl' if counts['nl']>=8 and counts['nl']>=counts['en']*1.5+2 else 'en' if counts['en']>=8 and counts['en']>=counts['nl']*1.5+2 else 'unknown'
        language='unsupported' if declared and declared not in ('nl','en') else inferred if inferred!='unknown' else declared if declared in ('nl','en') else 'unknown'
        conflict=bool(declared and inferred!='unknown' and declared!=inferred)
        abbreviations=re.sub(r'\b(?:mr|dr|prof|art|vgl|bijv|nr)\.',lambda m:m[0].replace('.',''),raw,flags=re.I)
        sentences=[s for s in re.split(r'[.!?]+(?:\s+|$)|\n+',abbreviations) if WORD.search(s)]
        legal=[term for term in LEGAL if term in lower]
        average=round(words/len(sentences),1) if sentences else None
        status='unknown';reason='onvoldoende of niet-ondersteunde taal/tekst'
        if complete and not buffer.truncated and words>=100 and len(sentences)>=3 and language=='nl' and not conflict and chosen and len(main)<=1 and (main or len(articles)==1):
            status='expert_review' if len(legal)>=2 else 'measured';reason='juridische context: vakpubliek en begrijpelijkheid handmatig beoordelen' if len(legal)>=2 else 'beschrijvende zinslengte-indicatie; geen leesniveau of gevalideerde begripsscore'
        path=urlsplit(base).path.lower()
        functional=any(p in path for p in ('contact','login','inloggen','zoeken','search','calculator','bereken','privacy','cookie'))
        article=bool(articles) or any(p in path for p in ('nieuws','blog','publicatie','wor-wekelijks'))
        page_type='functional' if functional else 'home' if path=='/' else 'article' if article else 'general'
        fields['main_content']={'version':'main-html-v1','selection':chosen['kind'] if chosen else 'body_without_landmarks','location':chosen['location'] if chosen else None,
            'selection_confidence':'explicit' if chosen and len(main)<=1 and (main or len(articles)==1) else 'fallback_or_ambiguous',
            'word_count':words,'text_excerpt':text[:2000],'text_hash':hashlib.sha256(text.encode()).hexdigest(),'truncated':buffer.truncated,
            'complete':complete and not buffer.truncated,'counting':'Unicode words; hyphen/apostrophe compounds one token; HTML blocks separate; no CSS or JS inference',
            'page_type':page_type,'page_type_basis':'URL/semantic HTML heuristic; review against actual function',
            'language':{'declared':declared,'inferred':inferred,'used':language,'conflict':conflict,'method':'bounded nl/en stopword-v1; declared-language fallback'},
            'readability':{'profile':'nl-sentence-indicator-v1','status':status,'reason':reason,'sentence_count':len(sentences),'mean_words_per_sentence':average,
                'specialist_terms':legal,'score':None,'long_sentence_advice_threshold':25,'scope':'indicative structural metrics; no scientific reading-grade or quality score'}}
