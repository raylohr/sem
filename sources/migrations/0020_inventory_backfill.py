from django.db import migrations


def backfill(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    State = apps.get_model('sources', 'InventoryState')
    State.objects.using(schema_editor.connection.alias).bulk_create(
        (State(project_id=pk) for pk in Project.objects.using(schema_editor.connection.alias).values_list('pk', flat=True).iterator()),
        batch_size=500, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('sources', '0019_inventorystate_inventoryurl_inventoryvalue_and_more')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
