import base64
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, time
from os.path import basename, join
from pathlib import Path
from typing import List
import cryptography
import pytz
from cryptography import x509
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template.loader import get_template
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jacc.models import AccountEntry, AccountEntrySourceFile, Account
from jutil.dict import choices_label
from jutil.format import format_xml
from jutil.validators import iban_validator, iban_bic, iso_payment_reference_validator, fi_payment_reference_validator


logger = logging.getLogger(__name__)


JBANK_BIN_PATH = Path(__file__).absolute().parent.joinpath('bin')

RECORD_ENTRY_TYPE = (
    ('1', _('Deposit')),
    ('2', _('Withdrawal')),
    ('3', _('Deposit Correction')),
    ('4', _('Withdrawal Correction')),
)

RECORD_CODES = (
    ('700', _('Money Transfer (In/Out)')),
    ('701', _('Recurring Payment (In/Out)')),
    ('702', _('Bill Payment (Out)')),
    ('703', _('Payment Terminal Deposit (In)')),
    ('704', _('Bank Draft (In/Out)')),
    ('705', _('Reference Payments (In)')),
    ('706', _('Payment Service (Out)')),
    ('710', _('Deposit (In)')),
    ('720', _('Withdrawal (Out)')),
    ('721', _('Card Payment (Out)')),
    ('722', _('Check (Out)')),
    ('730', _('Bank Fees (Out)')),
    ('740', _('Interests Charged (Out)')),
    ('750', _('Interests Credited (In)')),
    ('760', _('Loan (Out)')),
    ('761', _('Loan Payment (Out)')),
    ('770', _('Foreign Transfer (In/Out)')),
    ('780', _('Zero Balancing (In/Out)')),
    ('781', _('Sweeping (In/Out)')),
    ('782', _('Topping (In/Out)')),
)

RECORD_DOMAIN = (
    ('PMNT', _('Money Transfer (In/Out)')),
    ('LDAS', _('Loan Payment (Out)')),
    ('CAMT', _('Cash Management')),
    ('ACMT', _('Account Management')),
    ('XTND', _('Entended Domain')),
    ('SECU', _('Securities')),
    ('FORX', _('Foreign Exchange')),
    ('XTND', _('Entended Domain')),
    ('NTAV', _('Not Available'))
)

RECEIPT_CODE = (
    ('', ''),
    ('E', _('Separate')),
    ('P', _('Separate/Paper')),
)

CURRENCY_IDENTIFIERS = (
    ('1', 'EUR'),
)

NAME_SOURCES = (
    ('', _('Not Set')),
    ('A', _('From Customer')),
    ('K', _('From Bank Clerk')),
    ('J', _('From Bank System')),
)

CORRECTION_IDENTIFIER = (
    ('0', _('Regular Entry')),
    ('1', _('Correction Entry')),
)

DELIVERY_FROM_CUSTOMER = 'A'
DELIVERY_FROM_BANK_CLERK = 'K'
DELIVERY_FROM_BANK_SYSTEM = 'J'

DELIVERY_METHOD = (
    (DELIVERY_FROM_CUSTOMER, _('From Customer')),
    (DELIVERY_FROM_BANK_CLERK, _('From Bank Clerk')),
    (DELIVERY_FROM_BANK_SYSTEM, _('From Bank System')),
)

PAYOUT_WAITING_PROCESSING = 'W'
PAYOUT_WAITING_UPLOAD = 'U'
PAYOUT_UPLOADED = 'D'
PAYOUT_PAID = 'P'
PAYOUT_CANCELED = 'C'
PAYOUT_ERROR = 'E'

PAYOUT_STATE = (
    (PAYOUT_WAITING_PROCESSING, _('waiting processing')),
    (PAYOUT_WAITING_UPLOAD, _('waiting upload')),
    (PAYOUT_UPLOADED, _('uploaded')),
    (PAYOUT_PAID, _('paid')),
    (PAYOUT_CANCELED, _('canceled')),
    (PAYOUT_ERROR, _('error')),
)


