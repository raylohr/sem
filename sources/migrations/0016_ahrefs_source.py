from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sources', '0015_screamingfrog_import')]
    operations = [migrations.AlterField(
        model_name='importbatch', name='source',
        field=models.CharField(max_length=24, choices=[
            ('gsc', 'Google Search Console'), ('sf', 'Screaming Frog'), ('ahrefs', 'Ahrefs')]))]
