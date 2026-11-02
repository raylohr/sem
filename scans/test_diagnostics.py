import ssl
from unittest.mock import patch,MagicMock
from django.test import SimpleTestCase
import urllib3
from .network import SafeClient,NetworkRequestError
from .diagnostics import describe_exception
from .outcomes import summarize


class DiagnosticsTests(SimpleTestCase):
    def test_underlying_network_errors_are_distinguishable(self):
        cases=[(urllib3.exceptions.ConnectTimeoutError('secret'),'connect_or_headers','connect_timeout'),
               (urllib3.exceptions.ReadTimeoutError(None,'/private?token=secret','secret'),'read_body','read_timeout'),
               (ssl.SSLCertVerificationError(1,'secret'),'connect_or_headers','tls_error'),
               (ConnectionResetError(104,'secret'),'read_body','connection_reset'),
               (ConnectionRefusedError(111,'secret'),'connect_or_headers','connection_refused'),
               (ValueError('secret'),'record_document','unexpected_error')]
        for exc,stage,code in cases:
            with self.subTest(code=code):
                actual,details=describe_exception(exc,stage)
                self.assertEqual(actual,code)
                self.assertNotIn('secret',str(details))
        nested=urllib3.exceptions.ProtocolError('secret',ConnectionResetError(104,'secret'))
        code,details=describe_exception(nested,'read_body')
        self.assertEqual(code,'connection_reset')
        self.assertIn('ProtocolError',details['exception_chain'])
        self.assertIn('ConnectionResetError',details['exception_chain'])
        wrapped=urllib3.exceptions.NewConnectionError(None,'secret')
        wrapped.__cause__=ConnectionRefusedError(111,'secret')
        code,_=describe_exception(wrapped,'connect_or_headers')
        self.assertEqual(code,'connection_refused')
        code,_=describe_exception(urllib3.exceptions.NewConnectionError(None,'secret'),'connect_or_headers')
        self.assertEqual(code,'connection_error')

    @patch('scans.network.resolve_public',return_value='93.184.216.34')
    @patch('scans.network.urllib3.HTTPSConnectionPool')
    def test_read_failure_is_logged_and_saved_without_sensitive_url_or_message(self,pool,dns):
        response=pool.return_value.urlopen.return_value
        response.read.side_effect=urllib3.exceptions.ReadTimeoutError(None,'/private?token=secret','private-exception-secret')
        client=SafeClient(['example.com'],check_access=lambda:None,batch_id='batch-123')
        with self.assertLogs('scans.network',level='WARNING') as logs,self.assertRaises(NetworkRequestError) as raised:
            client._request('https://example.com/private?token=secret',0)
        self.assertEqual(raised.exception.code,'read_timeout')
        self.assertEqual(raised.exception.details['stage'],'read_body')
        self.assertIn('batch-123',logs.output[0])
        self.assertIn('ReadTimeoutError',logs.output[0])
        self.assertNotIn('secret',str(logs.output))
        self.assertNotIn('/private',str(logs.output))
        client.close()

    def test_404_and_robots_denial_do_not_mean_transport_failure(self):
        summary=summarize([{'data':{'status':404}}, {'data':{'error':'robots.txt','robots_blocked':True}}])
        self.assertFalse(summary['incomplete'])
        self.assertEqual(summary['http_errors'],1)
        self.assertEqual(summary['robots_blocked'],1)

    def test_blocked_server_and_legacy_network_errors_mark_incomplete(self):
        summary=summarize([{'data':{'status':200}}, {'data':{'status':403}}, {'data':{'status':429}},
            {'data':{'status':503}}, {'data':{'error':'HTTP-opvraag mislukt (netwerk, certificaat of antwoord).'}}])
        self.assertTrue(summary['incomplete'])
        self.assertEqual(summary['request_failures'],1)
        self.assertEqual(summary['errors_by_code']['legacy_http_error'],1)
        self.assertEqual(summary['blocked_responses'],2)
        self.assertEqual(summary['server_errors'],1)
