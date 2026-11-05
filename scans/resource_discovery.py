"""Bounded HTML and CSS URL discovery. Does not interpret arbitrary JavaScript."""
import re
from urllib.parse import urljoin, urlsplit

MAX_REFERENCES=2000


def srcset_urls(value):
    # URL tokens may contain commas (notably data:); never split data into HTTP candidates.
    position=0
    while position<len(value):
        while position<len(value) and (value[position].isspace() or value[position]==','):position+=1
        start=position
        while position<len(value) and not value[position].isspace():position+=1
        url=value[start:position]
        if not url:break
        if url.endswith(','):
            yield url.rstrip(',');continue
        yield url
        while position<len(value) and value[position]!=',':position+=1
        position+=1


def css_urls(text, limit=MAX_REFERENCES):
    """Small scanner: comments/strings, CSS escapes, url() and quoted @import only."""
    complete=len(text)<=1024**2;text=text[:1024**2]
    found=[];i=0;importing=False
    escape=re.compile(r'[0-9a-fA-F]{1,6}\s?');url_function=re.compile(r'url\s*\(',re.I)
    def string(index, quote):
        output=[]
        while index<len(text):
            char=text[index];index+=1
            if char==quote:return ''.join(output),index,True
            if char=='\\' and index<len(text):
                match=escape.match(text,index)
                if match:
                    code=int(match[0].strip(),16);output.append(chr(code) if 0<code<=0x10ffff else '\ufffd');index+=len(match[0])
                elif text[index] in '\r\n':index+=1
                else:output.append(text[index]);index+=1
            else:output.append(char)
        return ''.join(output),index,False
    while i<len(text):
        if len(found)>=limit:complete=False;break
        if text.startswith('/*',i):
            end=text.find('*/',i+2)
            if end<0:complete=False;break
            i=end+2;continue
        if text[i].isspace():i+=1;continue
        if text[i:i+7].lower()=='@import' and (i+7==len(text) or not re.match(r'[\w-]',text[i+7])):
            importing=True;i+=7;continue
        if text[i] in ('"',"'"):
            offset=i;value,i,closed=string(i+1,text[i]);complete=complete and closed
            if importing:found.append({'raw_url':value,'kind':'stylesheet','locator':'css:'+str(offset),'reference_type':'css_import'})
            importing=False;continue
        match=url_function.match(text,i) if i==0 or not re.match(r'[\w-]',text[i-1]) else None
        if match:
            offset=i;i=match.end()
            while i<len(text) and text[i].isspace():i+=1
            if i<len(text) and text[i] in ('"',"'"):
                value,i,closed=string(i+1,text[i]);complete=complete and closed
                while i<len(text) and text[i].isspace():i+=1
                if i>=len(text) or text[i]!=')' or not closed:
                    complete=False;continue
                else:i+=1
            else:
                # Unquoted escapes are left unknown rather than producing a wrong URL.
                end=text.find(')',i)
                if end<0:complete=False;break
                value=text[i:end].strip();i=end+1
                if '\\' in value or '(' in value or '/*' in value or any(c.isspace() for c in value):complete=False;continue
            if value.startswith('#') or not value:
                importing=False;continue
            try:path=urlsplit(value).path.lower()
            except ValueError:path=''
            kind='stylesheet' if importing else 'font' if path.endswith(('.woff','.woff2','.ttf','.otf','.eot')) else 'css_asset'
            found.append({'raw_url':value,'kind':kind,'locator':'css:'+str(offset),'reference_type':'css_import' if importing else 'css_url'})
            importing=False;continue
        if text[i]==';':importing=False
        i+=1
    return found,complete


def tag_resources(parser,tag,attrs,location):
    def add(raw,kind,attribute):
        if not raw:return
        if len(parser.asset_candidates)>=MAX_REFERENCES:
            parser.assets_complete=False;return
        if len(raw)>4096:parser.assets_complete=False;return
        parser.asset_candidates.append({**location,'raw_url':raw,'kind':kind,'reference_type':attribute})
    if tag=='a' and known_file(attrs.get('href','')):add(attrs.get('href',''),'download','href')
    if tag in ('img','source'):
        add(attrs.get('src',''),'image' if tag=='img' else 'media','src')
        for attribute in ('srcset','data-srcset'):
            for raw in srcset_urls(attrs.get(attribute,'')):add(raw,'image',attribute)
        for attribute in ('data-src','data-lazy-src','data-original'):add(attrs.get(attribute,''),'image',attribute)
    elif tag in ('script','iframe','video','audio','embed','input'):
        add(attrs.get('src',''),{'script':'script','iframe':'frame','input':'image'}.get(tag,'media'),'src')
    elif tag=='object':add(attrs.get('data',''),'object','data')
    if tag=='video':add(attrs.get('poster',''),'image','poster')
    if tag=='link':
        for raw in srcset_urls(attrs.get('imagesrcset','')):add(raw,'image','imagesrcset')
        rel=set(attrs.get('rel','').lower().split())
        if rel & {'stylesheet','preload','modulepreload','icon','apple-touch-icon','image_src'}:
            kind='stylesheet' if 'stylesheet' in rel else 'image' if rel & {'icon','apple-touch-icon','image_src'} else 'script' if 'modulepreload' in rel else {'style':'stylesheet','font':'font','image':'image','script':'script'}.get(attrs.get('as','').lower(),'preload')
            add(attrs.get('href',''),kind,'link:'+','.join(sorted(rel)))
    if tag=='meta' and (attrs.get('property') or attrs.get('name','')).lower() in ('og:image','og:image:url','og:image:secure_url','twitter:image','twitter:image:src'):
        add(attrs.get('content',''),'image','social_image')
    if attrs.get('style'):
        css=attrs['style'][:65536];refs,complete=css_urls(css)
        parser.assets_complete &= complete and len(attrs['style'])<=65536
        for item in refs:add(item['raw_url'],item['kind'],'style:'+item['locator'])


def finish_resources(parser, fields, base):
    for block in parser.inline_styles:
        refs,complete=css_urls(block['text'])
        parser.assets_complete &= complete and not block['truncated']
        for item in refs:
            if len(parser.asset_candidates)>=MAX_REFERENCES:parser.assets_complete=False;break
            parser.asset_candidates.append({**block['location'],**item,'locator':block['location']['locator']+':'+item['locator']})
    fields['resource_candidates']=[dict(item,url=urljoin(base,item['raw_url'])) for item in parser.asset_candidates]
    fields['resource_discovery']={'version':'resources-v1','complete':parser.complete and parser.assets_complete,
        'basis':'HTML attributes, srcset, explicit lazy attributes and bounded inline CSS; no arbitrary JavaScript execution'}


def known_file(url):
    try:return urlsplit(url).path.lower().endswith(('.pdf','.zip','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.jpg','.jpeg','.png','.gif','.webp','.avif','.svg','.ico','.css','.js','.woff','.woff2','.ttf','.mp4','.mp3','.webm'))
    except ValueError:return False
