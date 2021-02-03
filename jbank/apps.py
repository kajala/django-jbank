from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class JbankConfig(AppConfig):
    name = "jbank"
    verbose_name = _("Bank Integration")
