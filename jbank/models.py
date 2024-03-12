import base64
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, time, date, timezone
from decimal import Decimal
from os.path import basename, join
from pathlib import Path
from typing import List, Optional, Tuple
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.template.loader import get_template
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jacc.helpers import sum_queryset
from jacc.models import AccountEntry, AccountEntrySourceFile, Account, AccountEntryManager
from jbank.x509_helpers import get_x509_cert_from_file
from jutil.modelfields import SafeCharField, SafeTextField
from jutil.format import format_xml, get_media_full_path, choices_label
from jutil.validators import iban_validator, iban_bic, iso_payment_reference_validator, fi_payment_reference_validator

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


JBANK_BIN_PATH = Path(__file__).absolute().parent.joinpath("bin")

RECORD_ENTRY_TYPE = (
    ("1", _("Deposit")),
    ("2", _("Withdrawal")),
    ("3", _("Deposit Correction")),
    ("4", _("Withdrawal Correction")),
)

RECORD_CODES = (
    ("700", _("Money Transfer (In/Out)")),
    ("701", _("Recurring Payment (In/Out)")),
    ("702", _("Bill Payment (Out)")),
    ("703", _("Payment Terminal Deposit (In)")),
    ("704", _("Bank Draft (In/Out)")),
    ("705", _("Reference Payments (In)")),
    ("706", _("Payment Service (Out)")),
    ("710", _("Deposit (In)")),
    ("720", _("Withdrawal (Out)")),
    ("721", _("Card Payment (Out)")),
    ("722", _("Check (Out)")),
    ("730", _("Bank Fees (Out)")),
    ("740", _("Interests Charged (Out)")),
    ("750", _("Interests Credited (In)")),
    ("760", _("Loan (Out)")),
    ("761", _("Loan Payment (Out)")),
    ("770", _("Foreign Transfer (In/Out)")),
    ("780", _("Zero Balancing (In/Out)")),
    ("781", _("Sweeping (In/Out)")),
    ("782", _("Topping (In/Out)")),
)

RECORD_DOMAIN = (
    ("PMNT", _("Money Transfer (In/Out)")),
    ("LDAS", _("Loan Payment (Out)")),
    ("CAMT", _("Cash Management")),
    ("ACMT", _("Account Management")),
    ("XTND", _("Entended Domain")),
    ("SECU", _("Securities")),
    ("FORX", _("Foreign Exchange")),
    ("XTND", _("Entended Domain")),
    ("NTAV", _("Not Available")),
)

RECEIPT_CODE = (
    ("", ""),
    ("0", "(0)"),
    ("E", _("Separate")),
    ("P", _("Separate/Paper")),
)

CURRENCY_IDENTIFIERS = (("1", "EUR"),)

NAME_SOURCES = (
    ("", _("Not Set")),
    ("A", _("From Customer")),
    ("K", _("From Bank Clerk")),
    ("J", _("From Bank System")),
)

CORRECTION_IDENTIFIER = (
    ("0", _("Regular Entry")),
    ("1", _("Correction Entry")),
)

DELIVERY_METHOD_UNKNOWN = ""
DELIVERY_FROM_CUSTOMER = "A"
DELIVERY_FROM_BANK_CLERK = "K"
DELIVERY_FROM_BANK_SYSTEM = "J"

DELIVERY_METHOD = (
    (DELIVERY_METHOD_UNKNOWN, ""),
    (DELIVERY_FROM_CUSTOMER, _("From Customer")),
    (DELIVERY_FROM_BANK_CLERK, _("From Bank Clerk")),
    (DELIVERY_FROM_BANK_SYSTEM, _("From Bank System")),
)

PAYOUT_ON_HOLD = "H"
PAYOUT_WAITING_PROCESSING = "W"
PAYOUT_WAITING_UPLOAD = "U"
PAYOUT_UPLOADED = "D"
PAYOUT_PAID = "P"
PAYOUT_CANCELED = "C"
PAYOUT_ERROR = "E"

PAYOUT_STATE = (
    (PAYOUT_ON_HOLD, _("on hold")),
    (PAYOUT_WAITING_PROCESSING, _("waiting processing")),
    (PAYOUT_WAITING_UPLOAD, _("waiting upload")),
    (PAYOUT_UPLOADED, _("uploaded")),
    (PAYOUT_PAID, _("paid")),
    (PAYOUT_CANCELED, _("canceled")),
    (PAYOUT_ERROR, _("error")),
)


class Statement(AccountEntrySourceFile):
    file = models.ForeignKey("StatementFile", blank=True, default=None, null=True, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, related_name="+", on_delete=models.PROTECT)
    account_number = SafeCharField(_("account number"), max_length=32, db_index=True)
    statement_identifier = SafeCharField(_("statement identifier"), max_length=48, db_index=True, blank=True, default="")
    statement_number = models.SmallIntegerField(_("statement number"), db_index=True)
    begin_date = models.DateField(_("begin date"), db_index=True, null=True, default=None, blank=True)
    end_date = models.DateField(_("end date"), db_index=True)
    record_date = models.DateTimeField(_("record date"), db_index=True)
    customer_identifier = SafeCharField(_("customer identifier"), max_length=64, blank=True, default="")
    begin_balance_date = models.DateField(_("begin balance date"), null=True, blank=True, default=None)
    begin_balance = models.DecimalField(_("begin balance"), max_digits=10, decimal_places=2)
    record_count = models.IntegerField(_("record count"), null=True, default=None)
    currency_code = SafeCharField(_("currency code"), max_length=3)
    account_name = SafeCharField(_("account name"), max_length=32, blank=True, default="")
    account_limit = models.DecimalField(_("account limit"), max_digits=10, decimal_places=2, blank=True, default=None, null=True)
    owner_name = SafeCharField(_("owner name"), max_length=64)
    contact_info_1 = SafeCharField(_("contact info (1)"), max_length=64, blank=True, default="")
    contact_info_2 = SafeCharField(_("contact info (2)"), max_length=64, blank=True, default="")
    bank_specific_info_1 = SafeCharField(_("bank specific info (1)"), max_length=1024, blank=True, default="")
    iban = SafeCharField(_("IBAN"), max_length=32, db_index=True)
    bic = SafeCharField(_("BIC"), max_length=11, db_index=True)

    class Meta:
        verbose_name = _("statement")
        verbose_name_plural = _("statements")


