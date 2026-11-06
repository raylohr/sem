from django.db import migrations


class Migration(migrations.Migration):
    # Preserve migration identity; project-specific data seeding was removed.
    dependencies = [('catalog', '0005_label_url_path')]
    operations = []
