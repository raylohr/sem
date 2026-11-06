from django.db import migrations


def remove_file_imports(apps, schema_editor):
    from django.core.files.storage import default_storage
    from posixpath import join
    alias = schema_editor.connection.alias
    Batch = apps.get_model('sources', 'ImportBatch')
    Pending = apps.get_model('sources', 'PendingFileDeletion')
    for path in Pending.objects.using(alias).values_list('path', flat=True).iterator():
        default_storage.delete(path)
    for path in Batch.objects.using(alias).exclude(file='').values_list('file', flat=True).iterator():
        default_storage.delete(path)

    def remove_upload_files(directory):
        try:
            directories, filenames = default_storage.listdir(directory)
        except FileNotFoundError:
            return
        for filename in filenames:
            default_storage.delete(join(directory, filename))
        for child in directories:
            remove_upload_files(join(directory, child))

    # Include orphaned uploads left by interrupted or previously deleted imports.
    remove_upload_files('imports')
    Batch.objects.using(alias).exclude(source='gsc', method='api').delete()



class Migration(migrations.Migration):
    dependencies = [('sources', '0012_alter_importbatch_source')]
    operations = [migrations.RunPython(remove_file_imports)]
