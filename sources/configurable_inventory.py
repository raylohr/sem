"""User-scoped column configuration, typed filters and complete-value sorting."""
from collections import Counter
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4
from django import forms
from django.utils.formats import number_format
from .column_fields import field_catalog,present,typed_value,value_key,number,calendar_date
from .models import InventoryPreference

MAX_COLUMNS=5


def choices_for(rows,field):
    choices={}
    for row in rows:
        value=row.get('field_values',{}).get(field['id'],{}).get('value')
        typed=typed_value(value,field['type'])
        if typed is not None:choices[value_key(value,field['type'])]=typed
    return sorted(choices.items(),key=lambda item:item[1].casefold() if isinstance(item[1],str) else item[1])


def configured_field(field,rows,column_id=None,label=''):
    mode='date' if field['type']=='date' else 'choices' if len(choices_for(rows,field))<10 else 'range' if field['type']=='number' else 'keyword'
    return {key:field[key] for key in ('id','source','source_label','report','report_label','label','type','presentation')} | {
        'field_id':field['id'],'id':column_id or uuid4().hex,'name':label or field['label'], 'mode':mode}


def default_columns(catalog,rows):
    result=[]
    for source,path,name in [('ahrefs',['Organic traffic'],'o-traffic'),('gsc',['gsc_report_status'],'Index Status'),('sf',['@contenttype'],'contenttype')]:
        matches=[field for field in catalog.values() if field['source']==source and field['path']==path]
        if matches:
            field=sorted(matches,key=lambda item:(item['report']!='ahrefs_site_audit_pages',item['report']))[0]
            result.append(configured_field(field,rows,source,name))
    used={column['source'] for column in result}
    for source in sorted({field['source'] for field in catalog.values()}-used):
        preferred=[['raw','clicks'],['date'],['Last crawled']] if source=='gsc' else []
        candidates=sorted((field for field in catalog.values() if field['source']==source),
            key=lambda field:(preferred.index(field['path']) if field['path'] in preferred else len(preferred),field['path'] in (['url'],['URL'],['Address']),field['label']))
        if candidates and len(result)<MAX_COLUMNS:
            result.append(configured_field(candidates[0],rows,source,candidates[0]['label']))
    return result


def selected_columns(project,user,catalog,rows):
    preference=InventoryPreference.objects.filter(project=project,user=user).first()
    columns=preference.columns if preference else default_columns(catalog,rows)
    result=[]
    for saved in columns[:MAX_COLUMNS]:
        field=catalog.get(saved['field_id'])
        result.append({**saved,'available':field is not None,
            'type':saved.get('type',field['type'] if field else 'text')})
    return result


class ColumnConfigurationForm(forms.Form):
    def __init__(self,*args,catalog,columns,**kwargs):
        super().__init__(*args,**kwargs)
        self.catalog,self.columns=catalog,columns
        groups={}
        for field in sorted(catalog.values(),key=lambda field:(field['source_label'],field['report_label'],field['label'])):
            group=f"{field['source_label']} / {field['report_label']}"
            groups.setdefault(group,[]).append((field['id'],field['label']))
        for index in range(MAX_COLUMNS):
            saved=columns[index] if index<len(columns) else None
            choices=[('','Geen kolom / verwijderen'),*groups.items()]
            if saved and saved['field_id'] not in catalog:
                choices.append((saved['field_id'],'Niet beschikbaar: '+saved['name']))
            self.fields[f'field_{index}']=forms.ChoiceField(required=False,choices=choices,
                initial=saved['field_id'] if saved else '',widget=forms.Select(attrs={'class':'form-select','data-column-field':''}))
            self.fields[f'name_{index}']=forms.CharField(required=False,max_length=80,
                initial=saved['name'] if saved else '',widget=forms.TextInput(attrs={'class':'form-control','placeholder':'Veldnaam gebruiken','data-column-name':''}))

    @property
    def slots(self):
        return [{'number':index+1,'field':self[f'field_{index}'],'name':self[f'name_{index}']} for index in range(MAX_COLUMNS)]

    def configuration(self,rows):
        result=[]
        for index in range(MAX_COLUMNS):
            field_id=self.cleaned_data[f'field_{index}']
            if not field_id:continue
            old=self.columns[index] if index<len(self.columns) else None
            field=self.catalog.get(field_id)
            if field is None:
                result.append({**old,'name':self.cleaned_data[f'name_{index}'] or old['name']})
            else:
                # Saving is the explicit point at which filter mode may be recalculated.
                result.append(configured_field(field,rows,old['id'] if old and old['field_id']==field_id else None,self.cleaned_data[f'name_{index}']))
        return result


def short(value,limit):
    return value if len(value)<=limit else value[:limit-3]+'...'


def display(value,kind):
    if value is None:return '—'
    if kind=='date':return value.strftime('%d.%m.%Y')
    if kind=='number':return str(number_format(value,use_l10n=True))
    return str(value)


