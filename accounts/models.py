from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """User model established before the first database migration."""
