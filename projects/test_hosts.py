import socket
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .hosts import allows_url, normalize_domain, check_dns
from .models import Project


class HostPolicyTests(SimpleTestCase):
    def test_exact_hosts_subdomain_boundary_and_idna(self):
        project = Project(website='https://example.co.uk', domain='example.co.uk')
        for url in ['https://example.co.uk/a', 'https://www.example.co.uk/a', 'https://EXAMPLE.CO.UK./a']:
            self.assertTrue(allows_url(project, url), url)
        for url in ['https://blog.example.co.uk', 'https://example.co.uk.evil.test', 'https://notexample.co.uk', 'https://example.co.uk@evil.test', 'javascript:alert(1)']:
            self.assertFalse(allows_url(project, url), url)
        project.include_subdomains = True
        self.assertTrue(allows_url(project, 'https://a.blog.example.co.uk'))
        self.assertFalse(allows_url(project, 'https://example.co.uk.evil.test'))
        self.assertEqual(normalize_domain('BÜCHER.de.'), 'xn--bcher-kva.de')

    @patch('projects.hosts.socket.getaddrinfo')
    def test_dns_failure_blocks_project_validation(self, lookup):
        lookup.side_effect = socket.gaierror()
        project = Project(name='Missing', website='https://missing.example', domain='missing.example')
        with self.assertRaises(ValidationError) as error:
            project.clean()
        self.assertIn('domain', error.exception.message_dict)
        lookup.assert_called_once_with('missing.example.', None, type=socket.SOCK_STREAM)

    @patch('projects.hosts.socket.getaddrinfo', return_value=[('address',)])
    def test_valid_dns_and_website_boundary(self, lookup):
        project = Project(name='Valid', website='https://www.example.com')
        project.clean()
        self.assertEqual(project.domain, 'example.com')
        project.website = 'https://other.test'
        with self.assertRaises(ValidationError):
            project.clean()
        self.assertEqual(lookup.call_count, 1)
