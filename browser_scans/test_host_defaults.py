from django.test import SimpleTestCase
from . import tests as fixtures


class OpenResourceBrokerTests(SimpleTestCase):
    def test_public_dependencies_allowed_subdomain_navigation_allowed_but_external_navigation_private_and_post_blocked(self):
        messages=[{'type':'request','url':url,'method':method,'main_navigation':navigation} for url,method,navigation in (
            ('https://fonts.googleapis.com/font.css','GET',False),
            ('https://fonts.gstatic.com/font.woff2','GET',False),
            ('https://new.vendor.com/runtime.js','GET',False),
            ('https://blog.example.com/page','GET',True),
            ('https://new.vendor.com/','GET',True),
            ('https://private.example.com/','GET',True),
            ('https://blocked.vendor.com/a.js','GET',False),
            ('http://127.0.0.1/','GET',False),
            ('https://new.vendor.com/form','POST',False))]
        messages.append({'type':'result','final_url':'https://blog.example.com/page'})
        _,calls,outputs=fixtures.BrokerPolicyTests().broker(messages,config_overrides={'domain':'example.com','include_subdomains':True,'allow_external_resources':True,'blocked_hosts':['private.example.com'],'blocked_resource_hosts':['blocked.vendor.com']})
        self.assertEqual(len(calls),4)
        self.assertEqual([row['allowed'] for row in outputs[1:]],[True,True,True,True,False,False,False,False,False])
