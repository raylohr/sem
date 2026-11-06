from decimal import Decimal
from django.http import QueryDict
from django.test import SimpleTestCase
from .column_fields import present,typed_value,value_key
from .configurable_inventory import legacy_params,build_table,short,number_display,choices_for


class InventoryControlsTests(SimpleTestCase):
    def rows_columns(self):
        rows=[{'url':'https://example.com/a','path':'/a','field_values':{
            'content':{'value':'HTML'},'status':{'value':'not_indexed'},'traffic':{'value':'10'}}},
            {'url':'https://example.com/b','path':'/b','field_values':{
            'content':{'value':'PDF'},'status':{'value':'indexed'},'traffic':{'value':'0'}}}]
        columns=[{'id':key,'field_id':field,'source':key,'source_label':key,'report_label':'Report','label':field,
                  'name':field,'type':kind,'mode':'choices','available':True,'presentation':'value'}
                 for key,field,kind in [('sf','content','text'),('gsc','status','text'),('ahrefs','traffic','number')]]
        return rows,columns

    def test_legacy_links_translate_into_current_filter_state(self):
        rows,columns=self.rows_columns()
        params=legacy_params(QueryDict('traffic_sort=desc&content_filter=1&content_type=html&index_filter=1&index_status=not_indexed'),columns,rows)
        result,state=build_table(rows,columns,params)
        self.assertEqual([row['path'] for row in result],['/a'])
        self.assertIn('sort=ahrefs%3Adesc',state['url_state_query'])
        self.assertIn('a_sf=1',state['url_state_query'])
        self.assertIn('a_gsc=1',state['url_state_query'])
        self.assertNotIn('traffic_sort',state['url_state_query'])

    def test_removing_a_column_ignores_old_filter_and_sort_ids(self):
        rows,columns=self.rows_columns()
        params=QueryDict('sort=removed:asc&a_removed=1&v_removed=anything')
        result,state=build_table(rows,columns,params)
        self.assertEqual(len(result),2)
        self.assertNotIn('removed',state['url_state_query'])
        self.assertFalse(state['sort_active'])

    def test_display_limits_and_numeric_identity_preserve_zero(self):
        self.assertEqual(len(short('x'*100,40)),40)
        self.assertEqual(len(short('x'*100,24)),24)
        self.assertLessEqual(len(number_display(Decimal('12345678901234567890'))),12)
        self.assertTrue(present(0));self.assertTrue(present(False));self.assertFalse(present('  '))
        self.assertEqual(value_key('1.00','number'),value_key('1','number'))
        self.assertEqual(typed_value('0','number'),0)
        self.assertNotEqual(value_key(None,'number'),value_key(0,'number'))

    def test_low_cardinality_numeric_choices_are_sorted_numerically(self):
        rows=[{'field_values':{'n':{'value':value}}} for value in ('10','2','0')]
        options=choices_for(rows,{'id':'n','type':'number'})
        self.assertEqual([value for _,value in options],[0,2,10])
