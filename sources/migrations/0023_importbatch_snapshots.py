import hashlib

import django.db.models.deletion
from django.db import migrations, models


def dataset_key(batch):
    # Frozen copy of sources.snapshots.dataset_key at the time of this migration.
    metadata = batch.request_metadata or {}
    report = metadata.get('type') or metadata.get('kind') or (
        'search_analytics' if batch.source == 'gsc' else 'internal_all' if batch.source == 'sf' else 'records')
    key = f'{batch.source}:{report}'
    if report == 'gsc_page_indexing':
        segment = f"{metadata.get('report_status', '')}|{metadata.get('indexing_reason', '')}"
        key += ':' + hashlib.sha256(segment.encode()).hexdigest()[:16]
    return key[:255]


def fill_dataset_keys(apps, schema_editor):
    # Keys only; statuses stay NULL (= counted as before) until
    # `manage.py classify_snapshots --apply` has been reviewed and run.
    ImportBatch = apps.get_model('sources', 'ImportBatch')
    for batch in ImportBatch.objects.only('id', 'source', 'request_metadata').iterator(chunk_size=500):
        ImportBatch.objects.filter(pk=batch.pk).update(dataset_key=dataset_key(batch))


class Migration(migrations.Migration):
    dependencies = [('sources', '0022_importbatch_seranking_connection_and_more')]

    operations = [
        migrations.AddField(model_name='importbatch', name='dataset_key',
                            field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='importbatch', name='snapshot_status',
                            field=models.CharField(blank=True, null=True, max_length=12, choices=[
                                ('active', 'Actief'), ('supplement', 'Aanvullend'), ('superseded', 'Vervangen'),
                                ('redundant', 'Redundant'), ('historical', 'Historisch'), ('conflict', 'Conflict'),
                                ('independent', 'Zelfstandig')])),
        migrations.AddField(model_name='importbatch', name='snapshot_reason', field=models.TextField(blank=True)),
        migrations.AddField(model_name='importbatch', name='quality_warnings', field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='importbatch', name='superseded_by', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='sources.importbatch')),
        migrations.RunPython(fill_dataset_keys, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='importbatch', constraint=models.UniqueConstraint(
            condition=models.Q(snapshot_status='active'), fields=('project', 'dataset_key'), name='one_active_snapshot_per_dataset')),
    ]
