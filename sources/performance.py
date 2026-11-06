"""Project-scoped HTML URL scatter data from published source observations."""
import math
from .column_fields import field_catalog, number


def latest_value(row, source, path):
    observations=[item for item in row.get('field_values',{}).values()
                  if item['field']['source']==source and item['field']['path']==path]
    return max(observations,key=lambda item:item['rank'])['value'] if observations else None


def scatter_data(rows, requested=''):
    catalog=field_catalog(rows)
    fields=sorted((field for field in catalog.values() if field['type']=='number'),
                  key=lambda field:(field['source_label'],field['report_label'],field['label']))
    default=next((field for field in fields if field['source']=='ahrefs' and field['path']==['No. of backlinks'] and field['report']=='ahrefs_site_audit_pages'),None)
    if default is None:
        default=next((field for field in fields if field['source']=='ahrefs' and field['path']==['No. of backlinks']),None)
    selected=next((field for field in fields if field['id']==requested),default)
    points=[];html_count=0
    for row in rows:
        content=latest_value(row,'sf',['@contenttype'])
        if content is None:
            mime=latest_value(row,'ahrefs',['Content type'])
            content='HTML' if isinstance(mime,str) and mime.split(';')[0].strip().lower() in ('text/html','application/xhtml+xml') else None
        if content!='HTML':continue
        html_count+=1
        x=number(row.get('field_values',{}).get(selected['id'],{}).get('value')) if selected else None
        y=number(latest_value(row,'ahrefs',['Organic traffic']))
        if x is None or y is None or x<0 or y<0:continue
        x,y=float(x),float(y)
        if not math.isfinite(x) or not math.isfinite(y):continue
        points.append({'url':row['url'],'title':row['title'] or '—','x':x,'y':y})
    return {'performance_fields':fields,'performance_x':selected,'performance_points':points,
            'performance_html_count':html_count,'performance_missing_count':html_count-len(points)}
