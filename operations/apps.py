from django.apps import AppConfig

class OperationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'operations'
    verbose_name = 'Field Operations'

    def ready(self):
        # Import signals if needed
        pass
