"""Full queue fixture using only the disposable database and test network."""
import os
import time


def main():
    os.environ['DJANGO_SETTINGS_MODULE']='config.test_settings'
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from config.celery import app
    from projects.models import Project,Membership
    from integrations.models import PlaywrightCrawlSettings,PlaywrightCrawl,PlaywrightCrawlURL
    from integrations.crawl_service import start_crawl
    from sources.models import ImportBatch
    assert settings.DATABASES['default']['NAME']=='sem_checks'
    assert settings.DATABASES['default']['HOST']=='database'
    call_command('migrate',verbosity=0,interactive=False)
    app.conf.broker_url=os.environ['CELERY_BROKER_URL']
    user=get_user_model().objects.create_user('crawl-smoke')
    project=Project.objects.create(name='Fixture',website='http://fixture.invalid/',created_by=user,include_subdomains=True)
    Membership.objects.create(project=project,user=user,role='admin')
    PlaywrightCrawlSettings.objects.create(project=project,desktop_profile='chrome_windows',
        parallel_agents=2,pages_per_second=2,max_pages=1,max_depth=2,max_duration_minutes=2,
        render_timeout_seconds=15,settle_seconds=1)
    from django.test import Client
    from django.urls import reverse
    client=Client()
    client.force_login(user)
    action=reverse('projects:connections:playwright-action',args=[project.pk])
    assert client.post(action,{'action':'start'}).status_code==302
    crawl=PlaywrightCrawl.objects.get(project=project)
    assert client.post(action,{'action':'pause','crawl_id':crawl.pk}).status_code==302
    crawl.refresh_from_db()
    assert crawl.status=='paused'
    crawl.batch.refresh_from_db()
    assert crawl.batch.status=='processing'
    time.sleep(2)
    from datetime import timedelta
    from django.utils import timezone
    PlaywrightCrawl.objects.filter(pk=crawl.pk).update(started_at=timezone.now()-timedelta(minutes=3))
    assert client.post(action,{'action':'resume','crawl_id':crawl.pk}).status_code==302
    print('Real Celery crawl queued:',crawl.pk,flush=True)
    deadline=time.monotonic()+110
    while time.monotonic()<deadline:
        crawl.refresh_from_db()
        if crawl.status in ('complete','incomplete','failed','cancelled'):
            break
        time.sleep(1)
    rows=list(crawl.urls.values('url','kind','status','error','metadata'))
    assert crawl.status=='complete',(crawl.status,crawl.error,rows)
    pages=[row for row in rows if row['kind']=='page']
    external=[row for row in rows if row['kind']=='external']
    assert len(pages)==4,rows
    assert all(row['status']=='complete' and row['metadata'].get('rendered_html_bytes',0)>0 for row in pages),rows
    assert len(external)==1 and external[0]['metadata']['reachable'] is True,rows
    assert all(row['status']=='complete' for row in rows),rows
    from sources.models import CrawlPageArtifact
    from integrations.crawl_content import decompress_document
    batch=ImportBatch.objects.get(project=project,source='playwright')
    assert batch.status=='complete' and batch.record_count==4
    assert CrawlPageArtifact.objects.filter(record__batch=batch).count()==4
    record=batch.records.get(data__url='http://fixture.invalid/')
    artifact=record.crawl_artifact
    assert b'/from-js' in decompress_document(artifact.rendered_html)
    assert b'<script>' in decompress_document(artifact.server_html)
    assert b'JS' in decompress_document(artifact.text)
    assert 'artifacts' not in record.data
    from sources.url_inventory import project_inventory
    assert len(project_inventory(project)[1])==4
    assert PlaywrightCrawl.objects.count()==1
    print('UI start/pause/resume → Celery → browser → HTML archive + inventory: OK',flush=True)


if __name__=='__main__':
    main()
