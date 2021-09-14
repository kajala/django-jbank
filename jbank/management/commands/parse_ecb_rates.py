import logging
from datetime import timedelta
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jutil.command import SafeCommand
from jbank.ecb import download_euro_exchange_rates_xml, parse_euro_exchange_rates_xml
from jutil.format import format_xml
from jbank.models import CurrencyExchangeSource, CurrencyExchange

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses European Central Bank exchange rates. Can use either pre-downloaded file or download from online (default)."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--file", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--xml-only", action="store_true")
        parser.add_argument("--delete-older-than-days", type=int)

    def do(self, *args, **options):  # pylint: disable=too-many-branches,too-many-locals
        if options["file"]:
            with open(options["file"], "rt", encoding="utf-8") as fp:
                content = fp.read()
        else:
            content = download_euro_exchange_rates_xml()

        verbose = options["verbose"]
        if verbose or options["xml_only"]:
            print(format_xml(content))
            if options["xml_only"]:
                return

        rates = parse_euro_exchange_rates_xml(content)
        if verbose:
            for record_date, currency, rate in rates:
                print(record_date, currency, rate)

        delete_old_date = None
        delete_old_days = options["delete_older_than_days"]
        if delete_old_days:
            delete_old_date = now().date() - timedelta(days=delete_old_days)

        source, created = CurrencyExchangeSource.objects.get_or_create(name="European Central Bank")
        for record_date, currency, rate in rates:
            if delete_old_date and record_date < delete_old_date:
                continue
            created = CurrencyExchange.objects.get_or_create(
                record_date=record_date,
                source_currency="EUR",
                unit_currency="EUR",
                target_currency=currency,
                exchange_rate=rate,
                source=source,
            )[1]
            if created and verbose:
                print("({}, {}, {}) created".format(record_date, currency, rate))

        if delete_old_date:
            qs = CurrencyExchange.objects.filter(record_date__lt=delete_old_date, recorddetail_set=None)
            for e in qs:
                try:
                    e.delete()
                except Exception as err:
                    logger.error("Failed to delete %s: %s", e, err)
