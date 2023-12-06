from os.path import basename
from typing import Union, Dict, List, Optional, Any
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from jacc.models import Account, EntryType
from jbank.helpers import MESSAGE_STATEMENT_RECORD_FIELDS
from jbank.models import (
    StatementFile,
    Statement,
    StatementRecord,
    StatementRecordSepaInfo,
    ReferencePaymentBatchFile,
    ReferencePaymentBatch,
    ReferencePaymentRecord,
)
from jbank.parsers import parse_filename_suffix, parse_records, convert_date_fields, convert_decimal_fields

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

SVM_STATEMENT_SUFFIXES = ("SVM", "TXT", "KTL")

SVM_FILE_HEADER_DATES = (("record_date", "record_time"),)

SVM_FILE_HEADER_TYPES = ("0",)

SVM_FILE_HEADER = (
    ("statement_type", "9(1)", "P"),
    ("record_date", "9(6)", "P"),
    ("record_time", "9(4)", "P"),
    ("institution_identifier", "X(2)", "P"),
    ("service_identifier", "X(9)", "P"),
    ("currency_identifier", "X(1)", "P"),
    ("pad01", "X(67)", "P"),
)

SVM_FILE_RECORD_TYPES = ("3", "5")

SVM_FILE_RECORD_DECIMALS = ("amount",)

SVM_FILE_RECORD_DATES = (
    "record_date",
    "paid_date",
)

SVM_FILE_RECORD = (
    ("record_type", "9(1)", "P"),  # 3=viitesiirto, 5=suoraveloitus
    ("account_number", "9(14)", "P"),
    ("record_date", "9(6)", "P"),
    ("paid_date", "9(6)", "P"),
    ("archive_identifier", "X(16)", "P"),
    ("remittance_info", "X(20)", "P"),
    ("payer_name", "X(12)", "P"),
    ("currency_identifier", "X(1)", "P"),  # 1=eur
    ("name_source", "X", "V"),
    ("amount", "9(10)", "P"),
    ("correction_identifier", "X", "V"),  # 0=normal, 1=correction
    ("delivery_method", "X", "P"),  # A=asiakkaalta, K=konttorista, J=pankin jarjestelmasta
    ("receipt_code", "X", "P"),
)

SVM_FILE_SUMMARY_TYPES = ("9",)

SVM_FILE_SUMMARY_DECIMALS = (
    "record_amount",
    "correction_amount",
)

SVM_FILE_SUMMARY = (
    ("record_type", "9(1)", "P"),  # 9
    ("record_count", "9(6)", "P"),
    ("record_amount", "9(11)", "P"),
    ("correction_count", "9(6)", "P"),
    ("correction_amount", "9(11)", "P"),
    ("pad01", "X(5)", "P"),
)


def parse_svm_batches_from_file(filename: str) -> list:
    if parse_filename_suffix(filename).upper() not in SVM_STATEMENT_SUFFIXES:
        raise ValidationError(
            _('File {filename} has unrecognized ({suffixes}) suffix for file type "{file_type}"').format(
                filename=filename, suffixes=", ".join(SVM_STATEMENT_SUFFIXES), file_type="saapuvat viitemaksut"
            )
        )
    with open(filename, "rt", encoding="ISO-8859-1") as fp:
        return parse_svm_batches(fp.read(), filename=basename(filename))  # type: ignore


