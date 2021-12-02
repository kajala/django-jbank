import logging
import os
from datetime import datetime, date
from os.path import basename
from typing import Any, Tuple, Optional, List
import pytz
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction, models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jacc.models import Account, AccountType, EntryType
from jbank.models import (
    Statement,
    StatementRecord,
    StatementRecordSepaInfo,
    ReferencePaymentBatch,
    ReferencePaymentRecord,
    StatementFile,
    ReferencePaymentBatchFile,
    Payout,
    PayoutStatus,
    PAYOUT_PAID,
)
from jbank.sepa import Pain002
import re
from lxml import etree, objectify  # type: ignore  # pytype: disable=import-error
from jutil.parse import parse_datetime
from jutil.format import strip_media_root, is_media_full_path

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

MESSAGE_STATEMENT_RECORD_FIELDS = ("messages", "client_messages", "bank_messages")

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

ASSIGNABLE_STATEMENT_RECORD_SEPA_INFO_FIELDS = (
    "reference",
    "iban_account_number",
    "bic_code",
    "recipient_name_detail",
    "payer_name_detail",
    "identifier",
    "archive_identifier",
)

ASSIGNABLE_REFERENCE_PAYMENT_BATCH_HEADER_FIELDS = (
    "record_date",
    "institution_identifier",
    "service_identifier",
    "currency_identifier",
)

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


logger = logging.getLogger(__name__)


@transaction.atomic  # noqa
def create_statement(statement_data: dict, name: str, file: StatementFile, **kw) -> Statement:  # noqa
    """
    Creates Statement from statement data parsed by parse_tiliote_statements()
    :param statement_data: See parse_tiliote_statements
    :param name: File name of the account statement
    :param file: Source statement file
    :return: Statement
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
    """
    Creates ReferencePaymentBatch from data parsed by parse_svm_batches()
    :param batch_data: See parse_svm_batches
    :param name: File name of the batch file
    :return: ReferencePaymentBatch
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


def get_or_create_bank_account_entry_types() -> List[EntryType]:
    e_type_codes = [
        settings.E_BANK_DEPOSIT,
        settings.E_BANK_WITHDRAW,
        settings.E_BANK_REFERENCE_PAYMENT,
        settings.E_BANK_REFUND,
        settings.E_BANK_PAYOUT,
    ]
    e_types: List[EntryType] = []
    for code in e_type_codes:
        e_type = EntryType.objects.get_or_create(
            code=code,
            defaults={
                "identifier": code,
                "name": code,
                "is_settlement": True,
                "is_payment": code in [settings.E_BANK_DEPOSIT, settings.E_BANK_REFERENCE_PAYMENT],
            },
        )[0]
        e_types.append(e_type)
    return e_types


def get_or_create_bank_account(account_number: str, currency: str = "EUR") -> Account:
    a_type = AccountType.objects.get_or_create(code=settings.ACCOUNT_BANK_ACCOUNT, is_asset=True, defaults={"name": _("bank account")})[0]
    acc, created = Account.objects.get_or_create(name=account_number, type=a_type, currency=currency)
    if created:
        get_or_create_bank_account_entry_types()
    return acc


def process_pain002_file_content(bcontent: bytes, filename: str, created: Optional[datetime] = None):
    if not created:
        created = now()
    s = Pain002(bcontent)
    p = Payout.objects.filter(msg_id=s.original_msg_id).first()

    ps = PayoutStatus(
        payout=p,
        file_name=basename(filename),
        file_path=strip_media_root(filename),
        msg_id=s.msg_id,
        original_msg_id=s.original_msg_id,
        group_status=s.group_status,
        status_reason=s.status_reason[:255],
        created=created,
        timestamp=s.credit_datetime,
    )
    ps.full_clean()
    fields = (
        "payout",
        "file_name",
        "response_code",
        "response_text",
        "msg_id",
        "original_msg_id",
        "group_status",
        "status_reason",
    )
    params = {}
    for k in fields:
        params[k] = getattr(ps, k)
    ps_old = PayoutStatus.objects.filter(**params).first()
    if ps_old:
        ps = ps_old
    else:
        ps.save()
        logger.info("%s status updated %s", p, ps)
    if p:
        if ps.is_accepted:
            p.state = PAYOUT_PAID
            p.paid_date = s.credit_datetime
            p.save(update_fields=["state", "paid_date"])
            logger.info("%s marked as paid %s", p, ps)
    return ps


def make_msg_id() -> str:
    return re.sub(r"[^\d]", "", now().isoformat())[:-4]


def validate_xml(content: bytes, xsd_file_name: str):
    """
    Validates XML using XSD
    """
    schema = etree.XMLSchema(file=xsd_file_name)
    parser = objectify.makeparser(schema=schema)
    objectify.fromstring(content, parser)


def parse_start_and_end_date(tz: Any, **options) -> Tuple[Optional[date], Optional[date]]:
    start_date = None
    end_date = None
    time_now = now().astimezone(tz if tz else pytz.utc)
    if options["start_date"]:
        if options["start_date"] == "today":
            start_date = time_now.date()
        else:
            start_date = parse_datetime(options["start_date"]).date()  # type: ignore
        end_date = start_date
    if options["end_date"]:
        if options["end_date"] == "today":
            end_date = time_now.date()
        else:
            end_date = parse_datetime(options["end_date"]).date()  # type: ignore
    return start_date, end_date


def save_or_store_media(file: models.FileField, filename: str):
    """
    Saves FileField filename as relative path if it's under MEDIA_ROOT.
    Otherwise writes file under media root.
    """
    if is_media_full_path(filename):
        file.name = strip_media_root(filename)  # type: ignore
    else:
        with open(filename, "rb") as fp:
            plain_filename = os.path.basename(filename)
            file.save(plain_filename, File(fp))  # type: ignore  # noqa
