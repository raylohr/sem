from django.db import migrations


PATHS = {
    'Aansprakelijkheid': '/en/expertises/law-of-liability/',
    'Arbeid': '/en/expertises/employment/',
    'Bouw': '/en/expertises/real-estate/real-estate-law/construction/',
    'Geschillen tussen bedrijven': '/en/expertises/disputes-between-companies/',
    'Erfenis': '/en/expertises/inheritance-law/',
    'Familie & Echtscheiding': '/en/expertises/family-and-divorce/',
    'Bedrijfsfinanciering & zekerheden': '/en/expertises/financing-and-securities/',
    'Huur': '/en/expertises/tenancy-law/',
    'Bedrijven in financiële moeilijkheden': '/en/expertises/companies-financial-distress/',
    'Letselschade': '/en/expertises/personal-injury-law/',
    'Mediation': '/en/mediator-the-hague/',
    'Geschillen en advies binnen de onderneming': '/en/expertises/corporate-disputes-and-advisory/',
    'Pensioen': '/en/expertises/pension-law/',
    'Vastgoed': '/en/expertises/real-estate/',
}


def populate_paths(apps, schema_editor):
    Label = apps.get_model('catalog', 'Label')
    for name, path in PATHS.items():
        Label.objects.using(schema_editor.connection.alias).filter(
            project_id='ae8a41a9-91eb-4825-9019-451935c1105c', label_type='expertise',
            name=name, url_path_en='',
        ).update(url_path_en=path)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0007_label_url_path_en')]
    operations = [migrations.RunPython(populate_paths, migrations.RunPython.noop)]
