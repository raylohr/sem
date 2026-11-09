"""Dynamic field catalogue, preserving source, report and original field path."""
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, Context
from .content_types import content_badge

SOURCE_NAMES = {'sf':'SF', 'gsc':'GSC', 'ahrefs':'AHREFS', 'seranking':'SE Ranking'}
from .sf_reports import REPORTS

from integrations.seranking import REPORTS as SERANKING_REPORTS

REPORT_NAMES = {key: label for key, (label, scope) in REPORTS.items()} | {'internal_all':'Internal All', 'gsc_page_indexing':'Page indexing',
                'search_analytics':'Search performance', 'inspection':'URL Inspection',
                'ahrefs_site_audit_pages':'Site Audit · Pages',
                'ahrefs_site_audit_missing_titles':'Site Audit · Missing titles', '@response':'Response'}
REPORT_NAMES.update({key: label for key, (label, scope) in SERANKING_REPORTS.items()})


def present(value):
    return value is not None and (not isinstance(value,str) or bool(value.strip()))


def number(value):
    if isinstance(value,bool) or not present(value):
        return None
    if isinstance(value, Decimal):
        return value.normalize(context=Context(prec=1000)) if value.is_finite() and abs(value.adjusted()) <= 308 else None
    try:
        text=str(value).strip()
        if len(text)>128:
            return None
        result=Decimal(text)
        return result if result.is_finite() and abs(result.adjusted())<=308 else None
    except (InvalidOperation,ValueError):
        return None


def calendar_date(value):
    if isinstance(value,datetime):
        return value.date()
    if isinstance(value,date):
        return value
    if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?',value):
        try:return date.fromisoformat(value[:10])
        except ValueError:pass
    return None


def scalar_fields(data,path=()):
    if isinstance(data,dict):
        for key,value in data.items():
            yield from scalar_fields(value,path+(str(key),))
    elif isinstance(data,list):
        # Lists remain an intact value rather than producing hundreds of unstable columns.
        yield path,json.dumps(data,ensure_ascii=False)
    else:
        yield path,data


def record_fields(source,metadata,data,cache):
    report=metadata.get('type') or metadata.get('kind') or ('search_analytics' if source=='gsc' else 'internal_all' if source=='sf' else 'records')
    values=list(scalar_fields(data))
    if source=='gsc':
        for key in ('gsc_report_status','gsc_indexing_reason'):
            if key not in data:
                value=metadata.get('report_status' if key=='gsc_report_status' else 'indexing_reason')
                if value is not None:values.append(((key,),value))
    if source=='sf' and ('Content Type' in data or 'Status Code' in data):
        badge=content_badge(data)
        values.append((('@contenttype',),badge['label']))
    for path,value in values:
        detail='@response' if path==('@contenttype',) else str(report)
        identity=(source,detail,path)
        if identity not in cache:
            field_id=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
            cache[identity]={'id':field_id,'source':source,'source_label':SOURCE_NAMES.get(source,source.replace('_',' ').capitalize()),
                'report':detail,'report_label':REPORT_NAMES.get(detail,detail),
                'path':list(path),'label':'contenttype' if path==('@contenttype',) else ' → '.join(path),
                'presentation':'contenttype' if path==('@contenttype',) else 'value'}
        yield cache[identity],value


def field_catalog(rows):
    catalog={}
    for row in rows:
        for field_id,observation in row.get('field_values',{}).items():
            if field_id not in catalog:
                catalog[field_id]={**observation['field'],'values':[]}
            if present(observation['value']):catalog[field_id]['values'].append(observation['value'])
    for field in catalog.values():
        values=field.pop('values')
        label=field['label'].lower()
        force_text=any(word in label for word in ('title','description','address','url','canonical','name')) and not any(word in label for word in ('length','width','count','score'))
        numeric_name=any(word in label for word in ('traffic','clicks','impressions','ctr','position','count','length','width','depth','score','size','bytes','inlinks','outlinks','backlinks','referring domains','status code')) or label=='pr'
        field['type']='number' if not force_text and (numeric_name or values and all(number(v) is not None for v in values)) else 'date' if values and not force_text and all((isinstance(v,date) or isinstance(v,str) and len(v)==10) and calendar_date(v) is not None for v in values) else 'text'
    return catalog


def typed_value(value,kind):
    if not present(value):return None
    if kind=='number':return number(value)
    if kind=='date':return calendar_date(value)
    return str(value)


def value_key(value,kind):
    value=typed_value(value,kind)
    if value is None:return '__missing__'
    if kind=='number':
        text=format(value,'f')
        if '.' in text:text=text.rstrip('0').rstrip('.')
        if value==0:text='0'
    elif kind=='date':text=value.isoformat()
    else:text=value
    return hashlib.sha256(text.encode()).hexdigest()
