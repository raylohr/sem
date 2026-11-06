from urllib.parse import urlsplit
from django.db import migrations, models


def populate(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    for project in Project.objects.all().iterator():
        project.domain = (urlsplit(project.website).hostname or '').removeprefix('www.').rstrip('.').lower().encode('idna').decode('ascii')
        project.save(update_fields=['domain'])


class Migration(migrations.Migration):
    dependencies = [('projects', '0001_initial')]
    operations = [
        migrations.AddField(model_name='project', name='domain', field=models.CharField(max_length=253, blank=True)),
        migrations.AddField(model_name='project', name='include_subdomains', field=models.BooleanField(default=False)),
        migrations.RunPython(populate, migrations.RunPython.noop),
    ]
