from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path


def forbidden(request):
    raise PermissionDenied('PRIVATE_PERMISSION_REASON')


def missing(request):
    raise Http404('PRIVATE_RESOURCE_NAME')


def submit(request):
    return HttpResponse('Accepted')


# Preserve named app routes used by the layout and account policy middleware.
from config.urls import urlpatterns as app_patterns
urlpatterns = [path('error-check/forbidden/', forbidden),
               path('error-check/missing/', missing),
               path('error-check/submit/', submit), *app_patterns]


@override_settings(ROOT_URLCONF=__name__, DEBUG=False)
class ErrorPageTests(SimpleTestCase):
    def test_forbidden_page_uses_branding_without_internal_reason(self):
        response = self.client.get('/error-check/forbidden/')
        self.assertContains(response, 'Geen toegang', status_code=403)
        self.assertTemplateUsed(response, '403.html')
        self.assertContains(response, 'Search Visibility Toolkit', status_code=403)
        self.assertNotContains(response, 'PRIVATE_PERMISSION_REASON', status_code=403)

    def test_missing_resource_and_unknown_route_use_standard_404(self):
        for url in ('/error-check/missing/', '/unknown-private-resource/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'Pagina niet gevonden', status_code=404)
                self.assertTemplateUsed(response, '404.html')
                self.assertContains(response, 'Naar mijn werkplek', status_code=404)
                self.assertNotContains(response, 'PRIVATE_RESOURCE_NAME', status_code=404)
                self.assertNotContains(response, 'unknown-private-resource', status_code=404)

    def test_csrf_rejection_uses_branded_403_and_remains_blocked(self):
        from django.test import Client
        response = Client(enforce_csrf_checks=True).post('/error-check/submit/', {'private': 'SECRET_FORM_VALUE'})
        self.assertContains(response, 'Formulier opnieuw openen', status_code=403)
        self.assertTemplateUsed(response, '403.html')
        self.assertNotContains(response, 'SECRET_FORM_VALUE', status_code=403)
        self.assertNotContains(response, 'CSRF cookie not set', status_code=403)
        self.assertIn('no-store', response['Cache-Control'])
