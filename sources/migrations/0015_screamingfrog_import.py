from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sources', '0014_remove_file_import_fields')]
    operations = [
        migrations.AddField(model_name='importbatch', name='file_checksum',
                            field=models.CharField(max_length=64, null=True, blank=True, editable=False)),
        migrations.AlterField(model_name='importbatch', name='source',
                              field=models.CharField(max_length=24, choices=[('gsc', 'Google Search Console'), ('sf', 'Screaming Frog')])),
        migrations.AlterField(model_name='importbatch', name='method',
                              field=models.CharField(max_length=10, choices=[('api', 'API'), ('file', 'Bestand')])),
        migrations.AddConstraint(model_name='importbatch', constraint=models.UniqueConstraint(
            fields=['project', 'file_checksum'], name='unique_project_file_import')),
    ]
