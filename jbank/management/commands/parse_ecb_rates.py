import base64
import logging
import os
from django.core.management import CommandParser
from jutil.command import SafeCommand

from jbank.ecb import download_euro_exchange_rates_xml, parse_euro_exchange_rates_xml
from jutil.format import format_xml

from jbank.models import CurrencyExchangeSource, CurrencyExchange

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Parses European Central Bank rates. Can use either pre-downloaded file or download from online (default). 
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--file', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--xml-only', action='store_true')

    def do(self, *args, **options):
        if options['file']:
            with open(options['file'], 'rt') as fp:
                content = fp.read()
        else:
            content = download_euro_exchange_rates_xml()

        verbose = options['verbose']
        if verbose or options['xml_only']:
            print(format_xml(content))
            if options['xml_only']:
                return

        rates = parse_euro_exchange_rates_xml(content)
        if verbose:
            for record_date, currency, rate in rates:
                print(record_date, currency, rate)

        source, created = CurrencyExchangeSource.objects.get_or_create(name='European Central Bank')
        for record_date, currency, rate in rates:
            obj, created = CurrencyExchange.objects.get_or_create(record_date=record_date, source_currency='EUR', target_currency=currency, exchange_rate=rate, source=source)
            if created and verbose:
                print('({}, {}, {}) created'.format(record_date, currency, rate))
