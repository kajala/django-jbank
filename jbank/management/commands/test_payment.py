import logging
from decimal import Decimal
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jbank.helpers import make_msg_id
from jbank.models import Payout, PayoutParty

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Makes test payment"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--payer-id", type=int, default=1)
        parser.add_argument("--recipient-id", type=int, default=2)
        parser.add_argument("--messages", type=str, default="test payment")
        parser.add_argument("--amount", type=Decimal, default=Decimal("1.23"))
        parser.add_argument("--ws", type=int, default=1)

    def do(self, *args, **options):
        payer = PayoutParty.objects.get(id=options["payer_id"])
        p = Payout(
            account=payer.payouts_account,
            payer=payer,
            recipient_id=options["recipient_id"],
            messages=options["messages"],
            msg_id=make_msg_id(),
            amount=options["amount"],
            connection_id=options["ws"],
        )
        p.full_clean()
        p.save()
        print("{} created".format(p))
