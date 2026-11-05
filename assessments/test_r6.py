import json
from types import SimpleNamespace
from django.test import SimpleTestCase
from scans.extraction import extract_page
from .r6 import local


def extract(html,path='/article'):
    return extract_page(SimpleNamespace(url='https://example.com'+path,status=200,headers={'content-type':'text/html'},body=html.encode()))[0]


class ResearchTests(SimpleTestCase):
    def checks(self,facts,profiles=None):
        result={}
        local(SimpleNamespace(page=SimpleNamespace(facts=facts,coverage={}),schema_profiles=profiles if profiles is not None else ['google-breadcrumb-v1','google-product-snippet-v1'],check=lambda rule,state,details=None:result.update({rule:state})))
        return result
    def test_main_separated_from_navigation_and_functional_pages_not_thin(self):
        facts=extract('<html lang="nl"><head><title>Title</title></head><body><header><nav>'+('Menu '*200)+'</nav></header><main><h1>Contact</h1><p>Bel ons voor een afspraak.</p></main><footer>Footer</footer></body></html>','/contact')
        self.assertLess(facts['main_content']['word_count'],20)
        self.assertGreater(facts['word_count'],200)
        self.assertNotIn('Menu',facts['main_content']['text_excerpt'])
        self.assertEqual(self.checks(facts)['main_content_thin'],'not_applicable')
    def test_legal_dutch_and_unknown_language_do_not_receive_fake_readability_score(self):
        text=('De rechtbank beoordeelt de arbeidsovereenkomst en de jurisprudentie voor het doel van deze afspraak. '*12)
        facts=extract('<html lang="nl"><main><p>'+text+'</p></main></html>')
        self.assertEqual(facts['main_content']['readability']['status'],'expert_review')
        self.assertIsNone(facts['main_content']['readability']['score'])
        self.assertEqual(self.checks(facts)['content_readability_review'],'unknown')
        other=extract('<html lang="ru"><main><p>'+text+'</p></main></html>')
        self.assertEqual(other['main_content']['readability']['status'],'unknown')
    def test_plain_dutch_long_sentences_advice_and_thin_article(self):
        sentence=('De bezoeker en de klant kunnen voor deze dienst op de website een duidelijke afspraak maken met de mensen die voor het team en de organisatie werken ')*2+'. '
        facts=extract('<html lang="nl"><main><p>'+sentence*4+'</p></main></html>')
        self.assertEqual(self.checks(facts)['content_readability_review'],'issue')
        thin=extract('<main><article><p>Korte toelichting.</p></article></main>')
        self.assertEqual(self.checks(thin)['main_content_thin'],'issue')
    def test_syntax_schema_and_rich_profile_are_separate(self):
        bad=extract('<script type="application/ld+json">{broken}</script>')
        self.assertEqual(self.checks(bad)['structured_syntax_invalid'],'issue')
        data={'@context':'https://schema.org','@type':'Product','name':'Example','inventedProperty':1}
        facts=extract('<script type="application/ld+json">'+json.dumps(data)+'</script>')
        states=self.checks(facts)
        self.assertEqual(states['structured_syntax_invalid'],'pass')
        self.assertEqual(states['structured_vocabulary_review'],'issue')
        self.assertEqual(states['structured_rich_profile'],'issue')
    def test_valid_product_price_zero_and_optional_currency(self):
        data={'@context':'https://schema.org','@type':'Product','name':'Example','offers':{'@type':'Offer','price':0}}
        facts=extract('<script type="application/ld+json">'+json.dumps(data)+'</script>')
        self.assertEqual(self.checks(facts)['structured_vocabulary_review'],'pass',facts['structured_data'])
        self.assertEqual(self.checks(facts)['structured_rich_profile'],'pass')
    def test_microdata_and_rdfa_nested_breadcrumbs(self):
        micro='<ol itemscope itemtype="https://schema.org/BreadcrumbList"><li itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem"><a itemprop="item" href="/"><span itemprop="name">Home</span></a><meta itemprop="position" content="1"></li><li itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem"><span itemprop="name">Current</span><meta itemprop="position" content="2"></li></ol>'
        rdfa='<ol vocab="https://schema.org/" typeof="BreadcrumbList"><li property="itemListElement" typeof="ListItem"><a property="item" typeof="WebPage" href="/"><span property="name">Home</span></a><meta property="position" content="1"></li><li property="itemListElement" typeof="ListItem"><span property="name">Current</span><meta property="position" content="2"></li></ol>'
        for html,fmt in ((micro,'microdata'),(rdfa,'rdfa')):
            facts=extract(html)
            self.assertIn(fmt,facts['structured_data']['formats'])
            self.assertEqual(self.checks(facts)['structured_vocabulary_review'],'pass',facts['structured_data'])
            self.assertEqual(self.checks(facts)['structured_rich_profile'],'pass',facts['structured_data'])
    def test_unsupported_context_and_itemref_are_unknown_without_network(self):
        facts=extract('<script type="application/ld+json">{"@context":"http://127.0.0.1/context","@type":"Product"}</script>')
        self.assertEqual(self.checks(facts)['structured_vocabulary_review'],'unknown')
        facts=extract('<div itemscope itemtype="https://schema.org/Product" itemref="other"><span itemprop="name">Example</span></div>')
        self.assertEqual(self.checks(facts)['structured_vocabulary_review'],'unknown')
    def test_disabled_profile_and_empty_inventory_not_pass(self):
        facts=extract('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Example"}</script>')
        self.assertEqual(self.checks(facts,[])['structured_rich_profile'],'not_applicable')
        self.assertEqual(self.checks(extract('<main>Text</main>'))['structured_vocabulary_review'],'not_applicable')
    def test_unclosed_truncated_and_fallback_main_are_unknown(self):
        facts=extract('<script type="application/ld+json">'+('x'*66000))
        self.assertEqual(self.checks(facts)['structured_syntax_invalid'],'unknown')
        self.assertEqual(self.checks(extract('<p>Some text</p>'))['main_content_thin'],'unknown')

    def test_nonfinite_and_fractional_integer_values_are_not_valid_numbers(self):
        from scans.structured_data import literal_matches
        for value in ('NaN','Infinity','-Infinity'):
            self.assertFalse(literal_matches(value,['Number'],'https://example.com/'))
        self.assertFalse(literal_matches('1.5',['Integer'],'https://example.com/'))
        self.assertTrue(literal_matches('2',['Integer'],'https://example.com/'))
        facts=extract('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":NaN}</script>')
        self.assertEqual(self.checks(facts)['structured_syntax_invalid'],'issue')