class PaymentRecordManager(AccountEntryManager):
    def filter_matched(self):
        return self.exclude(child_set=None)

    def filter_unmatched(self):
        return self.filter(child_set=None)


class StatementRecord(AccountEntry):
    objects: models.Manager = PaymentRecordManager()  # type: ignore
    statement = models.ForeignKey(Statement, verbose_name=_("statement"), related_name="record_set", on_delete=models.CASCADE)
    line_number = models.SmallIntegerField(_("line number"), default=None, null=True, blank=True)
    record_number = models.IntegerField(_("record number"), default=None, null=True, blank=True)
    archive_identifier = SafeCharField(_("archive identifier"), max_length=64, blank=True, default="", db_index=True)
    record_date = models.DateField(_("record date"), db_index=True)
    value_date = models.DateField(_("value date"), db_index=True, blank=True, null=True, default=None)
    paid_date = models.DateField(_("paid date"), db_index=True, blank=True, null=True, default=None)
    entry_type = SafeCharField(_("entry type"), max_length=1, choices=RECORD_ENTRY_TYPE, db_index=True)
    record_code = SafeCharField(_("record type"), max_length=4, choices=RECORD_CODES, db_index=True, blank=True)
    record_domain = SafeCharField(_("record domain"), max_length=4, choices=RECORD_DOMAIN, db_index=True, blank=True)
    family_code = SafeCharField(_("family code"), max_length=4, db_index=True, blank=True, default="")
    sub_family_code = SafeCharField(_("sub family code"), max_length=4, db_index=True, blank=True, default="")
    record_description = SafeCharField(_("record description"), max_length=128, blank=True, default="")
    receipt_code = SafeCharField(_("receipt code"), max_length=1, choices=RECEIPT_CODE, db_index=True, blank=True)
    delivery_method = SafeCharField(_("delivery method"), max_length=1, db_index=True, choices=DELIVERY_METHOD, blank=True)
    name = SafeCharField(_("name"), max_length=128, blank=True, db_index=True)
    name_source = SafeCharField(_("name source"), max_length=1, blank=True, choices=NAME_SOURCES)
    recipient_account_number = SafeCharField(_("recipient account number"), max_length=32, blank=True, db_index=True)
    recipient_account_number_changed = SafeCharField(_("recipient account number changed"), max_length=1, blank=True)
    remittance_info = SafeCharField(_("remittance info"), max_length=35, db_index=True, blank=True)
    messages = SafeTextField(_("messages"), blank=True, default="")
    client_messages = SafeTextField(_("client messages"), blank=True, default="")
    bank_messages = SafeTextField(_("bank messages"), blank=True, default="")
    marked_reconciled = models.BooleanField(_("marked as reconciled"), db_index=True, default=False, blank=True)

    class Meta:
        verbose_name = _("statement record")
        verbose_name_plural = _("statement records")

    @property
    def messages_combined(self) -> str:
        """
        Returns: All statement record message fields separated with newlines.
        """
        out = ""
        if self.messages:
            out += self.messages + "\n"
        if self.bank_messages:
            out += self.bank_messages + "\n"
        if self.bank_messages:
            out += self.bank_messages + "\n"
        for detail in self.detail_set.all().order_by("id").distinct():
            assert isinstance(detail, StatementRecordDetail)
            if detail.unstructured_remittance_info:
                out += detail.unstructured_remittance_info + "\n"
        return out[:-1]

    @property
    def remittance_info_list(self) -> List[Tuple[str, Decimal, str]]:
        """
        Returns structured remittance info list.
        Returns: List of (remittance_info, amount, currency_code)
        """
        out: List[Tuple[str, Decimal, str]] = []
        if self.remittance_info:
            out.append((self.remittance_info, self.amount, self.statement.currency_code))  # type: ignore
        for detail in self.detail_set.all().order_by("id").distinct():
            assert isinstance(detail, StatementRecordDetail)
            for rem in detail.remittanceinfo_set.all().order_by("id"):
                assert isinstance(rem, StatementRecordRemittanceInfo)
                out.append((rem.reference, rem.amount, rem.currency_code))  # type: ignore
        return out

    @property
    def is_reconciled(self) -> bool:
        """True if entry is either marked reconciled or has SUM(children)==amount."""
        return self.marked_reconciled or sum_queryset(self.child_set) == self.amount  # type: ignore

    def clean(self):
        self.source_file = self.statement
        self.timestamp = datetime.combine(self.record_date, time(0, 0)).replace(tzinfo=timezone.utc)
        if self.name:
            self.description = "{name}: {record_description}".format(record_description=self.record_description, name=self.name)
        else:
            self.description = "{record_description}".format(record_description=self.record_description)


