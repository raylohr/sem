from django.db import migrations, models


def forwards(apps, schema_editor):
    Batch = apps.get_model('sources', 'ImportBatch')
    for batch in Batch.objects.filter(source__in=['crawler', 'sitemap']).iterator(chunk_size=100):
        metadata = dict(batch.request_metadata)
        metadata.setdefault('source_original', batch.source)
        metadata.setdefault('scan', 'crawl' if batch.source == 'crawler' else 'sitemap')
        Batch.objects.filter(pk=batch.pk).update(source='semscan', request_metadata=metadata)


def backwards(apps, schema_editor):
    Batch = apps.get_model('sources', 'ImportBatch')
    for batch in Batch.objects.filter(source='semscan').iterator(chunk_size=100):
        source = batch.request_metadata.get('source_original') or ('crawler' if batch.request_metadata.get('scan') == 'crawl' else 'sitemap')
        Batch.objects.filter(pk=batch.pk).update(source=source)


class Migration(migrations.Migration):
    dependencies = [('sources', '0006_batchdeletionevent_project_identifier_and_more')]
    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(model_name='importbatch', name='source', field=models.CharField(max_length=24, choices=[
            ('gsc','Google Search Console'), ('ahrefs','Ahrefs'), ('screaming_frog','Screaming Frog'),
            ('audit','Contentaudit'), ('semscan','SEMScan')]))]
