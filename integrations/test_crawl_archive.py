import base64
import gzip
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Project, Membership
from sources.models import CrawlPageArtifact, ImportBatch
from sources.services import delete_batch
from sources.url_inventory import project_inventory
from .crawl_content import pack_content, unpack_content, decompress_document
from .crawl_renderer import PageCapture
from .crawl_service import start_crawl, add_urls
from .crawl_tasks import process_crawl_url
from .models import PlaywrightCrawlSettings


class DocumentTransportTests(SimpleTestCase):
    def test_exact_bytes_unicode_and_checksums_roundtrip(self):
        capture=PageCapture(url='https://example.test/',server_html=b'<html>\xff</html>',
            rendered_html='<html>Gerenderd café</html>',text='café')
        packed=pack_content(capture)
        result=unpack_content(packed,capture.metadata())
        self.assertEqual(decompress_document(result['server_html']),capture.server_html)
        self.assertEqual(decompress_document(result['text']).decode(),'café')
        packed['text']['sha256']='0'*64
        with self.assertRaises(ValueError):
            unpack_content(packed,capture.metadata())

    def test_compression_bombs_truncation_and_trailing_streams_are_rejected(self):
        from .crawl_content import MAX_DOCUMENT
        for value in (gzip.compress(b'a'*(MAX_DOCUMENT+1)),gzip.compress(b'a')[:-1],
                      gzip.compress(b'a')+gzip.compress(b'b')):
            with self.assertRaises(ValueError):
                decompress_document(value)


class CrawlArchiveTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('archive-owner')
        self.reader=get_user_model().objects.create_user('archive-reader')
        self.outsider=get_user_model().objects.create_user('archive-outsider')
        self.project=Project.objects.create(name='Archive',website='https://example.test/',created_by=self.owner,include_subdomains=True)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.reader,role='viewer')
        PlaywrightCrawlSettings.objects.create(project=self.project,use_sitemaps=False,discover_urls=False,max_retries=0)
        with patch('integrations.crawl_tasks.enqueue_bootstrap'):
            self.crawl=start_crawl(actor=self.owner,project_id=self.project.pk)
        self.crawl.status='running'
        self.crawl.started_at=timezone.now()
        self.crawl.save()
        add_urls(self.crawl.pk,['https://example.test/'])
        self.row=self.crawl.urls.get()
        self.row.status='queued'
        self.row.task_token=uuid4()
        self.row.save()
        self.capture=PageCapture(url=self.row.url,final_url=self.row.url,http_status=200,
            server_html=b'<html><script>test()</script></html>',
            rendered_html='<html><body>Gerenderd café</body></html>',text='Gerenderd café',
            title='Een pagina',render_complete=True)

    def run_page(self,result=None):
        if result is None:
            result={**self.capture.metadata(),'artifacts':pack_content(self.capture)}
        with patch('integrations.crawl_tasks.call_renderer',return_value=result):
            process_crawl_url(self.row.pk,str(self.row.task_token))
        self.crawl.refresh_from_db()
        self.crawl.batch.refresh_from_db()
        self.row.refresh_from_db()

    def download_url(self,record,variant='rendered_html',project=None):
        return reverse('projects:sources:crawl_document',args=[(project or self.project).pk,record.batch_id,record.pk,variant])

    def test_publication_exact_archive_download_and_duplicate_delivery(self):
        self.assertEqual(project_inventory(self.project)[1],[])
        self.run_page()
        batch=self.crawl.batch
        self.assertEqual((batch.status,batch.record_count),('complete',1))
        record=batch.records.get()
        self.assertEqual(record.data['url'],self.row.url)
        self.assertNotIn('artifacts',self.row.metadata)
        self.assertNotIn('rendered_html',record.data)
        self.assertEqual(decompress_document(record.crawl_artifact.rendered_html).decode(),self.capture.rendered_html)
        self.assertEqual(len(project_inventory(self.project)[1]),1)
        self.run_page()
        self.assertEqual(CrawlPageArtifact.objects.count(),1)
        self.client.force_login(self.reader)
        response=self.client.get(self.download_url(record))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,self.capture.rendered_html.encode())
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['Content-Type'],'application/octet-stream')
        self.assertIn('no-store',response['Cache-Control'])
        detail=self.client.get(reverse('ui:url-detail',args=[self.project.pk]),{'url':self.row.url})
        self.assertContains(detail,'Gerenderde HTML downloaden')
        delete_batch(batch_id=batch.pk,project=self.project,actor=self.owner)
        self.assertFalse(CrawlPageArtifact.objects.exists())
        self.assertEqual(self.client.get(self.download_url(record)).status_code,404)

    def test_download_checks_membership_project_publication_scope_and_variant(self):
        self.run_page()
        record=self.crawl.batch.records.get()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.download_url(record)).status_code,404)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.download_url(record,'invalid')).status_code,404)
        other=Project.objects.create(name='Other',website='https://other.test/',created_by=self.reader)
        Membership.objects.create(project=other,user=self.reader,role='viewer')
        self.assertEqual(self.client.get(self.download_url(record,project=other)).status_code,404)
        ImportBatch.objects.filter(pk=record.batch_id).update(status='processing')
        self.assertEqual(self.client.get(self.download_url(record)).status_code,404)
        ImportBatch.objects.filter(pk=record.batch_id).update(status='complete')
        Project.objects.filter(pk=self.project.pk).update(domain='other.test')
        self.assertEqual(self.client.get(self.download_url(record)).status_code,404)

    def test_partial_render_is_published_as_incomplete_with_artifact(self):
        self.capture.error='timeout'
        self.capture.render_complete=False
        self.run_page()
        self.assertEqual(self.crawl.batch.status,'incomplete')
        self.assertEqual(self.crawl.batch.request_metadata['coverage']['page']['failed'],1)
        self.assertEqual(CrawlPageArtifact.objects.count(),1)

    def test_corrupt_content_never_becomes_success_or_downloadable(self):
        packed=pack_content(self.capture)
        packed['rendered_html']['sha256']='bad'
        self.run_page({**self.capture.metadata(),'artifacts':packed})
        self.assertEqual(self.crawl.batch.status,'incomplete')
        self.assertEqual(self.row.status,'failed')
        self.assertFalse(CrawlPageArtifact.objects.exists())

    def test_stale_attempt_cannot_write_documents(self):
        def replaced(*args):
            self.crawl.urls.filter(pk=self.row.pk).update(task_token=uuid4())
            return {**self.capture.metadata(),'artifacts':pack_content(self.capture)}
        with patch('integrations.crawl_tasks.call_renderer',side_effect=replaced):
            process_crawl_url(self.row.pk,str(self.row.task_token))
        self.assertFalse(CrawlPageArtifact.objects.exists())
        self.assertFalse(self.crawl.batch.records.exists())

    def test_storage_failure_rolls_back_observation_and_documents(self):
        with patch('integrations.crawl_archive.CrawlPageArtifact.objects.update_or_create',side_effect=RuntimeError):
            self.run_page()
        self.assertFalse(CrawlPageArtifact.objects.exists())
        self.assertFalse(self.crawl.batch.records.exists())
        self.assertEqual(self.crawl.batch.status,'incomplete')

    def test_revoked_actor_cannot_archive_or_publish(self):
        def revoked(*args):
            Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
            return {**self.capture.metadata(),'artifacts':pack_content(self.capture)}
        with patch('integrations.crawl_tasks.call_renderer',side_effect=revoked):
            process_crawl_url(self.row.pk,str(self.row.task_token))
        self.crawl.batch.refresh_from_db()
        self.assertEqual(self.crawl.batch.status,'cancelled')
        self.assertFalse(CrawlPageArtifact.objects.exists())
        self.assertFalse(self.crawl.batch.records.exists())
