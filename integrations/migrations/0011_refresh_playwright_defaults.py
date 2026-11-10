from datetime import time
from django.db import migrations


def refresh_defaults(apps,schema_editor):
    settings=apps.get_model('integrations','PlaywrightCrawlSettings').objects.using(schema_editor.connection.alias)
    settings.filter(max_pages=10000).update(max_pages=100000)
    settings.filter(max_depth=10).update(max_depth=99)
    settings.filter(max_duration_minutes=720).update(max_duration_minutes=1400)
    settings.filter(max_duration_minutes__gt=1400).update(max_duration_minutes=1400)
    # Existing enabled schedules retain their chosen cadence and wall-clock time.
    settings.filter(schedule_enabled=False,schedule_frequency='daily').update(schedule_frequency='weekly')
    settings.filter(schedule_enabled=False,schedule_time=time(3,0)).update(schedule_time=time(20,0))


class Migration(migrations.Migration):
    dependencies=[('integrations','0010_alter_playwrightcrawlsettings_max_depth_and_more')]
    operations=[migrations.RunPython(refresh_defaults,migrations.RunPython.noop)]
