import logging
from os.path import basename
from django.core.management import CommandParser
from jbank.files import list_dir_files
from jbank.models import Payout, PayoutStatus, PAYOUT_PAID
from jbank.sepa import Pain002
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Parses pain.002 payment response .XP files and updates Payout status'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--test', action='store_true')
        parser.add_argument('--verbose', action='store_true')

    def do(self, *args, **options):
        files = list_dir_files(options['path'], '.XP')
        for f in files:
            f_base = basename(f)
            if PayoutStatus.objects.filter(file_name=f_base).count() > 0:
                if options['verbose']:
                    print('Skipping payment status file', f_base)
                continue
            if options['verbose']:
                print('Importing payment status file', f_base)
            with open(f, 'rb') as fp:
                s = Pain002(fp.read())
                p = Payout.objects.filter(msg_id=s.original_msg_id).first()
                ps = PayoutStatus(payout=p, file_name=f_base, msg_id=s.msg_id, original_msg_id=s.original_msg_id, group_status=s.group_status, status_reason=s.status_reason[:255])
                ps.full_clean()
                if options['test']:
                    print(p, ps)
                    continue
                ps.save()
                if p:
                    logger.info('{} status updated {}'.format(p, ps))
                    if ps.is_accepted:
                        p.state = PAYOUT_PAID
                        p.paid_date = s.credit_datetime
                        p.save(update_fields=['state', 'paid_date'])