class Statement(AccountEntrySourceFile):
    file = models.ForeignKey('StatementFile', blank=True, default=None, null=True, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, related_name='+', on_delete=models.PROTECT)
    account_number = models.CharField(_('account number'), max_length=32, db_index=True)
    statement_identifier = models.CharField(_('statement identifier'), max_length=48, db_index=True, blank=True, default='')
    statement_number = models.SmallIntegerField(_('statement number'), db_index=True)
    begin_date = models.DateField(_('begin date'), db_index=True)
    end_date = models.DateField(_('end date'), db_index=True)
    record_date = models.DateTimeField(_('record date'), db_index=True)
    customer_identifier = models.CharField(_('customer identifier'), max_length=64, blank=True, default='')
    begin_balance_date = models.DateField(_('begin balance date'), )
    begin_balance = models.DecimalField(_('begin balance'), max_digits=10, decimal_places=2)
    record_count = models.IntegerField(_('record count'), null=True, default=None)
    currency_code = models.CharField(_('currency code'), max_length=3)
    account_name = models.CharField(_('account name'), max_length=32, blank=True, default='')
    account_limit = models.DecimalField(_('account limit'), max_digits=10, decimal_places=2, blank=True, default=None, null=True)
    owner_name = models.CharField(_('owner name'), max_length=64)
    contact_info_1 = models.CharField(_('contact info (1)'), max_length=64, blank=True, default='')
    contact_info_2 = models.CharField(_('contact info (2)'), max_length=64, blank=True, default='')
    bank_specific_info_1 = models.CharField(_('bank specific info (1)'), max_length=1024, blank=True, default='')
    iban = models.CharField(_('IBAN'), max_length=32, db_index=True)
    bic = models.CharField(_('BIC'), max_length=8, db_index=True)

    class Meta:
        verbose_name = _('statement')
        verbose_name_plural = _('statements')


class PaymentRecordManager(models.Manager):
    def filter_matched(self):
        return self.exclude(child_set=None)

    def filter_unmatched(self):
        return self.filter(child_set=None)


class StatementRecord(AccountEntry):
    objects = PaymentRecordManager()
    statement = models.ForeignKey(Statement, verbose_name=_('statement'), related_name='record_set', on_delete=models.CASCADE)
    line_number = models.SmallIntegerField(_('line number'), default=None, null=True, blank=True)
    record_number = models.IntegerField(_('record number'), default=None, null=True, blank=True)
    archive_identifier = models.CharField(_('archive identifier'), max_length=64, blank=True, default='', db_index=True)
    record_date = models.DateField(_('record date'), db_index=True)
    value_date = models.DateField(_('value date'), db_index=True, blank=True, null=True, default=None)
    paid_date = models.DateField(_('paid date'), db_index=True, blank=True, null=True, default=None)
    entry_type = models.CharField(_('entry type'), max_length=1, choices=RECORD_ENTRY_TYPE, db_index=True)
    record_code = models.CharField(_('record type'), max_length=4, choices=RECORD_CODES, db_index=True, blank=True)
    record_domain = models.CharField(_('record domain'), max_length=4, choices=RECORD_DOMAIN, db_index=True, blank=True)
    family_code = models.CharField(_('family code'), max_length=4, db_index=True, blank=True, default='')
    sub_family_code = models.CharField(_('sub family code'), max_length=4, db_index=True, blank=True, default='')
    record_description = models.CharField(_('record description'), max_length=128, blank=True, default='')
    receipt_code = models.CharField(_('receipt code'), max_length=1, choices=RECEIPT_CODE, db_index=True, blank=True)
    delivery_method = models.CharField(_('delivery method'), max_length=1, db_index=True, choices=DELIVERY_METHOD)
    name = models.CharField(_('name'), max_length=64, blank=True, db_index=True)
    name_source = models.CharField(_('name source'), max_length=1, blank=True, choices=NAME_SOURCES)
    recipient_account_number = models.CharField(_('recipient account number'), max_length=32, blank=True, db_index=True)
    recipient_account_number_changed = models.CharField(_('recipient account number changed'), max_length=1, blank=True)
    remittance_info = models.CharField(_('remittance info'), max_length=35, db_index=True, blank=True)
    messages = models.TextField(_('messages'), blank=True, default='')
    client_messages = models.TextField(_('client messages'), blank=True, default='')
    bank_messages = models.TextField(_('bank messages'), blank=True, default='')
    manually_settled = models.BooleanField(_('manually settled'), db_index=True, default=False, blank=True)

    class Meta:
        verbose_name = _('statement record')
        verbose_name_plural = _('statement records')

    def clean(self):
        self.source_file = self.statement
        self.timestamp = pytz.utc.localize(datetime.combine(self.record_date, time(0, 0)))
        if self.name:
            self.description = '{name}: {record_description}'.format(record_description=self.record_description, name=self.name)
        else:
            self.description = '{record_description}'.format(record_description=self.record_description)


