from types import SimpleNamespace
from django.test import SimpleTestCase
from scans.extraction import extract_page
from . import r3


class R3ContentTests(SimpleTestCase):
    def extract(self, html):
        return extract_page(SimpleNamespace(url='https://example.com/',status=200,
            headers={'content-type':'text/html'},body=html.encode()))[0]

    def analyze(self, html=None, *, facts=None, coverage=None):
        facts=facts if facts is not None else self.extract(html)
        states={};details={}
        def check(rule,state,evidence=None):states[rule]=state;details[rule]=evidence
        r3.local(SimpleNamespace(page=SimpleNamespace(facts=facts,coverage=coverage or {}),check=check))
        return states,details,facts

    def test_empty_alt_is_not_missing_and_explicit_decoration_is_not_flagged(self):
        states,details,facts=self.analyze('<img src="a" alt="" role="presentation"><img src="b" alt="" aria-hidden="true"><img src="c" alt="Description">')
        self.assertEqual(states['image_empty_alt_review'],'pass')
        self.assertEqual([i['alt_interpretation'] for i in facts['images']],['explicitly_decorative','explicitly_decorative','text_present_unverified'])
        self.assertTrue(all(i['alt_present'] for i in facts['images']))

    def test_ambiguous_empty_alt_is_one_page_advice_with_bounded_examples(self):
        states,details,_=self.analyze('<img src="same" alt="">'*50)
        self.assertEqual(states['image_empty_alt_review'],'issue')
        self.assertEqual(details['image_empty_alt_review']['count'],50)
        self.assertEqual(len(details['image_empty_alt_review']['elements']),20)
        self.assertEqual(r3.RULES['image_empty_alt_review'][1],'low')

    def test_image_alt_and_aria_create_link_names(self):
        _,_,facts=self.analyze('<a href="/a"><img src="a" alt="Home"></a><a href="/b" aria-label="Contact"><img src="b" alt=""></a>')
        self.assertEqual([l['static_name'] for l in facts['link_details']],['Home','Contact'])
        self.assertEqual([l['visible_text'] for l in facts['link_details']],['',''])
        self.assertEqual([l['name_basis'] for l in facts['link_details']],['contents','aria-label'])

    def test_nameless_image_link_requires_action_but_decorative_labelled_link_does_not(self):
        states,details,_=self.analyze('<a href="/a"><img src="a" alt=""></a><a href="/b" aria-label="Contact"><img src="b" alt="" role="none"></a>')
        self.assertEqual(states['image_link_name_missing'],'issue')
        self.assertEqual(details['image_link_name_missing']['count'],1)
        self.assertEqual(states['image_empty_alt_review'],'pass')

    def test_aria_labelledby_precedence_duplicates_and_missing_reference(self):
        _,_,facts=self.analyze('<a href="/a" aria-labelledby="one two" aria-label="Other">Visible</a><span id="one">First</span><span id="two">Second</span>')
        self.assertEqual(facts['link_details'][0]['static_name'],'First Second')
        self.assertEqual(facts['link_details'][0]['visible_text'],'Visible')
        self.assertEqual(facts['link_details'][0]['name_status'],'known')
        for html in ('<a href="/" aria-labelledby="missing">Text</a>',
                     '<span id="x">A</span><span id="x">B</span><a href="/" aria-labelledby="x">Text</a>'):
            states,_,facts=self.analyze(html)
            self.assertEqual(facts['link_details'][0]['name_status'],'unknown')
            self.assertEqual(states['link_name_missing'],'unknown')

    def test_title_fallback_hidden_content_and_svg_remain_limited(self):
        states,_,facts=self.analyze('<a href="/a" title="Contact"></a><a href="/b"><span hidden>Invisible</span></a><a href="/c"><svg><title>Icon</title></svg></a>')
        self.assertEqual(facts['link_details'][0]['static_name'],'Contact')
        self.assertEqual(facts['link_details'][1]['static_name'],'')
        self.assertEqual(facts['link_details'][2]['name_status'],'unknown')
        self.assertEqual(states['link_name_missing'],'issue')

    def test_normal_headings_pass_and_navigation_does_not_create_jump(self):
        states,_,_=self.analyze('<header><h4>Menu</h4></header><main><h1>Title</h1><h2>A</h2><h2>B</h2><h3>Detail</h3></main><footer><h6>Footer</h6></footer>')
        for rule in ('heading_first_not_h1','heading_h1_after_h2','heading_content_jump','heading_repeated_h2'):
            self.assertEqual(states[rule],'pass',rule)

    def test_heading_signals_are_distinct_and_have_source_locations(self):
        states,details,_=self.analyze('<main><h2> Intro </h2><h1>Title</h1><h3>Detail</h3><h2>intro</h2></main>')
        for rule in ('heading_first_not_h1','heading_h1_after_h2','heading_content_jump','heading_repeated_h2'):
            self.assertEqual(states[rule],'issue',rule)
        self.assertEqual(details['heading_content_jump']['elements'][0]['next']['element_path'],'/main[1]/h3[1]')
        self.assertEqual(details['heading_repeated_h2']['elements'][0]['count'],2)

    def test_social_missing_requires_complete_evidence(self):
        states,details,facts=self.analyze('<html><head></head><body></body></html>')
        self.assertEqual(states['social_og_missing'],'issue')
        self.assertEqual(details['social_og_missing']['missing'],['og:title','og:type','og:url','og:image'])
        states,_,_=self.analyze(facts=facts,coverage={'analysis_truncated':True})
        self.assertEqual(states['social_og_missing'],'unknown')
        self.assertEqual(self.analyze(facts={})[0]['social_og_missing'],'unknown')

    def test_valid_social_profile_and_x_fallback_without_network(self):
        states,details,_=self.analyze('<html><head><meta property="og:title" content="Title"><meta property="og:type" content="website"><meta property="og:url" content="https://example.com/"><meta property="og:image" content="https://example.com/image.png"><meta property="og:description" content="Description"><meta name="twitter:card" content="summary_large_image"></head></html>')
        for rule in ('social_og_missing','social_x_missing','social_values_invalid','social_values_conflicting','social_outside_head'):
            self.assertEqual(states[rule],'pass',rule)
        self.assertFalse(details['social_x_missing']['network_checked'])
        self.assertEqual(details['social_x_missing']['effective_values']['twitter:title'],'Title')

    def test_conflicts_invalid_values_and_multiple_images(self):
        states,details,_=self.analyze('<html><head><meta property="og:title" content="A"><meta property="og:title" content="B"><meta property="og:image" content="https://example.com/1"><meta property="og:image" content="https://example.com/2"><meta name="twitter:card" content="invalid"><meta property="og:url" content="/relative"></head></html>')
        self.assertEqual(states['social_values_conflicting'],'issue')
        self.assertEqual([e['name'] for e in details['social_values_conflicting']['elements']],['og:title'])
        self.assertEqual(states['social_values_invalid'],'issue')
        self.assertEqual(details['social_values_invalid']['count'],2)

    def test_template_and_hidden_headings_are_excluded(self):
        states,_,facts=self.analyze('<template><h4>Inactive</h4><meta property="og:title" content="Inactive"></template><h1>Title</h1><h4 hidden>Hidden</h4><h2>Section</h2>')
        self.assertEqual(states['heading_content_jump'],'pass')
        self.assertEqual(facts['social_metadata'],[])

    def test_bounds_remain_unknown_but_positive_signals_survive(self):
        states,_,facts=self.analyze('<h1>Title</h1>'+'<h2>Repeated</h2>'*220)
        self.assertFalse(facts['content_audit']['headings_complete'])
        self.assertEqual(states['heading_content_jump'],'unknown')
        self.assertEqual(states['heading_repeated_h2'],'issue')

    def test_new_content_rules_do_not_expand_score_v1(self):
        from reporting.catalog import EXPECTED
        expected=set().union(*EXPECTED.values())
        self.assertFalse(expected.intersection(r3.RULES))

    def test_complex_or_truncated_aria_references_do_not_invent_names(self):
        for html in ('<a href="/" aria-labelledby="x"></a><span id="x"><img src="a" alt="Name"></span>',
                     '<a href="/" aria-labelledby="x"></a><span id="x" aria-labelledby="y">Text</span><span id="y">Other</span>',
                     '<a href="/" aria-label="'+('A'*600)+'"></a>'):
            states,_,facts=self.analyze(html)
            self.assertEqual(facts['link_details'][0]['name_status'],'unknown')
            self.assertEqual(states['link_name_missing'],'unknown')

    def test_labelledby_target_aria_label_is_retained(self):
        _,_,facts=self.analyze('<a href="/" aria-labelledby="x">Visible</a><span id="x" aria-label="Label">Text</span>')
        self.assertEqual(facts['link_details'][0]['static_name'],'Label')

    def test_social_bad_url_never_raises_and_outside_head_is_reported(self):
        states,_,_=self.analyze('<html><head></head><body><meta property="og:url" content="https://[broken"><meta name="twitter:card" content="summary"></body></html>')
        self.assertEqual(states['social_values_invalid'],'issue')
        self.assertEqual(states['social_outside_head'],'issue')

    def test_image_aria_label_is_used_and_complex_image_reference_is_unknown(self):
        states,_,facts=self.analyze('<a href="/"><img src="a" alt="" aria-label="Home"></a>')
        self.assertEqual(facts['link_details'][0]['static_name'],'Home')
        self.assertEqual(states['image_link_name_missing'],'pass')
        states,_,facts=self.analyze('<a href="/"><img src="a" alt="" aria-labelledby="x"></a><span id="x">Name</span>')
        self.assertEqual(facts['link_details'][0]['name_status'],'unknown')
        self.assertEqual(states['image_link_name_missing'],'unknown')

    def test_generic_link_names_use_aria_and_main_content_context(self):
        states,details,_=self.analyze('<header><a href="/menu">Meer</a></header><main><a href="/a">Lees meer</a><a href="/b" aria-label="Meer over arbeidsrecht">Lees meer</a></main>')
        self.assertEqual(states['link_name_generic'],'issue')
        self.assertEqual(details['link_name_generic']['count'],1)
        self.assertEqual(details['link_name_generic']['elements'][0]['raw_url'],'/a')