def parse_svm_batches(content: str, filename: str) -> list:
    lines = content.split("\n")
    nlines = len(lines)
    line_number = 1
    tz = ZoneInfo("Europe/Helsinki")
    batches = []
    header: Optional[Dict[str, Union[int, str]]] = None
    records: List[Dict[str, Union[int, str]]] = []
    summary: Optional[Dict[str, Union[int, str]]] = None

    while line_number <= nlines:
        line = lines[line_number - 1]
        if line.strip() == "":
            line_number += 1
            continue
        record_type = line[:1]

        if record_type in SVM_FILE_HEADER_TYPES:
            if header:
                batches.append(combine_svm_batch(header, records, summary))
                header, records, summary = None, [], None
            header = parse_records(lines[line_number - 1], SVM_FILE_HEADER, line_number=line_number)
            convert_date_fields(header, SVM_FILE_HEADER_DATES, tz)
            line_number += 1
        elif record_type in SVM_FILE_RECORD_TYPES:
            record = parse_records(line, SVM_FILE_RECORD, line_number=line_number)
            convert_date_fields(record, SVM_FILE_RECORD_DATES, tz)
            convert_decimal_fields(record, SVM_FILE_RECORD_DECIMALS)
            line_number += 1
            records.append(record)
        elif record_type in SVM_FILE_SUMMARY_TYPES:
            summary = parse_records(line, SVM_FILE_SUMMARY, line_number=line_number)
            convert_decimal_fields(summary, SVM_FILE_SUMMARY_DECIMALS)
            line_number += 1
        else:
            raise ValidationError(_("Unknown record type on {}({}): {}").format(filename, line_number, record_type))

    batches.append(combine_svm_batch(header, records, summary))
    return batches


def combine_svm_batch(header: Optional[Dict[str, Any]], records: List[Dict[str, Union[int, str]]], summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = {"header": header, "records": records}
    if summary is not None:
        data["summary"] = summary
    return data


@transaction.atomic  # noqa
def create_statement(statement_data: dict, name: str, file: StatementFile, **kw) -> Statement:  # noqa
    """Creates Statement from statement data parsed by parse_tiliote_statements()

    Args:
        statement_data: See parse_tiliote_statements
        name: File name of the account statement
        file: Source statement file

    Returns:
        Statement
    """
    if "header" not in statement_data or not statement_data["header"]:
        raise ValidationError("Invalid header field in statement data {}: {}".format(name, statement_data.get("header")))
    header = statement_data["header"]

    account_number = header["account_number"]
    if not account_number:
        raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=""))
    accounts = list(Account.objects.filter(name=account_number))
    if len(accounts) != 1:
        raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=account_number))
    account = accounts[0]
    assert isinstance(account, Account)

    if Statement.objects.filter(name=name, account=account).first():
        raise ValidationError("Bank account {} statement {} of processed already".format(account_number, name))
    stm = Statement(name=name, account=account, file=file)
    for k in ASSIGNABLE_STATEMENT_HEADER_FIELDS:
        if k in header:
            setattr(stm, k, header[k])
    # pprint(statement_data['header'])
    for k, v in kw.items():
        setattr(stm, k, v)
    stm.full_clean()
    stm.save()

    if EntryType.objects.filter(code=settings.E_BANK_DEPOSIT).count() == 0:
        raise ValidationError(_("entry.type.missing") + " ({}): {}".format("settings.E_BANK_DEPOSIT", settings.E_BANK_DEPOSIT))
    if EntryType.objects.filter(code=settings.E_BANK_WITHDRAW).count() == 0:
        raise ValidationError(_("entry.type.missing") + " ({}): {}".format("settings.E_BANK_WITHDRAW", settings.E_BANK_WITHDRAW))
    entry_types = {
        "1": EntryType.objects.get(code=settings.E_BANK_DEPOSIT),
        "2": EntryType.objects.get(code=settings.E_BANK_WITHDRAW),
    }

    for rec_data in statement_data["records"]:
        line_number = rec_data["line_number"]
        e_type = entry_types.get(rec_data["entry_type"])
        rec = StatementRecord(statement=stm, account=account, type=e_type, line_number=line_number)
        for k in ASSIGNABLE_STATEMENT_RECORD_FIELDS:
            if k in rec_data:
                setattr(rec, k, rec_data[k])
        for k in MESSAGE_STATEMENT_RECORD_FIELDS:
            if k in rec_data:
                setattr(rec, k, "\n".join(rec_data[k]))
        rec.full_clean()
        rec.save()

        if "sepa" in rec_data:
            sepa_info_data = rec_data["sepa"]
            sepa_info = StatementRecordSepaInfo(record=rec)
            for k in ASSIGNABLE_STATEMENT_RECORD_SEPA_INFO_FIELDS:
                if k in sepa_info_data:
                    setattr(sepa_info, k, sepa_info_data[k])
            # pprint(rec_data['sepa'])
            sepa_info.full_clean()
            sepa_info.save()

    return stm


