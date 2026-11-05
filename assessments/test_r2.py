from types import SimpleNamespace
from django.test import SimpleTestCase
from scans.extraction import extract_page
from . import r2


class R2EvidenceTests(SimpleTestCase):
    def extract(self, html, **extra):
        result=SimpleNamespace(url='https://example.com/',status=200,headers={'content-type':'text/html'},body=html.encode(),**extra)
        return extract_page(result)[0]

    def checks(self, facts, *, settings=None, coverage=None, links=None):
        states={};details={}
        def check(rule,state,evidence=None):
            states[rule]=state;details[rule]=evidence
        page=SimpleNamespace(facts=facts,coverage=coverage or {},final_url='https://example.com/')
        r2.local(SimpleNamespace(page=page,settings=settings or {},blank_links=links or [],check=check))
        return states,details

    def test_source_locations_attributes_and_context(self):
        facts=self.extract('<html><head><title>Title</title></head><body><header><nav><a href="/x" target="_blank" aria-labelledby="label">X</a></nav></header><main><img src="/i" width="320" height="200" srcset="/i2 2x"><span id="label">Name</span></main></body></html>')
        link=facts['link_details'][0]
        self.assertEqual(link['region'],'header')
        self.assertEqual(link['raw_url'],'/x')
        self.assertEqual(link['element_path'],'/html[1]/body[1]/header[1]/nav[1]/a[1]')
        self.assertEqual(link['aria_labelledby'],['label'])
        self.assertEqual(facts['images'][0]['width'],'320')
        self.assertEqual(facts['aria_references'][0]['text'],'Name')

    def test_nested_document_metadata_and_dimensions(self):
        facts=self.extract('<html><head><title>Outer</title></head><body><html><head><title>Inner</title></head><body><img src="/x" width="²" height="20"></body></html></body></html>')
        states,details=self.checks(facts)
        self.assertEqual(states['html_nested_document'],'issue')
        self.assertEqual(states['metadata_outside_head'],'issue')
        self.assertEqual(states['image_dimensions_invalid'],'issue')
        self.assertEqual(details['html_nested_document']['counts']['html'],2)

    def test_identical_canonicals_are_advice_conflicts_are_separate(self):
        facts=self.extract('<html><head><link rel="canonical" href="/"><link rel="canonical" href="https://example.com/"></head></html>')
        states,_=self.checks(facts)
        self.assertEqual(states['canonical_duplicate_identical'],'issue')
        self.assertEqual(states['canonical_conflicting'],'pass')
        self.assertEqual(r2.RULES['canonical_duplicate_identical'][1],'low')
        facts['canonicals'].append('https://example.com/other')
        states,_=self.checks(facts)
        self.assertEqual(states['canonical_conflicting'],'issue')
        self.assertEqual(states['canonical_duplicate_identical'],'pass')

    def test_thresholds_and_incomplete_evidence(self):
        facts=self.extract('<html><head><title>'+('X'*60)+'</title></head></html>')
        self.assertEqual(self.checks(facts)[0]['title_too_long'],'pass')
        self.assertEqual(self.checks(facts,settings={'title_max_chars':59})[0]['title_too_long'],'issue')
        self.assertEqual(self.checks(facts,coverage={'analysis_truncated':True})[0]['title_too_long'],'unknown')
        self.assertEqual(self.checks(facts,settings={'title_max_chars':59},coverage={'analysis_truncated':True})[0]['title_too_long'],'issue')
        for raw in ([],{'title_min_chars':100,'title_max_chars':50},{'heading_max_chars':0},{'title_max_chars':True}):
            with self.assertRaises(ValueError):r2.settings_for(raw)

    def test_header_observation_and_blank_link_scope(self):
        facts=self.extract('<html><head></head><body></body></html>')
        facts['headers_observed']=False
        self.assertEqual(self.checks(facts)[0]['header_missing_content_security_policy'],'unknown')
        links=[{'destination':'https://other.example/','rel':[],'element_path':'/html[1]/body[1]/a[1]'}]
        self.assertEqual(self.checks(facts,links=links)[0]['external_blank_rel'],'issue')
        links[0]['rel']=['noopener']
        self.assertEqual(self.checks(facts,links=links)[0]['external_blank_rel'],'pass')
        links[0].update(destination='https://example.com/internal',rel=[])
        self.assertEqual(self.checks(facts,links=links)[0]['external_blank_rel'],'pass')

    def test_implicit_head_and_bounded_parser_do_not_claim_pass(self):
        facts=self.extract('<title>Fragment title</title><link rel="canonical" href="/">')
        self.assertEqual(self.checks(facts)[0]['metadata_outside_head'],'unknown')
        self.assertEqual(self.checks(facts)[0]['canonical_outside_head'],'unknown')
        facts=self.extract('<div>'*130+'<img src="/x">'+'</div>'*130)
        self.assertFalse(facts['html_audit']['complete'])
        self.assertEqual(self.checks(facts)[0]['metadata_outside_head'],'unknown')

    def test_score_catalog_excludes_new_advisories(self):
        from reporting.catalog import TECHNICAL
        self.assertNotIn('canonical_duplicate_identical',TECHNICAL)
