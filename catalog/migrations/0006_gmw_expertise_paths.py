from django.db import migrations


PROJECT_ID = 'ae8a41a9-91eb-4825-9019-451935c1105c'
PATHS = {
    'Aansprakelijkheid': '/expertises/aansprakelijkheid/',
    'Arbeid': '/expertises/arbeid/',
    'Bouw': '/expertises/vastgoed/vastgoedrecht/bouw/',
    'Cassatie': '/expertises/cassatie/',
    'Geschillen tussen bedrijven': '/expertises/geschillen-tussen-bedrijven/',
    'Erfenis': '/expertises/erfrecht/',
    'Familie & Echtscheiding': '/expertises/familie-echtscheiding/',
    'Bedrijfsfinanciering & zekerheden': '/expertises/bedrijfsfinanciering-en-zekerheden/',
    'Huur': '/expertises/huurrecht/',
    'Bedrijven in financiële moeilijkheden': '/expertises/bedrijven-financiele-moeilijkheden/',
    'Letselschade': '/expertises/letselschade/',
    'Geschillen en advies binnen de onderneming': '/expertises/geschillen-advies-onderneming/',
    'Pensioen': '/expertises/pensioenrecht/',
    'Vastgoed': '/expertises/vastgoed/',
}


def populate_paths(apps, schema_editor):
    Label = apps.get_model('catalog', 'Label')
    for name, path in PATHS.items():
        Label.objects.using(schema_editor.connection.alias).filter(
            project_id=PROJECT_ID, label_type='expertise', name=name, url_path='',
        ).update(url_path=path)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0005_label_url_path')]
    operations = [migrations.RunPython(populate_paths, migrations.RunPython.noop)]