@transaction.atomic
def create_reference_payment_batch(batch_data: dict, name: str, file: ReferencePaymentBatchFile, **kw) -> ReferencePaymentBatch:
    """Creates ReferencePaymentBatch from data parsed by parse_svm_batches()

    Args:
        batch_data: See parse_svm_batches
        name: File name of the batch file

    Returns:
        ReferencePaymentBatch
    """
    if ReferencePaymentBatch.objects.exclude(file=file).filter(name=name).first():
        raise ValidationError("Reference payment batch file {} already exists".format(name))

    if "header" not in batch_data or not batch_data["header"]:
        raise ValidationError("Invalid header field in reference payment batch data {}: {}".format(name, batch_data.get("header")))
    header = batch_data["header"]

    batch = ReferencePaymentBatch(name=name, file=file)
    for k in ASSIGNABLE_REFERENCE_PAYMENT_BATCH_HEADER_FIELDS:
        if k in header:
            setattr(batch, k, header[k])
    # pprint(statement_data['header'])
    for k, v in kw.items():
        setattr(batch, k, v)
    batch.full_clean()
    batch.save()
    e_type = EntryType.objects.get(code=settings.E_BANK_REFERENCE_PAYMENT)

    for rec_data in batch_data["records"]:
        line_number = rec_data["line_number"]
        account_number = rec_data["account_number"]
        if not account_number:
            raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=""))
        accounts = list(Account.objects.filter(name=account_number))
        if len(accounts) != 1:
            raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=account_number))
        account = accounts[0]
        assert isinstance(account, Account)

        rec = ReferencePaymentRecord(batch=batch, account=account, type=e_type, line_number=line_number)
        for k in ASSIGNABLE_REFERENCE_PAYMENT_RECORD_FIELDS:
            if k in rec_data:
                setattr(rec, k, rec_data[k])
        # pprint(rec_data)
        rec.full_clean()
        rec.save()

    return batch


ASSIGNABLE_REFERENCE_PAYMENT_RECORD_FIELDS = (
    "record_type",
    "account_number",
    "record_date",
    "paid_date",
    "archive_identifier",
    "remittance_info",
    "payer_name",
    "currency_identifier",
    "name_source",
    "amount",
    "correction_identifier",
    "delivery_method",
    "receipt_code",
)
ASSIGNABLE_REFERENCE_PAYMENT_BATCH_HEADER_FIELDS = (
    "record_date",
    "institution_identifier",
    "service_identifier",
    "currency_identifier",
)
ASSIGNABLE_STATEMENT_RECORD_SEPA_INFO_FIELDS = (
    "reference",
    "iban_account_number",
    "bic_code",
    "recipient_name_detail",
    "payer_name_detail",
    "identifier",
    "archive_identifier",
)
ASSIGNABLE_STATEMENT_RECORD_FIELDS = (
    "record_date",
    "value_date",
    "paid_date",
    "record_number",
    "archive_identifier",
    "entry_type",
    "record_code",
    "record_description",
    "amount",
    "receipt_code",
    "delivery_method",
    "name",
    "name_source",
    "recipient_account_number",
    "recipient_account_number_changed",
    "remittance_info",
)
ASSIGNABLE_STATEMENT_HEADER_FIELDS = (
    "account_number",
    "statement_number",
    "begin_date",
    "end_date",
    "record_date",
    "customer_identifier",
    "begin_balance_date",
    "begin_balance",
    "record_count",
    "currency_code",
    "account_name",
    "account_limit",
    "owner_name",
    "contact_info_1",
    "contact_info_2",
    "bank_specific_info_1",
    "iban",
    "bic",
)
