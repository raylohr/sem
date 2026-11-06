from django.test import SimpleTestCase
from .content_types import content_badge


class ContentBadgeTests(SimpleTestCase):
    def test_html_other_types_and_errors(self):
        cases = [
            ({'Content Type':'text/html; charset=UTF-8','Status Code':'200'}, 'HTML','html'),
            ({'Content Type':'application/xhtml+xml','Status Code':'200'},'HTML','html'),
            ({'Content Type':'image/jpeg','Status Code':'200'},'Afbeelding','other'),
            ({'Content Type':'text/css','Status Code':'200'},'CSS','other'),
            ({'Content Type':'application/javascript','Status Code':'200'},'JavaScript','other'),
            ({'Content Type':'application/pdf','Status Code':'200'},'PDF','other'),
            ({'Content Type':'text/html','Status Code':'404'},'HTTP 404','error'),
            ({'Content Type':'text/html','Status Code':'429'},'HTTP 429','error'),
            ({'Status Code':'0.0'},'Geen response','error'),
            ({'Content Type':'text/html','Status Code':'301'},'Omleiding 301','other'),
            ({},'Onbekend','other'),
        ]
        for data,label,tone in cases:
            with self.subTest(data=data):
                badge=content_badge(data)
                self.assertEqual((badge['label'],badge['tone']),(label,tone))