class CurrencyExchangeSource(models.Model):
    name = SafeCharField(_("name"), max_length=64)
    created = models.DateTimeField(_("created"), default=now, db_index=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("currency exchange source")
        verbose_name_plural = _("currency exchange sources")

    def __str__(self):
        return str(self.name)


class CurrencyExchange(models.Model):
    record_date = models.DateField(_("record date"), db_index=True)
    source_currency = SafeCharField(_("source currency"), max_length=3, blank=True)
    target_currency = SafeCharField(_("target currency"), max_length=3, blank=True)
    unit_currency = SafeCharField(_("unit currency"), max_length=3, blank=True)
    exchange_rate = models.DecimalField(_("exchange rate"), decimal_places=6, max_digits=12, null=True, default=None, blank=True)
    source = models.ForeignKey(
        CurrencyExchangeSource,
        verbose_name=_("currency exchange source"),
        blank=True,
        null=True,
        default=None,
        on_delete=models.PROTECT,
    )  # noqa

    class Meta:
        verbose_name = _("currency exchange")
        verbose_name_plural = _("currency exchanges")

    def __str__(self):
        return "{src} = {rate} {tgt}".format(src=self.source_currency, tgt=self.target_currency, rate=self.exchange_rate)


class StatementRecordDetail(models.Model):
    record = models.ForeignKey(StatementRecord, verbose_name=_("record"), related_name="detail_set", on_delete=models.CASCADE)
    batch_identifier = SafeCharField(_("batch message id"), max_length=64, db_index=True, blank=True, default="")
    amount = models.DecimalField(verbose_name=_("amount"), max_digits=10, decimal_places=2, blank=True, default=None, null=True, db_index=True)
    currency_code = SafeCharField(_("currency code"), max_length=3)
    instructed_amount = models.DecimalField(
        verbose_name=_("instructed amount"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        default=None,
        null=True,
        db_index=True,
    )
    exchange = models.ForeignKey(
        CurrencyExchange,
        verbose_name=_("currency exchange"),
        related_name="recorddetail_set",
        on_delete=models.PROTECT,
        null=True,
        default=None,
        blank=True,
    )
    archive_identifier = SafeCharField(_("archive identifier"), max_length=64, blank=True)
    end_to_end_identifier = SafeCharField(_("end-to-end identifier"), max_length=64, blank=True)
    creditor_name = SafeCharField(_("creditor name"), max_length=128, blank=True)
    creditor_account = SafeCharField(_("creditor account"), max_length=35, blank=True)
    creditor_account_scheme = SafeCharField(_("creditor account scheme"), max_length=8, blank=True)
    debtor_name = SafeCharField(_("debtor name"), max_length=128, blank=True)
    ultimate_debtor_name = SafeCharField(_("ultimate debtor name"), max_length=128, blank=True)
    unstructured_remittance_info = SafeCharField(_("unstructured remittance info"), max_length=2048, blank=True)
    paid_date = models.DateTimeField(_("paid date"), db_index=True, blank=True, null=True, default=None)

    class Meta:
        verbose_name = _("statement record details")
        verbose_name_plural = _("statement record details")


class StatementRecordRemittanceInfo(models.Model):
    detail = models.ForeignKey(StatementRecordDetail, related_name="remittanceinfo_set", on_delete=models.CASCADE)
    additional_info = SafeCharField(_("additional remittance info"), max_length=256, blank=True, db_index=True)
    amount = models.DecimalField(_("amount"), decimal_places=2, max_digits=10, null=True, default=None, blank=True)
    currency_code = SafeCharField(_("currency code"), max_length=3, blank=True)
    reference = SafeCharField(_("reference"), max_length=35, blank=True, db_index=True)

    def __str__(self):
        return "{} {} ref {} ({})".format(self.amount if self.amount is not None else "", self.currency_code, self.reference, self.additional_info)

    class Meta:
        verbose_name = _("statement record remittance info")
        verbose_name_plural = _("statement record remittance info")


class StatementRecordSepaInfo(models.Model):
    record = models.OneToOneField(StatementRecord, verbose_name=_("record"), related_name="sepa_info", on_delete=models.CASCADE)
    reference = SafeCharField(_("reference"), max_length=35, blank=True)
    iban_account_number = SafeCharField(_("IBAN"), max_length=35, blank=True)
    bic_code = SafeCharField(_("BIC"), max_length=35, blank=True)
    recipient_name_detail = SafeCharField(_("recipient name detail"), max_length=70, blank=True)
    payer_name_detail = SafeCharField(_("payer name detail"), max_length=70, blank=True)
    identifier = SafeCharField(_("identifier"), max_length=35, blank=True)
    archive_identifier = SafeCharField(_("archive identifier"), max_length=64, blank=True)

    class Meta:
        verbose_name = _("SEPA")
        verbose_name_plural = _("SEPA")

    def __str__(self):
        return "[{}]".format(self.id)


class ReferencePaymentBatchManager(models.Manager):
    def latest_record_date(self) -> Optional[datetime]:
        """
        Returns:
            datetime of latest record available or None
        """
        obj = self.order_by("-record_date").first()
        if not obj:
            return None
        return obj.record_date  # type: ignore


class ReferencePaymentBatch(AccountEntrySourceFile):
    objects = ReferencePaymentBatchManager()  # type: ignore
    file = models.ForeignKey("ReferencePaymentBatchFile", blank=True, default=None, null=True, on_delete=models.CASCADE)
    record_date = models.DateTimeField(_("record date"), db_index=True)
    identifier = SafeCharField(_("institution"), max_length=32, blank=True)
    institution_identifier = SafeCharField(_("institution"), max_length=2, blank=True, default="")
    service_identifier = SafeCharField(_("service"), max_length=9, blank=True, default="")
    currency_identifier = SafeCharField(_("currency"), max_length=3, blank=True, default="EUR")
    cached_total_amount = models.DecimalField(_("total amount"), max_digits=10, decimal_places=2, null=True, default=None, blank=True)

    class Meta:
        verbose_name = _("reference payment batch")
        verbose_name_plural = _("reference payment batches")

    def get_total_amount(self, force: bool = False) -> Decimal:
        if self.cached_total_amount is None or force:
            self.cached_total_amount = sum_queryset(ReferencePaymentRecord.objects.filter(batch=self))
            self.save(update_fields=["cached_total_amount"])
        return self.cached_total_amount

    @property
    def total_amount(self) -> Decimal:
        return self.get_total_amount()

    total_amount.fget.short_description = _("total amount")  # type: ignore


class ReferencePaymentRecord(AccountEntry):
    """Reference payment record. See jacc.Invoice for date/time variable naming conventions."""

    objects = PaymentRecordManager()  # type: ignore
    batch = models.ForeignKey(ReferencePaymentBatch, verbose_name=_("batch"), related_name="record_set", on_delete=models.CASCADE)
    line_number = models.SmallIntegerField(_("line number"), default=0, blank=True)
    record_type = SafeCharField(_("record type"), max_length=4, blank=True, default="")
    account_number = SafeCharField(_("account number"), max_length=32, db_index=True)
    record_date = models.DateField(_("record date"), db_index=True)
    paid_date = models.DateField(_("paid date"), db_index=True, blank=True, null=True, default=None)
    value_date = models.DateField(_("value date"), db_index=True, blank=True, null=True, default=None)
    archive_identifier = SafeCharField(_("archive identifier"), max_length=32, blank=True, default="", db_index=True)
    remittance_info = SafeCharField(_("remittance info"), max_length=256, db_index=True)
    payer_name = SafeCharField(_("payer name"), max_length=64, blank=True, default="", db_index=True)
    currency_identifier = SafeCharField(_("currency identifier"), max_length=1, choices=CURRENCY_IDENTIFIERS, blank=True, default="")
    name_source = SafeCharField(_("name source"), max_length=1, choices=NAME_SOURCES, blank=True, default="")
    correction_identifier = SafeCharField(_("correction identifier"), max_length=1, choices=CORRECTION_IDENTIFIER, default="")
    delivery_method = SafeCharField(_("delivery method"), max_length=1, db_index=True, choices=DELIVERY_METHOD, blank=True, default="")
    receipt_code = SafeCharField(_("receipt code"), max_length=1, choices=RECEIPT_CODE, db_index=True, blank=True, default="")
    marked_reconciled = models.BooleanField(_("marked as reconciled"), db_index=True, default=False, blank=True)
    instructed_amount = models.DecimalField(_("instructed amount"), blank=True, default=None, null=True, max_digits=10, decimal_places=2)
    instructed_currency = SafeCharField(_("instructed currency"), blank=True, default="", max_length=3)
    creditor_bank_bic = SafeCharField(_("creditor bank BIC"), max_length=16, blank=True, default="")
    end_to_end_identifier = SafeCharField(_("end to end identifier"), max_length=128, blank=True, default="")

    class Meta:
        verbose_name = _("reference payment records")
        verbose_name_plural = _("reference payment records")

    @property
    def is_reconciled(self) -> bool:
        """True if entry is either marked as reconciled or has SUM(children)==amount."""
        return self.marked_reconciled or sum_queryset(self.child_set) == self.amount  # type: ignore

    @property
    def remittance_info_short(self) -> str:
        """Remittance info without preceding zeroes.

        Returns:
            str
        """
        return re.sub(r"^0+", "", self.remittance_info)

    def clean(self):
        self.source_file = self.batch
        self.timestamp = datetime.combine(self.paid_date or self.record_date, time(0, 0)).replace(tzinfo=timezone.utc)
        self.description = "{amount} {remittance_info} {payer_name}".format(
            amount=self.amount, remittance_info=self.remittance_info, payer_name=self.payer_name
        )


class StatementFile(models.Model):
    created = models.DateTimeField(_("created"), default=now, db_index=True, blank=True, editable=False)
    file = models.FileField(verbose_name=_("file"), upload_to="uploads")
    original_filename = SafeCharField(_("original filename"), blank=True, default="", max_length=256)
    tag = SafeCharField(_("tag"), blank=True, max_length=64, default="", db_index=True)
    errors = SafeTextField(_("errors"), max_length=4086, default="", blank=True)

    class Meta:
        verbose_name = _("account statement file")
        verbose_name_plural = _("account statement files")

    @property
    def full_path(self):
        return join(settings.MEDIA_ROOT, self.file.name) if self.file else ""

    def __str__(self):
        return basename(str(self.file.name)) if self.file else ""


class ReferencePaymentBatchFile(models.Model):
    created = models.DateTimeField(_("created"), default=now, db_index=True, blank=True, editable=False)
    file = models.FileField(verbose_name=_("file"), upload_to="uploads")
    original_filename = SafeCharField(_("original filename"), blank=True, default="", max_length=256)
    tag = SafeCharField(_("tag"), blank=True, max_length=64, default="", db_index=True)
    timestamp = models.DateTimeField(_("timestamp"), default=None, null=True, db_index=True, blank=True, editable=False)
    msg_id = models.CharField(_("message identifier"), max_length=32, default="", blank=True, db_index=True)
    additional_info = models.CharField(_("additional information"), max_length=128, default="", blank=True)
    errors = SafeTextField(_("errors"), max_length=4086, default="", blank=True)
    cached_total_amount = models.DecimalField(_("total amount"), max_digits=10, decimal_places=2, null=True, default=None, blank=True)

    class Meta:
        verbose_name = _("reference payment batch file")
        verbose_name_plural = _("reference payment batch files")

    def clean(self):
        if self.timestamp is None:
            self.timestamp = self.created

    def get_total_amount(self, force: bool = False) -> Decimal:
        if self.cached_total_amount is None or force:
            self.cached_total_amount = sum_queryset(ReferencePaymentRecord.objects.filter(batch__file=self))
            self.save(update_fields=["cached_total_amount"])
        return self.cached_total_amount

    @property
    def total_amount(self) -> Decimal:
        return self.get_total_amount()

    total_amount.fget.short_description = _("total amount")  # type: ignore

    @property
    def full_path(self):
        return join(settings.MEDIA_ROOT, self.file.name) if self.file else ""

    def __str__(self):
        return basename(str(self.file.name)) if self.file else ""


class PayoutParty(models.Model):
    name = SafeCharField(_("name"), max_length=128, db_index=True)
    account_number = SafeCharField(_("account number"), max_length=35, db_index=True, validators=[iban_validator])
    bic = SafeCharField(_("BIC"), max_length=16, db_index=True, blank=True)
    org_id = SafeCharField(_("organization id"), max_length=32, db_index=True, blank=True, default="")
    address = SafeTextField(_("address"), blank=True, default="")
    country_code = SafeCharField(_("country code"), max_length=2, default="FI", blank=True, db_index=True)
    payouts_account = models.ForeignKey(Account, verbose_name=_("payouts account"), null=True, default=None, blank=True, on_delete=models.PROTECT)

    class Meta:
        verbose_name = _("payout party")
        verbose_name_plural = _("payout parties")

    def __str__(self):
        return "{} ({})".format(self.name, self.account_number)

    @property
    def is_payout_party_used(self) -> bool:
        """True if payout party has been used in any payment."""
        if not hasattr(self, "id") or self.id is None:
            return False
        return Payout.objects.all().filter(Q(recipient=self) | Q(payer=self)).exists()

    @property
    def is_account_number_changed(self) -> bool:
        """True if account number has been changed compared to the one stored in DB."""
        if not hasattr(self, "id") or self.id is None:
            return False
        return PayoutParty.objects.all().filter(id=self.id).exclude(account_number=self.account_number).exists()

    def clean(self):
        if not self.bic:
            self.bic = iban_bic(self.account_number)
        if self.is_account_number_changed and self.is_payout_party_used:
            raise ValidationError({"account_number": _("Account number changes of used payout parties is not allowed. Create a new payout party instead.")})

    @property
    def address_lines(self):
        out = []
        for line in self.address.split("\n"):
            line = line.strip()
            if line:
                out.append(line)
        return out


class Payout(AccountEntry):
    connection = models.ForeignKey(
        "WsEdiConnection",
        verbose_name=_("WS-EDI connection"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    payer = models.ForeignKey(PayoutParty, verbose_name=_("payer"), related_name="+", on_delete=models.PROTECT)
    recipient = models.ForeignKey(PayoutParty, verbose_name=_("recipient"), related_name="+", on_delete=models.PROTECT)
    messages = SafeTextField(_("recipient messages"), blank=True, default="")
    reference = SafeCharField(_("recipient reference"), blank=True, default="", max_length=32)
    msg_id = SafeCharField(_("message id"), max_length=64, blank=True, db_index=True, editable=False)
    file_name = SafeCharField(_("file name"), max_length=255, blank=True, db_index=True, editable=False)
    full_path = SafeTextField(_("full path"), blank=True, editable=False)
    file_reference = SafeCharField(_("file reference"), max_length=255, blank=True, db_index=True, editable=False)
    due_date = models.DateField(_("due date"), db_index=True, blank=True, null=True, default=None)
    paid_date = models.DateTimeField(_("paid date"), db_index=True, blank=True, null=True, default=None)
    state = SafeCharField(_("state"), max_length=1, blank=True, default=PAYOUT_WAITING_PROCESSING, choices=PAYOUT_STATE, db_index=True)

    class Meta:
        verbose_name = _("payout")
        verbose_name_plural = _("payouts")

    def clean(self):
        if self.parent and not self.amount:
            self.amount = self.parent.amount

        # prevent defining both reference and messages
        if self.messages and self.reference or not self.messages and not self.reference:
            raise ValidationError(_("payment.must.have.reference.or.messages"))

        # validate reference if any
        if self.reference:
            if self.reference[:2] == "RF":  # noqa
                iso_payment_reference_validator(self.reference)
            else:
                fi_payment_reference_validator(self.reference)

        # prevent canceling payouts which have been uploaded successfully
        if self.state == PAYOUT_CANCELED:
            if self.is_upload_done:
                group_status = self.group_status
                if group_status != "RJCT":
                    raise ValidationError(_("File already uploaded") + " ({})".format(group_status))

        # save paid time if marking payout as paid manually
        if self.state == PAYOUT_PAID and not self.paid_date:
            self.paid_date = now()
            status = self.payoutstatus_set.order_by("-created").first()
            if status:
                assert isinstance(status, PayoutStatus)
                self.paid_date = status.created

        # always require amount
        if self.amount is None or self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": _("value > 0 required")})

    def generate_msg_id(self, commit: bool = True):
        msg_id_base = re.sub(r"[^\d]", "", now().isoformat())[:-4]
        self.msg_id = msg_id_base + "P" + str(self.id)
        if commit:
            self.save(update_fields=["msg_id"])

    @property
    def state_name(self):
        return choices_label(PAYOUT_STATE, self.state)

    @property
    def is_upload_done(self):
        return PayoutStatus.objects.filter(payout=self, response_code="00").first() is not None

    @property
    def is_accepted(self):
        return self.has_group_status("ACCP")

    @property
    def is_rejected(self):
        return self.has_group_status("RJCT")

    def has_group_status(self, group_status: str) -> bool:
        return PayoutStatus.objects.filter(payout=self, group_status=group_status).exists()

    @property
    def group_status(self):
        status = PayoutStatus.objects.filter(payout=self).order_by("-timestamp", "-id").first()
        return status.group_status if status else ""

    group_status.fget.short_description = _("payment.group.status")  # type: ignore  # pytype: disable=attribute-error


class PayoutStatusManager(models.Manager):
    def is_file_processed(self, filename: str) -> bool:
        return self.filter(file_name=basename(filename)).first() is not None


class PayoutStatus(models.Model):
    objects = PayoutStatusManager()
    payout = models.ForeignKey(
        Payout,
        verbose_name=_("payout"),
        related_name="payoutstatus_set",
        on_delete=models.PROTECT,
        null=True,
        default=None,
        blank=True,
    )
    created = models.DateTimeField(_("created"), default=now, db_index=True, editable=False, blank=True)
    timestamp = models.DateTimeField(_("timestamp"), default=now, db_index=True, editable=False, blank=True)
    file_name = SafeCharField(_("file name"), max_length=128, blank=True, db_index=True, editable=False)
    file_path = SafeCharField(_("file path"), max_length=255, blank=True, db_index=True, editable=False)
    response_code = SafeCharField(_("response code"), max_length=4, blank=True, db_index=True)
    response_text = SafeCharField(_("response text"), max_length=128, blank=True)
    msg_id = SafeCharField(_("message id"), max_length=64, blank=True, db_index=True)
    original_msg_id = SafeCharField(_("original message id"), blank=True, max_length=64, db_index=True)
    group_status = SafeCharField(_("group status"), max_length=8, blank=True, db_index=True)
    status_reason = SafeCharField(_("status reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("payout status")
        verbose_name_plural = _("payout statuses")

    def __str__(self):
        return str(self.group_status)

    @property
    def full_path(self) -> str:
        return get_media_full_path(self.file_path) if self.file_path else ""

    @property
    def is_accepted(self):
        return self.group_status == "ACCP"

    @property
    def is_rejected(self):
        return self.group_status == "RJCT"


class Refund(Payout):
    class Meta:
        verbose_name = _("incoming.payment.refund")
        verbose_name_plural = _("incoming.payment.refunds")

    attachment = models.FileField(verbose_name=_("attachment"), blank=True, upload_to="uploads")


class WsEdiSoapCall(models.Model):
    connection = models.ForeignKey("WsEdiConnection", verbose_name=_("WS-EDI connection"), on_delete=models.CASCADE)
    command = SafeCharField(_("command"), max_length=64, blank=True, db_index=True)
    created = models.DateTimeField(_("created"), default=now, db_index=True, editable=False, blank=True)
    executed = models.DateTimeField(_("executed"), default=None, null=True, db_index=True, editable=False, blank=True)
    error = SafeTextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("WS-EDI SOAP call")
        verbose_name_plural = _("WS-EDI SOAP calls")

    def __str__(self):
        return "WsEdiSoapCall({})".format(self.id)

    @property
    def timestamp(self) -> datetime:
        return self.created.astimezone(ZoneInfo("Europe/Helsinki"))

    @property
    def timestamp_digits(self) -> str:
        v = re.sub(r"[^\d]", "", self.created.isoformat())
        return v[:17]

    @property
    def request_identifier(self) -> str:
        return str(self.id)

    @property
    def command_camelcase(self) -> str:
        return self.command[0:1].lower() + self.command[1:]  # noqa

    def debug_get_filename(self, file_type: str) -> str:
        return "{:08}{}.xml".format(self.id, file_type)

    @property
    def debug_request_full_path(self) -> str:
        return self.debug_get_file_path(self.debug_get_filename("q"))

    @property
    def debug_response_full_path(self) -> str:
        return self.debug_get_file_path(self.debug_get_filename("s"))

    @staticmethod
    def debug_get_file_path(filename: str) -> str:
        return os.path.join(settings.WSEDI_LOG_PATH, filename) if hasattr(settings, "WSEDI_LOG_PATH") and settings.WSEDI_LOG_PATH else ""


class WsEdiConnectionManager(models.Manager):
    def get_by_receiver_identifier(self, receiver_identifier: str):
        objs = list(self.filter(receiver_identifier=receiver_identifier))
        if len(objs) != 1:
            raise ValidationError(
                _("WS-EDI connection cannot be found by receiver identifier {receiver_identifier} since there are {matches} matches").format(
                    receiver_identifier=receiver_identifier, matches=len(objs)
                )
            )
        return objs[0]


class WsEdiConnection(models.Model):
    objects = WsEdiConnectionManager()
    name = SafeCharField(_("name"), max_length=64)
    enabled = models.BooleanField(_("enabled"), blank=True, default=True)
    sender_identifier = SafeCharField(_("sender identifier"), max_length=32)
    receiver_identifier = SafeCharField(_("receiver identifier"), max_length=32)
    target_identifier = SafeCharField(_("target identifier"), max_length=32)
    signer_identifier = SafeCharField(_("signer identifier"), max_length=32, blank=True, default="")
    agreement_identifier = SafeCharField(_("agreement identifier"), max_length=32, blank=True, default="")
    environment = SafeCharField(_("environment"), max_length=32, default="PRODUCTION")
    pin = SafeCharField("PIN", max_length=64, default="", blank=True)
    pki_endpoint = models.URLField(_("PKI endpoint"), blank=True, default="")
    bank_root_cert_file = models.FileField(verbose_name=_("bank root certificate file"), blank=True, upload_to="certs")
    soap_endpoint = models.URLField(_("EDI endpoint"))
    signing_cert_file = models.FileField(verbose_name=_("signing certificate file"), blank=True, upload_to="certs")
    signing_key_file = models.FileField(verbose_name=_("signing key file"), blank=True, upload_to="certs")
    encryption_cert_file = models.FileField(verbose_name=_("encryption certificate file"), blank=True, upload_to="certs")
    encryption_key_file = models.FileField(verbose_name=_("encryption key file"), blank=True, upload_to="certs")
    old_signing_key_file = models.FileField(verbose_name=_("old signing key file"), blank=True, upload_to="certs", editable=False)
    old_encryption_key_file = models.FileField(verbose_name=_("old encryption key file"), blank=True, upload_to="certs", editable=False)
    bank_encryption_cert_file = models.FileField(verbose_name=_("bank encryption cert file"), blank=True, upload_to="certs")
    bank_signing_cert_file = models.FileField(verbose_name=_("bank signing cert file"), blank=True, upload_to="certs")
    use_sha256 = models.BooleanField(_("SHA-256"), blank=True, default=False)
    use_wsse_timestamp = models.BooleanField(_("Use WS-Security timestamp"), blank=True, default=False)
    ca_cert_file = models.FileField(verbose_name=_("CA certificate file"), blank=True, upload_to="certs")
    debug_commands = SafeTextField(_("debug commands"), blank=True, help_text=_("wsedi.connection.debug.commands.help.text"))
    created = models.DateTimeField(_("created"), default=now, db_index=True, editable=False, blank=True)
    _signing_cert = None
    _valid_until: Optional[datetime] = None

    class Meta:
        verbose_name = _("WS-EDI connection")
        verbose_name_plural = _("WS-EDI connections")

    def __str__(self):
        return "{} / {}".format(self.name, self.receiver_identifier)

    @property
    def is_test(self) -> bool:
        return str(self.environment).lower() in ["customertest", "test"]

    @property
    def signing_cert_full_path(self) -> str:
        return get_media_full_path(self.signing_cert_file.file.name) if self.signing_cert_file else ""

    @property
    def signing_key_full_path(self) -> str:
        return get_media_full_path(self.signing_key_file.file.name) if self.signing_key_file else ""

    @property
    def encryption_cert_full_path(self) -> str:
        return get_media_full_path(self.encryption_cert_file.file.name) if self.encryption_cert_file else ""

    @property
    def encryption_key_full_path(self) -> str:
        return get_media_full_path(self.encryption_key_file.file.name) if self.encryption_key_file else ""

    @property
    def bank_encryption_cert_full_path(self) -> str:
        return get_media_full_path(self.bank_encryption_cert_file.file.name) if self.bank_encryption_cert_file else ""

    @property
    def bank_root_cert_full_path(self) -> str:
        return get_media_full_path(self.bank_root_cert_file.file.name) if self.bank_root_cert_file else ""

    @property
    def ca_cert_full_path(self) -> str:
        return get_media_full_path(self.ca_cert_file.file.name) if self.ca_cert_file else ""

    @property
    def signing_cert_with_public_key_full_path(self) -> str:
        src_file = self.signing_cert_full_path
        file = src_file[:-4] + "-with-pubkey.pem"
        if not os.path.isfile(file):
            cmd = [
                settings.OPENSSL_PATH,
                "x509",
                "-pubkey",
                "-in",
                src_file,
            ]
            logger.info(" ".join(cmd))
            out = subprocess.check_output(cmd)
            with open(file, "wb") as fp:
                fp.write(out)
        return file

    @property
    def bank_encryption_cert_with_public_key_full_path(self) -> str:
        src_file = self.bank_encryption_cert_full_path
        file = src_file[:-4] + "-with-pubkey.pem"
        if not os.path.isfile(file):
            cmd = [
                settings.OPENSSL_PATH,
                "x509",
                "-pubkey",
                "-in",
                src_file,
            ]
            # logger.info(' '.join(cmd))
            out = subprocess.check_output(cmd)
            with open(file, "wb") as fp:
                fp.write(out)
        return file

    @property
    def signing_cert(self):
        if hasattr(self, "_signing_cert") and self._signing_cert:
            return self._signing_cert
        self._signing_cert = get_x509_cert_from_file(self.signing_cert_full_path)
        return self._signing_cert

    def get_pki_template(self, template_name: str, soap_call: WsEdiSoapCall, **kwargs) -> bytes:
        return format_xml(
            get_template(template_name).render(
                {
                    "ws": soap_call.connection,
                    "soap_call": soap_call,
                    "command": soap_call.command,
                    "timestamp": now().astimezone(ZoneInfo("Europe/Helsinki")).isoformat(),
                    **kwargs,
                }
            )
        ).encode()

    def get_application_request(self, command: str, **kwargs) -> bytes:
        opt_sha256 = "-sha256" if self.use_sha256 else ""
        return format_xml(
            get_template(f"jbank/application_request_template{opt_sha256}.xml").render(
                {
                    "ws": self,
                    "command": command,
                    "timestamp": now().astimezone(ZoneInfo("Europe/Helsinki")).isoformat(),
                    **kwargs,
                }
            )
        ).encode()

    @classmethod
    def verify_signature(cls, content: bytes, signing_key_full_path: str):
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            cmd = [settings.XMLSEC1_PATH, "--verify", "--pubkey-pem", signing_key_full_path, fp.name]
            # logger.info(' '.join(cmd))
            subprocess.check_output(cmd)

    def sign_pki_request(self, content: bytes, signing_key_full_path: str, signing_cert_full_path: str) -> bytes:
        return self._sign_request(content, signing_key_full_path, signing_cert_full_path)

    def sign_application_request(self, content: bytes) -> bytes:
        return self._sign_request(content, self.signing_key_full_path, self.signing_cert_full_path)

    def _sign_request(self, content: bytes, signing_key_full_path: str, signing_cert_full_path: str) -> bytes:
        """Sign a request.
        See https://users.dcc.uchile.cl/~pcamacho/tutorial/web/xmlsec/xmlsec.html

        Args:
            content: XML application request
            signing_key_full_path: Override signing key full path (if not use self.signing_key_full_path)
            signing_cert_full_path: Override signing key full path (if not use self.signing_cert_full_path)

        Returns:
            str
        """
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            if self.use_sha256:
                cmd = [self._xmlsec1_example_bin("sign3-sha256"), fp.name, signing_key_full_path, signing_cert_full_path]
            else:
                cmd = [
                    settings.XMLSEC1_PATH,
                    "--sign",
                    "--privkey-pem",
                    "{},{}".format(signing_key_full_path, signing_cert_full_path),
                    fp.name,
                ]
            # logger.info(' '.join(cmd))
            out = subprocess.check_output(cmd)
        self.verify_signature(out, signing_key_full_path)
        return out

    def encrypt_pki_request(self, content: bytes) -> bytes:
        return self._encrypt_request(content)

    def encrypt_application_request(self, content: bytes) -> bytes:
        return self._encrypt_request(content)

    def _encrypt_request(self, content: bytes) -> bytes:
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            cmd = [
                self._xmlsec1_example_bin("encrypt3"),
                fp.name,
                self.bank_encryption_cert_with_public_key_full_path,
                self.bank_encryption_cert_full_path,
            ]
            # logger.info(' '.join(cmd))
            out = subprocess.check_output(cmd)
        return out

    def encode_application_request(self, content: bytes) -> bytes:
        lines = content.split(b"\n")
        if lines and lines[0].startswith(b"<?xml"):
            lines = lines[1:]
        content_without_xml_tag = b"\n".join(lines)
        return base64.b64encode(content_without_xml_tag)

    def decode_application_response(self, content: bytes) -> bytes:
        return base64.b64decode(content)

    def decrypt_application_response(self, content: bytes) -> bytes:
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            cmd = [
                self._xmlsec1_example_bin("decrypt3"),
                fp.name,
                self.encryption_key_full_path,
            ]
            # logger.info(' '.join(cmd))
            out = subprocess.check_output(cmd)
        return out

    @property
    def debug_command_list(self) -> List[str]:
        return [x for x in re.sub(r"[^\w]+", " ", self.debug_commands).strip().split(" ") if x]

    @property
    def valid_until(self) -> Optional[datetime]:
        """
        Returns: The closest not-valid-after date found from certificates.
        """
        if self._valid_until is not None:
            return self._valid_until
        min_not_valid_after: Optional[datetime] = None
        try:
            certs = [
                self.signing_cert_full_path,
                self.encryption_cert_full_path,
                self.bank_encryption_cert_full_path,
                self.bank_root_cert_full_path,
                self.ca_cert_full_path,
            ]
        except Exception as exc:
            logger.warning("Missing certificate files: %s", exc)
            return None
        for filename in certs:
            if filename and os.path.isfile(filename):
                cert = get_x509_cert_from_file(filename)
                not_valid_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
                if min_not_valid_after is None or not_valid_after < min_not_valid_after:
                    min_not_valid_after = not_valid_after
        self._valid_until = min_not_valid_after
        return min_not_valid_after

    @staticmethod
    def _xmlsec1_example_bin(file: str) -> str:
        if hasattr(settings, "XMLSEC1_EXAMPLES_PATH") and settings.XMLSEC1_EXAMPLES_PATH:
            xmlsec1_examples_path = settings.XMLSEC1_EXAMPLES_PATH
        else:
            xmlsec1_examples_path = os.path.join(str(os.getenv("HOME") or ""), "bin/xmlsec1-examples")
        return str(os.path.join(xmlsec1_examples_path, file))


class EuriborRateManager(models.Manager):
    def save_unique(self, record_date: date, name: str, rate: Decimal):
        return self.get_or_create(record_date=record_date, name=name, defaults={"rate": rate})[0]


class EuriborRate(models.Model):
    objects = EuriborRateManager()
    record_date = models.DateField(_("record date"), db_index=True)
    name = SafeCharField(_("interest rate name"), db_index=True, max_length=64)
    rate = models.DecimalField(_("interest rate %"), max_digits=10, decimal_places=4, db_index=True)
    created = models.DateTimeField(_("created"), default=now, db_index=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("euribor rate")
        verbose_name_plural = _("euribor rates")


class AccountBalance(models.Model):
    account_number = models.CharField(_("account number"), max_length=32, db_index=True)
    bic = models.CharField("BIC", max_length=16, db_index=True)
    record_datetime = models.DateTimeField(_("record date"), db_index=True)
    balance = models.DecimalField(_("balance"), max_digits=10, decimal_places=2)
    available_balance = models.DecimalField(_("available balance"), max_digits=10, decimal_places=2)
    credit_limit = models.DecimalField(_("credit limit"), max_digits=10, decimal_places=2, null=True, default=None, blank=True)
    currency = models.CharField(_("currency"), max_length=3, default="EUR", db_index=True)
    created = models.DateTimeField(_("created"), default=now, db_index=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("account balance")
        verbose_name_plural = _("account balances")
