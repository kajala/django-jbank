# pylint: disable=logging-format-interpolation
import logging
from django.core.management.base import CommandParser
from jbank.models import ReferencePaymentBatchFile, ReferencePaymentRecord
from jbank.svm import parse_svm_batches_from_file
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Re-parses old bank settlement .SVM (saapuvat viitemaksut) files. Used for adding missing fields."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--file", type=str)

    def do(self, *args, **options):
        logger.info("Re-parsing SVM files to update fields")
        qs = ReferencePaymentBatchFile.objects.all()
        if options["file"]:
            qs = qs.filter(file=options["file"])
        for file in qs.order_by("id"):
            assert isinstance(file, ReferencePaymentBatchFile)
            logger.info("Processing {} BEGIN".format(file))
            batches = parse_svm_batches_from_file(file.full_path)
            for batch in batches:
                for e in batch["records"]:  # pylint: disable=too-many-branches
                    # check missing line_number
                    e2 = ReferencePaymentRecord.objects.filter(
                        batch__file=file,
                        line_number=0,
                        record_type=e["record_type"],
                        account_number=e["account_number"],
                        paid_date=e["paid_date"],
                        archive_identifier=e["archive_identifier"],
                        remittance_info=e["remittance_info"],
                        payer_name=e["payer_name"],
                        currency_identifier=e["currency_identifier"],
                        name_source=e["name_source"],
                        correction_identifier=e["correction_identifier"],
                        delivery_method=e["delivery_method"],
                        receipt_code=e["receipt_code"],
                    ).first()
                    if e2:
                        e2.line_number = e["line_number"]
                        e2.save()
                        logger.info("Updated {} line number to {}".format(e2, e2.line_number))
            logger.info("Processing {} END".format(file))
