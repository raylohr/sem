from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from django.http import QueryDict
from django.test import SimpleTestCase
from .inventory_controls import inventory_controls


def row(path, traffic=None, badge=None):
    observations = {}
    if traffic is not None:
        observations['ahrefs'] = {'traffic': Decimal(str(traffic))}
    if badge:
        observations['sf'] = {'badge': {'label': badge}}
    return {'url':'https://example.com'+path,'path':path,'observations':observations}


class InventoryControlsTests(SimpleTestCase):
    columns = [{'key':'ahrefs'},{'key':'sf'}]

    def test_traffic_sort_is_numeric_missing_last_and_cycles_back_to_path_order(self):
        rows=[row('/a',100),row('/b',None),row('/c',0),row('/d',9),row('/e',9)]
        ascending, state=inventory_controls(rows,self.columns,QueryDict('traffic_sort=asc'))
        self.assertEqual([r['path'] for r in ascending],['/c','/d','/e','/a','/b'])
        self.assertEqual(state['traffic_aria_sort'],'ascending')
        self.assertEqual(parse_qs(urlsplit(state['traffic_sort_url']).query)['traffic_sort'],['desc'])
        descending,state=inventory_controls(rows,self.columns,QueryDict('traffic_sort=desc'))
        self.assertEqual([r['path'] for r in descending],['/a','/d','/e','/c','/b'])
        self.assertNotIn('traffic_sort',parse_qs(urlsplit(state['traffic_sort_url']).query))
        default,state=inventory_controls(rows,self.columns,QueryDict())
        self.assertEqual([r['path'] for r in default],['/a','/b','/c','/d','/e'])
        self.assertIn('traffic_sort=asc',state['traffic_sort_url'])

    def test_content_filter_multi_select_counts_search_and_missing_sf(self):
        rows=[row('/a',10,'HTML'),row('/b',2,'PDF'),row('/c',1,'HTTP 404'),row('/d',None)]
        selected,state=inventory_controls(rows,self.columns,QueryDict('content_filter=1&content_type=html&content_type=pdf&traffic_sort=desc'))
        self.assertEqual([r['path'] for r in selected],['/a','/b'])
        self.assertEqual({c['key']:c['count'] for c in state['content_choices']},{'html':1,'pdf':1,'http-404':1,'not-observed':1})
        self.assertIn('content_type=html',state['url_state_query'])
        self.assertIn('traffic_sort=desc',state['content_filter_reset_url'])
        self.assertNotIn('content_filter',state['content_filter_reset_url'])
        selected,state=inventory_controls(rows,self.columns,QueryDict('url_q=/a&content_filter=1&content_type=html'))
        self.assertEqual(len(selected),1)
        self.assertEqual(sum(c['count'] for c in state['content_choices']),1)
        selected,_=inventory_controls(rows,self.columns,QueryDict('content_filter=1&content_type=not-observed'))
        self.assertEqual([r['path'] for r in selected],['/d'])

    def test_none_selected_means_empty_and_invalid_state_is_safe(self):
        rows=[row('/a',1,'HTML')]
        self.assertEqual(inventory_controls(rows,self.columns,QueryDict('content_filter=1'))[0],[])
        self.assertEqual(inventory_controls(rows,self.columns,QueryDict('content_filter=1&content_type=invalid'))[0],[])
        result,state=inventory_controls(rows,self.columns,QueryDict('traffic_sort=invalid'))
        self.assertEqual(result,rows)
        self.assertEqual(state['traffic_sort'],'')
        result,state=inventory_controls(rows,[{'key':'gsc'}],QueryDict('content_filter=1&traffic_sort=desc'))
        self.assertEqual(result,rows)
        self.assertFalse(state['content_filter_active'])
