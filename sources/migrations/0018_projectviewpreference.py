from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('sources', '0017_inventorypreference'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='ProjectViewPreference', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('inventory_query', models.TextField(blank=True)),
        ('performance', models.JSONField(default=dict)),
        ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.project')),
        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
    ], options={'constraints': [models.UniqueConstraint(fields=('user', 'project'), name='unique_user_project_view')]})]
