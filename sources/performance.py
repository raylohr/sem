"""Project-scoped HTML URL scatter data from published source observations."""
import math
from .column_fields import field_catalog, number


def latest_value(row, source, path):
    observations=[item for item in row.get('field_values',{}).values()
                  if item['field']['source']==source and item['field']['path']==path]
    return max(observations,key=lambda item:item['rank'])['value'] if observations else None


def scatter_data(rows, requested='', *, catalog=None):
    catalog=field_catalog(rows) if catalog is None else catalog
    fields=sorted((field for field in catalog.values() if field['type']=='number'),
                  key=lambda field:(field['source_label'],field['report_label'],field['label']))
    default=next((field for field in fields if field['source']=='ahrefs' and field['path']==['No. of backlinks'] and field['report']=='ahrefs_site_audit_pages'),None)
    if default is None:
        default=next((field for field in fields if field['source']=='ahrefs' and field['path']==['No. of backlinks']),None)
    selected=next((field for field in fields if field['id']==requested),default)
    points=[];html_count=0;x_count=0;y_count=0
    for row in rows:
        content=latest_value(row,'sf',['@contenttype'])
        if content is None:
            mime=latest_value(row,'ahrefs',['Content type'])
            content='HTML' if isinstance(mime,str) and mime.split(';')[0].strip().lower() in ('text/html','application/xhtml+xml') else None
        if content!='HTML':continue
        html_count+=1
        x=number(row.get('field_values',{}).get(selected['id'],{}).get('value')) if selected else None
        y=number(latest_value(row,'ahrefs',['Organic traffic']))
        x_count+=x is not None
        y_count+=y is not None
        if x is None or y is None or x<0 or y<0:continue
        x,y=float(x),float(y)
        if not math.isfinite(x) or not math.isfinite(y):continue
        points.append({'url':row['url'],'title':row['title'] or '—','x':x,'y':y})
    return {'performance_fields':fields,'performance_x':selected,'performance_points':points,
            'performance_html_count':html_count,'performance_missing_count':html_count-len(points),
            'performance_x_count':x_count,'performance_y_count':y_count,
            # A field from a narrower export silently shrinks the chart; say so.
            'performance_x_sparse':bool(selected) and html_count>0 and x_count<html_count/2}


def indexed_scatter(inventory, requested=''):
    """Read only axis/content fields; stream URLs instead of expanding all fields."""
    from .inventory_index import InventoryRows
    fields = inventory.catalog
    options = scatter_data([], requested, catalog=fields)
    selected = options['performance_x']
    keys = {key for key, field in fields.items() if
        (field['source'], field['path']) in (
            ('ahrefs', ['Organic traffic']), ('ahrefs', ['Content type']), ('sf', ['@contenttype']))}
    if selected: keys.add(selected['id'])
    return scatter_data(InventoryRows(inventory.query, keys), requested, catalog=fields)
