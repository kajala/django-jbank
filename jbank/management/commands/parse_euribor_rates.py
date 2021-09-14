import logging
from datetime import timedelta
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jbank.euribor import fetch_latest_euribor_rates
from jbank.models import EuriborRate
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses Euribor reference rates published daily by the European Money Markets Institute (EMMI) from suomenpankki.fi feed"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--delete-older-than-days", type=int)

    def do(self, *args, **kwargs):  # pylint: disable=too-many-branches,too-many-locals
        rates = fetch_latest_euribor_rates(commit=kwargs["commit"], verbose=kwargs["verbose"])
        for rate in rates:
            print(f"{rate.record_date.isoformat()},{rate.name},{rate.rate} %")
        if kwargs["delete_older_than_days"]:
            old = now() - timedelta(days=kwargs["delete_older_than_days"])
            EuriborRate.objects.all().filter(created__lt=old).delete()