class CurrencyExchangeSource(models.Model):
    name = models.CharField(_('name'), max_length=64)
    created = models.DateTimeField(_('created'), default=now, db_index=True, blank=True, editable=False)

    class Meta:
        verbose_name = _('currency exchange source')
        verbose_name_plural = _('currency exchange sources')

    def __str__(self):
        return self.name


class CurrencyExchange(models.Model):
    record_date = models.DateField(_('record date'), db_index=True)
    source_currency = models.CharField(_('source currency'), max_length=3, blank=True)
    target_currency = models.CharField(_('target currency'), max_length=3, blank=True)
    unit_currency = models.CharField(_('unit currency'), max_length=3, blank=True)
    exchange_rate = models.DecimalField(_('exchange rate'), decimal_places=6, max_digits=12, null=True, default=None, blank=True)
    source = models.ForeignKey(CurrencyExchangeSource, verbose_name=_('currency exchange source'), blank=True, null=True, default=None, on_delete=models.PROTECT)  # noqa

    class Meta:
        verbose_name = _('currency exchange')
        verbose_name_plural = _('currency exchanges')

    def __str__(self):
        return '{src} = {rate} {tgt}'.format(src=self.source_currency, tgt=self.target_currency, rate=self.exchange_rate)


class StatementRecordDetail(models.Model):
    record = models.ForeignKey(StatementRecord, verbose_name=_('record'), related_name='detail_set', on_delete=models.CASCADE)
    batch_identifier = models.CharField(_('batch message id'), max_length=64, db_index=True, blank=True, default='')
    amount = models.DecimalField(verbose_name=_('amount'), max_digits=10, decimal_places=2, blank=True, default=None, null=True, db_index=True)
    currency_code = models.CharField(_('currency code'), max_length=3)
    instructed_amount = models.DecimalField(verbose_name=_('instructed amount'), max_digits=10, decimal_places=2, blank=True, default=None, null=True, db_index=True)    # noqa
    exchange = models.ForeignKey(CurrencyExchange, verbose_name=_('currency exchange'), related_name='recorddetail_set', on_delete=models.PROTECT, null=True, default=None, blank=True)  # noqa
    archive_identifier = models.CharField(_('archive identifier'), max_length=64, blank=True)
    end_to_end_identifier = models.CharField(_('end-to-end identifier'), max_length=64, blank=True)
    creditor_name = models.CharField(_('creditor name'), max_length=128, blank=True)
    creditor_account = models.CharField(_('creditor account'), max_length=35, blank=True)
    debtor_name = models.CharField(_('debtor name'), max_length=128, blank=True)
    ultimate_debtor_name = models.CharField(_('ultimate debtor name'), max_length=128, blank=True)
    unstructured_remittance_info = models.CharField(_('unstructured remittance info'), max_length=2048, blank=True)
    paid_date = models.DateTimeField(_('paid date'), db_index=True, blank=True, null=True, default=None)


