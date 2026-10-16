import logging
import os.path
from django.core.management.base import CommandParser
from jutil.admin import admin_log
from jutil.format import json_dumps
from jbank.models import ReferencePaymentBatchFile, ReferencePaymentRecord, ReferencePaymentBatch
from jbank.parsers import parse_filename_suffix
from jbank.services import (
    find_reference_payment_batch_by_svm_batch_data,
    create_reference_payment_batch_by_svm_batch_data,
    find_reference_payment_record_by_svm_record_data,
    create_reference_payment_record_by_svm_record_data,
)
from jbank.svm import parse_svm_batches_from_file, SVM_STATEMENT_SUFFIXES
from jutil.command import SafeCommand, add_date_range_arguments, parse_date_range_arguments

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Re-parses old bank settlement .SVM (saapuvat viitemaksut) files. Used for adding missing fields."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--file", type=str)
        parser.add_argument("--create-missing", action="store_true")
        parser.add_argument("--verbose", action="store_true")
        add_date_range_arguments(parser)

    def do(self, *args, **kwargs):  # pylint: disable=too-many-branches
        begin, end = parse_date_range_arguments(kwargs, "prev_7d")[:2]
        create_missing = kwargs["create_missing"]
        verbose = kwargs["verbose"]
        qs = ReferencePaymentBatchFile.objects.all()
        if kwargs["file"]:
            qs = qs.filter(file__endswith=kwargs["file"])
        else:
            qs = qs.filter(created__range=(begin, end))
        logger.info("Re-parsing %s SVM files", qs.count())
        for file in qs.order_by("id").distinct():
            assert isinstance(file, ReferencePaymentBatchFile)
            full_path = file.full_path
            if parse_filename_suffix(full_path).upper() not in SVM_STATEMENT_SUFFIXES:
                logger.info("Ignoring non-SVM file %s", full_path)
                continue
            if not os.path.isfile(full_path):
                logger.warning("Missing file %s", full_path)
                continue
            logger.info("Processing %s BEGIN", file)

            batches_data = parse_svm_batches_from_file(file.full_path)
            for batch_data in batches_data:
                batch = find_reference_payment_batch_by_svm_batch_data(file, batch_data)
                if batch is None:
                    logger.info("%s: ReferencePaymentBatch missing %s", file, json_dumps(batch_data))
                    if create_missing:
                        batch = create_reference_payment_batch_by_svm_batch_data(file, batch_data)
                        assert isinstance(batch, ReferencePaymentBatch)
                else:
                    assert isinstance(batch, ReferencePaymentBatch)
                    if verbose:
                        logger.debug("%s found = %s", json_dumps(batch_data), batch)

                for rec_data in batch_data["records"]:
                    rec = find_reference_payment_record_by_svm_record_data(file, rec_data)
                    if rec is None:
                        logger.info("%s: ReferencePaymentRecord missing %s", file, json_dumps(rec_data))
                        if create_missing:
                            rec = create_reference_payment_record_by_svm_record_data(batch, rec_data)
                            admin_log([rec], "Created missing record")
                            admin_log([file], f"Created missing record id={rec.pk}")
                    else:
                        assert isinstance(rec, ReferencePaymentRecord)
                        if verbose:
                            logger.debug("%s found = %s", json_dumps(rec_data), rec)
                if batch is not None:
                    batch.update_total_amount()
            file.update_total_amount()

            logger.info("Processing %s END", file)
