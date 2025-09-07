from django.apps import AppConfig


class BottleMgmtConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bottle_MGMT'

    def ready(self):
        # Import signals to ensure post_migrate hooks are registered
        from . import signals  # noqa: F401