class StatementRecordRemittanceInfo(models.Model):
    detail = models.ForeignKey(StatementRecordDetail, related_name='remittanceinfo_set', on_delete=models.CASCADE)
    additional_info = models.CharField(_('additional remittance info'), max_length=256, blank=True, db_index=True)
    amount = models.DecimalField(_('amount'), decimal_places=2, max_digits=10, null=True, default=None, blank=True)
    currency_code = models.CharField(_('currency code'), max_length=3, blank=True)
    reference = models.CharField(_('reference'), max_length=35, blank=True, db_index=True)

    def __str__(self):
        return '{} {} ref {} ({})'.format(self.amount if self.amount is not None else '', self.currency_code, self.reference, self.additional_info)


class StatementRecordSepaInfo(models.Model):
    record = models.OneToOneField(StatementRecord, verbose_name=_('record'), related_name='sepa_info', on_delete=models.CASCADE)
    reference = models.CharField(_('reference'), max_length=35, blank=True)
    iban_account_number = models.CharField(_('IBAN'), max_length=35, blank=True)
    bic_code = models.CharField(_('BIC'), max_length=35, blank=True)
    recipient_name_detail = models.CharField(_('recipient name detail'), max_length=70, blank=True)
    payer_name_detail = models.CharField(_('payer name detail'), max_length=70, blank=True)
    identifier = models.CharField(_('identifier'), max_length=35, blank=True)
    archive_identifier = models.CharField(_('archive identifier'), max_length=64, blank=True)

    class Meta:
        verbose_name = _('SEPA')
        verbose_name_plural = _('SEPA')

    def __str__(self):
        return '[{}]'.format(self.id)


class ReferencePaymentBatchManager(models.Manager):
    def latest_record_date(self) -> datetime:
        """
        :return: datetime of latest record available or None
        """
        obj = self.order_by('-record_date').first()
        if not obj:
            return None
        return obj.record_date


class ReferencePaymentBatch(AccountEntrySourceFile):
    objects = ReferencePaymentBatchManager()
    file = models.ForeignKey('ReferencePaymentBatchFile', blank=True, default=None, null=True, on_delete=models.CASCADE)
    record_date = models.DateTimeField(_('record date'), db_index=True)
    institution_identifier = models.CharField(_('institution identifier'), max_length=2, blank=True)
    service_identifier = models.CharField(_('service identifier'), max_length=9, blank=True)
    currency_identifier = models.CharField(_('currency identifier'), max_length=3, choices=CURRENCY_IDENTIFIERS)

    class Meta:
        verbose_name = _('reference payment batch')
        verbose_name_plural = _('reference payment batches')


class ReferencePaymentRecord(AccountEntry):
    """
    Reference payment record. See jacc.Invoice for date/time variable naming conventions.
    """
    objects = PaymentRecordManager()
    batch = models.ForeignKey(ReferencePaymentBatch, verbose_name=_('batch'), related_name='record_set', on_delete=models.CASCADE)
    line_number = models.SmallIntegerField(_('line number'), default=0, blank=True)
    record_type = models.CharField(_('record type'), max_length=1)
    account_number = models.CharField(_('account number'), max_length=32, db_index=True)
    record_date = models.DateField(_('record date'), db_index=True)
    paid_date = models.DateField(_('paid date'), db_index=True, blank=True, null=True, default=None)
    archive_identifier = models.CharField(_('archive identifier'), max_length=32, blank=True, default='', db_index=True)
    remittance_info = models.CharField(_('remittance info'), max_length=32, db_index=True)
    payer_name = models.CharField(_('payer name'), max_length=12, db_index=True)
    currency_identifier = models.CharField(_('currency identifier'), max_length=1, choices=CURRENCY_IDENTIFIERS)
    name_source = models.CharField(_('name source'), max_length=1, choices=NAME_SOURCES, blank=True)
    correction_identifier = models.CharField(_('correction identifier'), max_length=1, choices=CORRECTION_IDENTIFIER)
    delivery_method = models.CharField(_('delivery method'), max_length=1, db_index=True, choices=DELIVERY_METHOD)
    receipt_code = models.CharField(_('receipt code'), max_length=1, choices=RECEIPT_CODE, db_index=True, blank=True)
    manually_settled = models.BooleanField(_('manually settled'), db_index=True, default=False, blank=True)

    class Meta:
        verbose_name = _('reference payment records')
        verbose_name_plural = _('reference payment records')

    @property
    def remittance_info_short(self) -> str:
        """
        Remittance info without preceding zeroes.
        :return: str
        """
        return re.sub(r'^0+', '', self.remittance_info)

    def clean(self):
        self.source_file = self.batch
        self.timestamp = pytz.utc.localize(datetime.combine(self.paid_date, time(0, 0)))
        self.description = '{amount} {remittance_info} {payer_name}'.format(amount=self.amount, remittance_info=self.remittance_info,
                                                                            payer_name=self.payer_name)


