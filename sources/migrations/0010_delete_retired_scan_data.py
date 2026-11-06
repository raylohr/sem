from django.db import migrations
from django.db.models import Q


def purge(apps, schema_editor):
    alias = schema_editor.connection.alias
    Batch = apps.get_model('sources', 'ImportBatch')
    Pending = apps.get_model('sources', 'PendingFileDeletion')
    URL = apps.get_model('catalog', 'ProjectURL')
    Assignment = apps.get_model('catalog', 'URLLabel')
    scans = Batch.objects.using(alias).filter(
        Q(source__in=['semscan', 'crawler', 'sitemap']) | Q(method='scan'))
    affected = list(Assignment.objects.using(alias).filter(evidence__source_record__batch__in=scans).values_list('pk', flat=True))
    for batch in scans.iterator(chunk_size=100):
        if batch.file:
            Pending.objects.using(alias).get_or_create(path=batch.file.name)
    # Include abandoned checkpoints whose batch may already have been deleted.
    from django.core.files.storage import default_storage
    for prefix in ('scan-work', 'scan-activity'):
        try:
            _, filenames = default_storage.listdir(prefix)
        except FileNotFoundError:
            continue
        for filename in filenames:
            Pending.objects.using(alias).get_or_create(path=prefix + '/' + filename)
    scans.delete()
    Assignment.objects.using(alias).filter(pk__in=affected, status='source', evidence__isnull=True).delete()
    URL.objects.using(alias).filter(observations__isnull=True, label_assignments__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('scans', '0008_remove_crawlurlcoverage_batch_and_more'), ('browser_scans', '0004_remove_browserartifact_visit_and_more'), ('assessments', '0006_remove_analysisrun_definition_and_more'), ('site_intelligence', '0003_remove_detectionevidence_artifact_and_more'), ('reporting', '0007_remove_actionissue_action_remove_actionissue_group_and_more'), ('catalog', '0008_gmw_english_expertise_paths'), ('sources', '0009_remove_importbatch_scan_target_and_more')]
    operations = [migrations.RunPython(purge)]
