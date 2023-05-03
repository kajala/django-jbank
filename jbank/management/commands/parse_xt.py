import logging
import os
from pprint import pprint

from django.core.exceptions import ValidationError
from django.core.management.base import CommandParser
from django.db import transaction
from jbank.camt import (
    camt053_get_account_iban,
    camt053_create_statement,
    camt053_parse_statement_from_file,
    CAMT053_FILE_SUFFIXES,
    camt053_get_unified_str,
    camt053_get_account_currency,
)
from jbank.helpers import get_or_create_bank_account, save_or_store_media
from jbank.files import list_dir_files
from jbank.models import Statement, StatementFile, StatementRecord, StatementRecordDetail
from jbank.parsers import parse_filename_suffix
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses XML bank account statement (camt.053.001.02) files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--delete-old", action="store_true")
        parser.add_argument("--auto-create-accounts", action="store_true")
        parser.add_argument("--suffix", type=str)
        parser.add_argument("--resolve-original-filenames", action="store_true")
        parser.add_argument("--tag", type=str, default="")
        parser.add_argument("--parse-creditor-account-data", action="store_true", help="For data migration")

    def parse_creditor_account_data(self):  # pylint: disable=too-many-locals,too-many-branches
        for sf in StatementFile.objects.all():  # pylint: disable=too-many-nested-blocks
            assert isinstance(sf, StatementFile)
            full_path = sf.full_path
            if os.path.isfile(full_path) and parse_filename_suffix(full_path).upper() in CAMT053_FILE_SUFFIXES:
                logger.info("Parsing creditor account data of %s", full_path)
                statement_data = camt053_parse_statement_from_file(full_path)
                d_stmt = statement_data.get("BkToCstmrStmt", {}).get("Stmt", {})
                d_ntry = d_stmt.get("Ntry", [])
                recs = list(StatementRecord.objects.all().filter(statement__file=sf).order_by("id"))
                if len(recs) != len(d_ntry):
                    raise ValidationError(f"Statement record counts do not match in id={sf.id} ({sf})")
                for ix, ntry in enumerate(d_ntry):
                    rec = recs[ix]
                    assert isinstance(rec, StatementRecord)
                    for dtl_batch in ntry.get("NtryDtls", []):
                        rec_detail_list = list(StatementRecordDetail.objects.all().filter(record=rec))
                        if len(rec_detail_list) != len(dtl_batch.get("TxDtls", [])):
                            raise ValidationError(f"Statement record detail counts do not match in id={sf.id} ({sf})")
                        for dtl_ix, dtl in enumerate(dtl_batch.get("TxDtls", [])):
                            d = rec_detail_list[dtl_ix]
                            assert isinstance(d, StatementRecordDetail)
                            d_parties = dtl.get("RltdPties", {})
                            d_dbt = d_parties.get("Dbtr", {})
                            d.debtor_name = d_dbt.get("Nm", "")
                            d_udbt = d_parties.get("UltmtDbtr", {})
                            d.ultimate_debtor_name = d_udbt.get("Nm", "")
                            d_cdtr = d_parties.get("Cdtr", {})
                            d.creditor_name = d_cdtr.get("Nm", "")
                            d_cdtr_acct = d_parties.get("CdtrAcct", {})
                            d_cdtr_acct_id = d_cdtr_acct.get("Id", {})
                            d.creditor_account = d_cdtr_acct_id.get("IBAN", "")
                            if d.creditor_account:
                                d.creditor_account_scheme = "IBAN"
                            else:
                                d_cdtr_acct_id_othr = d_cdtr_acct_id.get("Othr") or {}
                                d.creditor_account_scheme = d_cdtr_acct_id_othr.get("SchmeNm", {}).get("Cd", "")
                                d.creditor_account = d_cdtr_acct_id_othr.get("Id") or ""
                            logger.info("%s creditor_account %s (%s)", rec, d.creditor_account, d.creditor_account_scheme)
                            d.save()

                    if not rec.recipient_account_number:
                        rec.recipient_account_number = camt053_get_unified_str(rec.detail_set.all(), "creditor_account")
                        if rec.recipient_account_number:
                            rec.save(update_fields=["recipient_account_number"])
                            logger.info("%s recipient_account_number %s", rec, rec.recipient_account_number)

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        if options["parse_creditor_account_data"]:
            self.parse_creditor_account_data()
            return

        files = list_dir_files(options["path"], options["suffix"])
        for filename in files:
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in CAMT053_FILE_SUFFIXES:
                print("Ignoring non-CAMT53 file {}".format(filename))
                continue

            if options["resolve_original_filenames"]:
                found = StatementFile.objects.filter(statement__name=plain_filename).first()
                if found and not found.original_filename:
                    assert isinstance(found, StatementFile)
                    found.original_filename = filename
                    found.save(update_fields=["original_filename"])
                    logger.info("Original XML statement filename of %s resolved to %s", found, filename)

            if options["test"]:
                statement = camt053_parse_statement_from_file(filename)
                pprint(statement)
                continue

            if options["delete_old"]:
                Statement.objects.filter(name=plain_filename).delete()

            if not Statement.objects.filter(name=plain_filename).first():
                print("Importing statement file {}".format(plain_filename))

                statement = camt053_parse_statement_from_file(filename)
                if options["verbose"]:
                    pprint(statement)

                with transaction.atomic():
                    file = StatementFile(original_filename=filename, tag=options["tag"])
                    file.save()
                    save_or_store_media(file.file, filename)
                    file.save()

                    for data in [statement]:
                        if options["auto_create_accounts"]:
                            account_number = camt053_get_account_iban(data)
                            currency = camt053_get_account_currency(data)
                            if account_number:
                                get_or_create_bank_account(account_number, currency)

                        camt053_create_statement(data, name=plain_filename, file=file)  # pytype: disable=not-callable
            else:
                print("Skipping statement file {}".format(filename))