class StatementFile(models.Model):
    created = models.DateTimeField(_('created'), default=now, db_index=True, blank=True, editable=False)
    file = models.FileField(_('file'), upload_to='uploads')
    errors = models.TextField(_('errors'), max_length=4086, default='', blank=True)

    class Meta:
        verbose_name = _('account statement file')
        verbose_name_plural = _('account statement files')

    @property
    def full_path(self):
        return join(settings.MEDIA_ROOT, self.file.name) if self.file else ''

    def __str__(self):
        return basename(str(self.file.name)) if self.file else ''


class ReferencePaymentBatchFile(models.Model):
    created = models.DateTimeField(_('created'), default=now, db_index=True, blank=True, editable=False)
    file = models.FileField(_('file'), upload_to='uploads')
    errors = models.TextField(_('errors'), max_length=4086, default='', blank=True)

    class Meta:
        verbose_name = _("reference payment batch file")
        verbose_name_plural = _("reference payment batch files")

    @property
    def full_path(self):
        return join(settings.MEDIA_ROOT, self.file.name) if self.file else ''

    def __str__(self):
        return basename(str(self.file.name)) if self.file else ''


class PayoutParty(models.Model):
    name = models.CharField(_('name'), max_length=128, db_index=True)
    account_number = models.CharField(_('account number'), max_length=35, db_index=True, validators=[iban_validator])
    bic = models.CharField(_('BIC'), max_length=16, db_index=True, blank=True)
    org_id = models.CharField(_('organization id'), max_length=32, db_index=True, blank=True, default='')
    address = models.TextField(_('address'), blank=True, default='')
    country_code = models.CharField(_('country code'), max_length=2, default='FI', blank=True, db_index=True)
    payouts_account = models.ForeignKey(Account, verbose_name=_('payouts account'), null=True, default=None, blank=True, on_delete=models.PROTECT)

    class Meta:
        verbose_name = _("payout party")
        verbose_name_plural = _("payout parties")

    def __str__(self):
        return '{} ({})'.format(self.name, self.account_number)

    def clean(self):
        if not self.bic:
            self.bic = iban_bic(self.account_number)

    @property
    def address_lines(self):
        out = []
        for line in self.address.split('\n'):
            line = line.strip()
            if line:
                out.append(line)
        return out