def number_display(value):
    full=display(value,'number')
    if len(full)<=12:return full
    for threshold,suffix in [(Decimal('1e12'),'T'),(Decimal('1e9'),'B'),(Decimal('1e6'),'M'),(Decimal('1e3'),'K')]:
        if abs(value)>=threshold:
            return short(f'{value/threshold:.2f}'.rstrip('0').rstrip('.')+suffix,12)
    return short(f'{value:.4g}',12)


def build_table(rows,columns,params):
    query=params.get('url_q','').strip()[:500]
    sort=params.get('sort','')
    valid_ids={column['id'] for column in columns}
    sort_id,_,direction=sort.partition(':')
    if sort_id not in valid_ids or direction not in ('asc','desc'):sort_id,direction='',''
    state=[('url_q',query)] if query else []
    if sort_id:state.append(('sort',sort_id+':'+direction))
    values={}
    errors=[]
    for column in columns:
        key=column['id'];kind=column['type']
        values[key]={row['url']:typed_value(row.get('field_values',{}).get(column['field_id'],{}).get('value'),kind) for row in rows}
        opts={}
        for value in values[key].values():
            if value is not None:opts[value_key(value,kind)]=value
        column['choices']=[{'key':option,'label':display(value,kind)} for option,value in sorted(opts.items(),key=lambda item:item[1].casefold() if isinstance(item[1],str) else item[1])]
        column['selected']=set()
        column['low']=params.get('lo_'+key,'').strip()[:128]
        column['high']=params.get('hi_'+key,'').strip()[:128]
        column['keyword']=params.get('q_'+key,'').strip()[:200]
        column['missing']=params.get('m_'+key,'auto')
        if column['missing'] not in ('auto','include','exclude','only'):column['missing']='auto'
        column['error']=''
        if column['mode']=='choices':
            column['active']=params.get('a_'+key)=='1'
            available=set(opts)|{'__missing__'}
            column['selected']=set(params.getlist('v_'+key)) & available if column['active'] else available
            if column['active']:
                state.append(('a_'+key,'1'));state.extend(('v_'+key,value) for value in sorted(column['selected']))
        else:
            column['active']=bool(column['low'] or column['high'] or column['keyword'] or column['missing']!='auto')
            for prefix,value in [('lo_',column['low']),('hi_',column['high']),('q_',column['keyword'])]:
                if value:state.append((prefix+key,value))
            if column['missing']!='auto':state.append(('m_'+key,column['missing']))
            parse=calendar_date if column['mode']=='date' else number
            column['lower']=parse(column['low']) if column['low'] else None
            column['upper']=parse(column['high']) if column['high'] else None
            if column['mode'] in ('range','date'):
                if (column['low'] and column['lower'] is None) or (column['high'] and column['upper'] is None):column['error']='Ongeldige grenswaarde voor '+column['name']
                elif column['lower'] is not None and column['upper'] is not None and column['lower']>column['upper']:column['error']='Ondergrens is hoger dan bovengrens voor '+column['name']
            if column['error']:errors.append(column['error'])
    def matches(row,column):
        if not column['active']:return True
        if column['error']:return False
        value=values[column['id']][row['url']]
        if column['mode']=='choices':return value_key(value,column['type']) in column['selected']
        if column['missing']=='only':return value is None
        if value is None:
            return column['missing']=='include' or (column['missing']=='auto' and not(column['low'] or column['high'] or column['keyword']))
        if column['mode']=='keyword':return column['keyword'].casefold() in str(value).casefold()
        return (column['lower'] is None or value>=column['lower']) and (column['upper'] is None or value<=column['upper'])
    searched=[row for row in rows if not query or query.casefold() in row['url'].casefold()]
    result=[row for row in searched if all(matches(row,column) for column in columns)]
    for column in columns:
        key=column['id'];column['present_count']=sum(values[key][row['url']] is not None for row in result)
        column['total_count']=len(result)
        counts=Counter(value_key(values[key][row['url']],column['type']) for row in searched if all(matches(row,other) for other in columns if other['id']!=key))
        for option in column['choices']:
            option.update(count=counts[option['key']],checked=option['key'] in column['selected'])
        column['missing_count']=counts['__missing__'];column['missing_checked']='__missing__' in column['selected']
        column['aria_sort']='ascending' if key==sort_id and direction=='asc' else 'descending' if key==sort_id else 'none'
        next_direction='desc' if key==sort_id and direction=='asc' else '' if key==sort_id and direction=='desc' else 'asc'
        filtered_state=[pair for pair in state if pair[0]!='sort']
        if next_direction:filtered_state.append(('sort',key+':'+next_direction))
        column['sort_url']='?'+urlencode(filtered_state)+'#url-inventory'
        own={prefix+key for prefix in ('a_','v_','lo_','hi_','q_','m_')}
        column['reset_url']='?'+urlencode([pair for pair in state if pair[0] not in own])+'#url-inventory'
        column['state']=[{'name':name,'value':value} for name,value in state if name not in own]
    if sort_id:
        known=[row for row in result if values[sort_id][row['url']] is not None]
        missing=[row for row in result if values[sort_id][row['url']] is None]
        # Stable ties preserve URL-path order, in either direction.
        result=sorted(known,key=lambda row:values[sort_id][row['url']].casefold() if isinstance(values[sort_id][row['url']],str) else values[sort_id][row['url']],reverse=direction=='desc')+missing
    for row in result:
        cells=[]
        for column in columns:
            observation=row.get('field_values',{}).get(column['field_id'])
            value=values[column['id']][row['url']]
            full=display(value,column['type'])
            badge=column.get('presentation')=='contenttype' or column['mode']=='choices' and column['type']=='text'
            text=number_display(value) if column['type']=='number' and value is not None else short(full,24 if badge else 40)
            tone='html' if full=='HTML' else 'error' if full=='Geen response' or full.startswith(('HTTP 4','HTTP 5','HTTP 9')) else 'other'
            cells.append({'text':text,'full':full,'value':value,'badge':badge,'tone':tone,'observation':observation,
                          'source':column['source'],'unavailable':not column['available']})
        row['value_cells']=cells
    return result,{'value_columns':columns,'table_width':360+170*len(columns),'url_query':query,
        'url_state_query':urlencode(state),'table_state':[{'name':name,'value':value} for name,value in state],
        'search_state':[{'name':name,'value':value} for name,value in state if name!='url_q'],
        'url_filtered_count':len(result),'table_errors':errors,'filters_active':any(c['active'] for c in columns),
        'sort_active':bool(sort_id)}


