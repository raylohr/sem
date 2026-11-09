from django.db import migrations


class Migration(migrations.Migration):
    """Undo 0023: snapshot classification is withdrawn."""
    dependencies = [('sources', '0023_importbatch_snapshots')]

    operations = [
        migrations.RemoveConstraint(model_name='importbatch', name='one_active_snapshot_per_dataset'),
        migrations.RemoveField(model_name='importbatch', name='superseded_by'),
        migrations.RemoveField(model_name='importbatch', name='quality_warnings'),
        migrations.RemoveField(model_name='importbatch', name='snapshot_reason'),
        migrations.RemoveField(model_name='importbatch', name='snapshot_status'),
        migrations.RemoveField(model_name='importbatch', name='dataset_key'),
    ]
