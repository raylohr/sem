from django.db import migrations


def seed(apps,schema_editor):
    from site_intelligence.catalog import install_catalog
    install_catalog(apps)


class Migration(migrations.Migration):
    dependencies=[('site_intelligence','0001_initial')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