class Payout(AccountEntry):
    connection = models.ForeignKey('WsEdiConnection', verbose_name=_('WS-EDI connection'), on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    payer = models.ForeignKey(PayoutParty, verbose_name=_('payer'), related_name='+', on_delete=models.PROTECT)
    recipient = models.ForeignKey(PayoutParty, verbose_name=_('recipient'), related_name='+', on_delete=models.PROTECT)
    messages = models.TextField(_('recipient messages'), blank=True, default='')
    reference = models.CharField(_('recipient reference'), blank=True, default='', max_length=32)
    msg_id = models.CharField(_('message id'), max_length=64, blank=True, db_index=True, editable=False)
    file_name = models.CharField(_('file name'), max_length=255, blank=True, db_index=True, editable=False)
    full_path = models.TextField(_('full path'), blank=True, editable=False)
    file_reference = models.CharField(_('file reference'), max_length=255, blank=True, db_index=True, editable=False)
    due_date = models.DateField(_('due date'), db_index=True, blank=True, null=True, default=None)
    paid_date = models.DateTimeField(_('paid date'), db_index=True, blank=True, null=True, default=None)
    state = models.CharField(_('state'), max_length=1, blank=True, default=PAYOUT_WAITING_PROCESSING, choices=PAYOUT_STATE, db_index=True)

    class Meta:
        verbose_name = _("payout")
        verbose_name_plural = _("payouts")

    def clean(self):
        if self.parent and not self.amount:
            self.amount = self.parent.amount

        # prevent defining both reference and messages
        if self.messages and self.reference or not self.messages and not self.reference:
            raise ValidationError(_('payment.must.have.reference.or.messages'))

        # validate reference if any
        if self.reference:
            if self.reference[:2] == 'RF':
                iso_payment_reference_validator(self.reference)
            else:
                fi_payment_reference_validator(self.reference)

        # prevent canceling payouts which have been uploaded successfully
        if self.state == PAYOUT_CANCELED:
            if self.is_upload_done:
                group_status = self.group_status
                if group_status != 'RJCT':
                    raise ValidationError(_('File already uploaded') + ' ({})'.format(group_status))

        # save paid time if marking payout as paid manually
        if self.state == PAYOUT_PAID and not self.paid_date:
            self.paid_date = now()
            status = self.payoutstatus_set.order_by('-created').first()
            if status:
                assert isinstance(status, PayoutStatus)
                self.paid_date = status.created

    def generate_msg_id(self, commit: bool = True):
        msg_id_base = re.sub(r'[^\d]', '', now().isoformat())[:-4]
        self.msg_id = msg_id_base + 'P' + str(self.id)
        if commit:
            self.save(update_fields=['msg_id'])

    @property
    def state_name(self):
        return choices_label(PAYOUT_STATE, self.state)

    @property
    def is_upload_done(self):
        return PayoutStatus.objects.filter(payout=self, response_code='00').first() is not None

    @property
    def is_accepted(self):
        return self.has_group_status('ACCP')

    @property
    def is_rejected(self):
        return self.has_group_status('RJCT')

    def has_group_status(self, group_status: str) -> bool:
        return PayoutStatus.objects.filter(payout=self, group_status=group_status).first() is not None

    @property
    def group_status(self):
        status = PayoutStatus.objects.filter(payout=self).order_by('-id').first()
        return status.group_status if status else ''
    group_status.fget.short_description = _('payment.group.status')


class PayoutStatusManager(models.Manager):
    def is_file_processed(self, filename: str) -> bool:
        return self.filter(file_name=basename(filename)).first() is not None


class PayoutStatus(models.Model):
    objects = PayoutStatusManager()
    payout = models.ForeignKey(Payout, verbose_name=_('payout'), related_name='payoutstatus_set', on_delete=models.PROTECT, null=True, default=None, blank=True)  # noqa
    created = models.DateTimeField(_('created'), default=now, db_index=True, editable=False, blank=True)
    file_name = models.CharField(_('file name'), max_length=255, blank=True, db_index=True, editable=False)
    response_code = models.CharField(_('response code'), max_length=4, blank=True, db_index=True)
    response_text = models.CharField(_('response text'), max_length=128, blank=True)
    msg_id = models.CharField(_('message id'), max_length=64, blank=True, db_index=True)
    original_msg_id = models.CharField(_('original message id'), blank=True, max_length=64, db_index=True)
    group_status = models.CharField(_('group status'), max_length=8, blank=True, db_index=True)
    status_reason = models.CharField(_('status reason'), max_length=255, blank=True)

    class Meta:
        verbose_name = _("payout status")
        verbose_name_plural = _("payout statuses")

    def __str__(self):
        return self.group_status

    @property
    def is_accepted(self):
        return self.group_status == 'ACCP'

    @property
    def is_rejected(self):
        return self.group_status == 'RJCT'


class Refund(Payout):
    class Meta:
        verbose_name = _("refund")
        verbose_name_plural = _("refunds")


class WsEdiSoapCall(models.Model):
    connection = models.ForeignKey('WsEdiConnection', verbose_name=_('WS-EDI connection'), on_delete=models.CASCADE)
    command = models.CharField(_('command'), max_length=64, blank=True, db_index=True)
    created = models.DateTimeField(_('created'), default=now, db_index=True, editable=False, blank=True)
    executed = models.DateTimeField(_('executed'), default=None, null=True, db_index=True, editable=False, blank=True)
    error = models.TextField(_('error'), blank=True)

    class Meta:
        verbose_name = _("WS-EDI SOAP call")
        verbose_name_plural = _("WS-EDI SOAP calls")

    def __str__(self):
        return 'WsEdiSoapCall({})'.format(self.id)

    @property
    def timestamp(self) -> datetime:
        return self.created.astimezone(pytz.timezone('Europe/Helsinki'))

    @property
    def request_identifier(self) -> str:
        return str(self.id)

    @property
    def command_camelcase(self) -> str:
        return self.command[0:1].lower() + self.command[1:]

    def debug_get_filename(self, file_type: str) -> str:
        return '{:08}{}.xml'.format(self.id, file_type)

    @property
    def debug_application_request_full_path(self) -> str:
        return self.debug_get_file_path(self.debug_get_filename('a'))

    @property
    def debug_application_response_full_path(self) -> str:
        return self.debug_get_file_path(self.debug_get_filename('r'))

    @staticmethod
    def debug_get_file_path(filename: str) -> str:
        return os.path.join(settings.WSEDI_LOG_PATH, filename) if hasattr(settings, 'WSEDI_LOG_PATH') and settings.WSEDI_LOG_PATH else ''


class WsEdiConnection(models.Model):
    name = models.CharField(_('name'), max_length=64)
    enabled = models.BooleanField(_('enabled'), blank=True, default=True)
    sender_identifier = models.CharField(_('sender identifier'), max_length=32)
    receiver_identifier = models.CharField(_('receiver identifier'), max_length=32)
    target_identifier = models.CharField(_('target identifier'), max_length=32)
    environment = models.CharField(_('environment'), max_length=32, default='PRODUCTION')
    soap_endpoint = models.URLField(_('SOAP endpoint'))
    signing_cert_file = models.FileField(_('signing certificate file'), blank=True, upload_to='certs')
    signing_key_file = models.FileField(_('signing key file'), blank=True, upload_to='certs')
    encryption_cert_file = models.FileField(_('encryption certificate file'), blank=True, upload_to='certs')
    encryption_key_file = models.FileField(_('encryption key file'), blank=True, upload_to='certs')
    bank_encryption_cert_file = models.FileField(_('bank encryption cert file'), blank=True, upload_to='certs')
    debug_commands = models.TextField(_('debug commands'), blank=True)
    created = models.DateTimeField(_('created'), default=now, db_index=True, editable=False, blank=True)
    _signing_cert = None

    class Meta:
        verbose_name = _("WS-EDI connection")
        verbose_name_plural = _("WS-EDI connections")

    def __str__(self):
        return '{} / {}'.format(self.name, self.receiver_identifier)

    @property
    def signing_cert_full_path(self) -> str:
        return self.signing_cert_file.file.name if self.signing_cert_file else ''

    @property
    def signing_key_full_path(self) -> str:
        return self.signing_key_file.file.name if self.signing_key_file else ''

    @property
    def encryption_cert_full_path(self) -> str:
        return self.encryption_cert_file.file.name if self.signing_cert_file else ''

    @property
    def encryption_key_full_path(self) -> str:
        return self.encryption_key_file.file.name if self.signing_key_file else ''

    @property
    def bank_encryption_cert_full_path(self) -> str:
        return self.bank_encryption_cert_file.file.name if self.bank_encryption_cert_file else ''

    @property
    def bank_encryption_cert_with_public_key_full_path(self) -> str:
        src_file = self.bank_encryption_cert_full_path
        file = src_file[:-4] + '-with-pubkey.pem'
        if not os.path.isfile(file):
            out = subprocess.check_output([
                settings.OPENSSL_PATH,
                'x509',
                '-pubkey',
                '-in',
                src_file,
            ])
            with open(file, 'wb') as fp:
                fp.write(out)
        return file

    @property
    def signing_cert(self):
        if hasattr(self, '_signing_cert') and self._signing_cert:
            return self._signing_cert
        pem_data = open(self.signing_cert_full_path, 'rb').read()
        cert = x509.load_pem_x509_certificate(pem_data, cryptography.hazmat.backends.default_backend())
        self._signing_cert = cert
        return cert

    def get_application_request(self, command: str, **kwargs) -> bytes:
        return format_xml(get_template('jbank/application_request_template.xml').render({
            'ws': self,
            'command': command,
            'timestamp': now().astimezone(pytz.timezone('Europe/Helsinki')).isoformat(),
            **kwargs
        })).encode()

    def verify_signature(self, content: bytes):
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            subprocess.check_output([
                settings.XMLSEC1_PATH,
                '--verify',
                '--pubkey-pem',
                self.signing_key_full_path,
                fp.name
            ])

    def sign_application_request(self, content: bytes) -> bytes:
        """
        Sign application request.
        See https://users.dcc.uchile.cl/~pcamacho/tutorial/web/xmlsec/xmlsec.html
        :param content: XML application request
        :return: str
        """
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            out = subprocess.check_output([
                settings.XMLSEC1_PATH,
                '--sign',
                '--privkey-pem',
                '{},{}'.format(self.signing_key_full_path, self.signing_cert_full_path),
                fp.name
            ])
        self.verify_signature(out)
        return out

    def encrypt_application_request(self, content: bytes) -> bytes:
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            out = subprocess.check_output([
                self._xmlsec1_example_bin('encrypt3'),
                fp.name,
                self.bank_encryption_cert_with_public_key_full_path,
                self.bank_encryption_cert_full_path
            ])
        return out

    def encode_application_request(self, content: bytes) -> bytes:
        lines = content.split(b'\n')
        if lines and lines[0].startswith(b'<?xml'):
            lines = lines[1:]
        content_without_xml_tag = b'\n'.join(lines)
        return base64.b64encode(content_without_xml_tag)

    def decode_application_response(self, content: bytes) -> bytes:
        return base64.b64decode(content)

    def decrypt_application_response(self, content: bytes) -> bytes:
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(content)
            fp.flush()
            out = subprocess.check_output([
                self._xmlsec1_example_bin('decrypt3'),
                fp.name,
                self.encryption_key_full_path,
            ])
        return out

    @property
    def debug_command_list(self) -> List[str]:
        return [x for x in re.sub(r'[^\w]+', ' ', self.debug_commands).strip().split(' ') if x]

    @staticmethod
    def _xmlsec1_example_bin(file: str) -> str:
        xmlsec1_examples_path = settings.XMLSEC1_EXAMPLES_PATH if hasattr(settings, 'XMLSEC1_EXAMPLES_PATH') and settings.XMLSEC1_EXAMPLES_PATH else ''
        if not xmlsec1_examples_path:
            xmlsec1_examples_path = os.path.join(os.getenv('HOME'), 'bin/xmlsec1-examples')
        return str(os.path.join(xmlsec1_examples_path, file))
