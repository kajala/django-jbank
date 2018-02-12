from django.apps import AppConfig
from django.utils.translation import ugettext_lazy as _


class JbankConfig(AppConfig):
    name = 'jbank'
    verbose_name = _('Bank Integration')
