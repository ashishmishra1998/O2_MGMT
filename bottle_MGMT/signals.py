from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .utils import create_default_users


@receiver(post_migrate)
def create_defaults_after_auth(sender, app_config=None, **kwargs):
    # Only create users after the auth app has been migrated, so the auth_user table exists
    if app_config and app_config.name == 'django.contrib.auth':
        try:
            create_default_users()
        except Exception:
            # Avoid breaking migrations flow; it's safe to ignore and try next run
            pass