def legacy_params(params,columns,rows):
    """Keep previously shared inventory links functional after upgrading the layout."""
    from django.utils.text import slugify
    result=params.copy()
    by_id={column['id']:column for column in columns}
    if 'sort' not in result and params.get('traffic_sort') in ('asc','desc') and 'ahrefs' in by_id:
        result['sort']='ahrefs:'+params['traffic_sort']
    for column_id,flag,values_name in [('sf','content_filter','content_type'),('gsc','index_filter','index_status')]:
        column=by_id.get(column_id)
        if column and params.get(flag)=='1' and 'a_'+column_id not in result:
            wanted=set(params.getlist(values_name));selected=[]
            for row in rows:
                raw=row.get('field_values',{}).get(column['field_id'],{}).get('value')
                label=slugify(str(raw)) if column_id=='sf' else str(raw)
                if label in wanted or (raw is None and ('not-observed' in wanted or '__no_gsc__' in wanted)):
                    selected.append(value_key(raw,column['type']))
            result['a_'+column_id]='1';result.setlist('v_'+column_id,sorted(set(selected)))
    return result


def build_dataset_table(rows, columns, params, catalog):
    """Filter any dataset field independently of the displayed value columns."""
    requested=params.get('filter_field','')
    filters=[]
    for field in catalog.values():
        key='field_'+field['id']
        if field['id']==requested or any(params.get(prefix+key) for prefix in ('a_','lo_','hi_','q_','m_')):
            filters.append({**configured_field(field,rows,key),'available':True})
    result,controls=build_table(rows,columns+filters,params)
    controls['value_columns']=columns
    controls['table_width']=360+170*len(columns)
    for row in result:row['value_cells']=row['value_cells'][:len(columns)]
    # Existing bookmarked column filters remain editable during the transition.
    active=[column for column in columns+filters if column['active']]
    chosen=next((column for column in filters if column['field_id']==requested),None)
    if chosen is None:
        chosen=next((column for column in active if column['id']==params.get('edit_filter')),None)
    controls.update(dataset_filters=active,filter_editor=chosen,filter_catalog=sorted(catalog.values(),key=lambda f:(f['source_label'],f['report_label'],f['label'])))
    # An explicit marker distinguishes clearing every filter from returning to the page.
    controls['url_state_query'] += '&inventory_state=1'
    for name in ('table_state','search_state'):
        controls[name].append({'name':'inventory_state','value':'1'})
    for column in columns+filters:
        for name in ('sort_url','reset_url'):
            column[name]=column[name].replace('#url-inventory','&inventory_state=1#url-inventory')
        column['state'].append({'name':'inventory_state','value':'1'})
    return result,controls


def inventory_metrics(rows):
    """Count URLs, using explicit Ahrefs keyword counts and crawler HTTP status."""
    def latest(row,source,path):
        observations=[item for item in row.get('field_values',{}).values() if item['field']['source']==source and item['field']['path']==[path]]
        return number(max(observations,key=lambda item:item['rank'])['value']) if observations else None
    result={}
    for limit in (3,10):
        values=[latest(row,'ahrefs',f'No. of keywords with Top {limit} position') for row in rows]
        result[f'top{limit}']=sum(value>0 for value in values if value is not None) if any(value is not None for value in values) else None
    statuses=[]
    for row in rows:
        status=latest(row,'sf','Status Code')
        if status is None:status=latest(row,'ahrefs','HTTP status code')
        statuses.append(status)
    result['not_found']=sum(status==404 for status in statuses) if any(status is not None for status in statuses) else None
    return result
