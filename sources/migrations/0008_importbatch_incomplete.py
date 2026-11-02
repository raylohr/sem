from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sources', '0007_semscan')]
    operations = [migrations.AlterField(model_name='importbatch',name='status',field=models.CharField(max_length=12,default='pending',choices=[
        ('pending','Wacht op verwerking'),('processing','Wordt verwerkt'),('complete','Voltooid'),
        ('incomplete','Onvolledig'),('failed','Mislukt'),('cancelled','Geannuleerd')]))]
