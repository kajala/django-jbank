import logging
import re
from datetime import datetime, time, date
from os.path import basename, join
import pytz
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models
from django.utils.text import format_lazy
from django.utils.timezone import now
from django.utils.translation import ugettext_lazy as _
from jacc.models import AccountEntry, AccountEntrySourceFile, Account
from jutil.dict import choices_label
from jutil.validators import fi_iban_validator, iban_validator, iban_bic, iso_payment_reference_validator, \
    fi_payment_reference_validator

logger = logging.getLogger(__name__)


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

DELIVERY_METHOD = (
    ('A', _('From Customer')),
    ('K', _('From Bank Clerk')),
    ('J', _('From Bank System')),
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
    statement_number = models.SmallIntegerField(_('statement number'), )
    begin_date = models.DateField(_('begin date'), db_index=True)
    end_date = models.DateField(_('end date'), db_index=True)
    record_date = models.DateTimeField(_('record date'), db_index=True)
    customer_identifier = models.CharField(_('customer identifier'), max_length=32)
    begin_balance_date = models.DateField(_('begin balance date'), )
    begin_balance = models.DecimalField(_('begin balance'), max_digits=10, decimal_places=2)
    record_count = models.IntegerField(_('record count'), null=True, default=None)
    currency_code = models.CharField(_('currency code'), max_length=3)
    account_name = models.CharField(_('account name'), max_length=32, blank=True, default='')
    account_limit = models.DecimalField(_('account limit'), max_digits=10, decimal_places=2, blank=True, default=None, null=True)
    owner_name = models.CharField(_('owner name'), max_length=64)
    contact_info_1 = models.CharField(_('contact info (1)'), max_length=64)
    contact_info_2 = models.CharField(_('contact info (2)'), max_length=64, blank=True, default='')
    bank_specific_info_1 = models.CharField(_('bank specific info (1)'), max_length=32, blank=True, default='')
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
    line_number = models.SmallIntegerField(_('line number'), default=0, blank=True)
    record_number = models.IntegerField(_('record number'))
    archive_identifier = models.CharField(_('archive identifier'), max_length=32, blank=True, default='', db_index=True)
    record_date = models.DateField(_('record date'), db_index=True)
    value_date = models.DateField(_('value date'), db_index=True, blank=True, null=True, default=None)
    paid_date = models.DateField(_('paid date'), db_index=True, blank=True, null=True, default=None)
    entry_type = models.CharField(_('entry type'), max_length=1, choices=RECORD_ENTRY_TYPE, db_index=True)
    record_code = models.CharField(_('record type'), max_length=3, choices=RECORD_CODES, db_index=True)
    record_description = models.CharField(_('record description'), max_length=64)
    receipt_code = models.CharField(_('receipt code'), max_length=1, choices=RECEIPT_CODE, db_index=True, blank=True)
    delivery_method = models.CharField(_('delivery method'), max_length=1, db_index=True, choices=DELIVERY_METHOD)
    name = models.CharField(_('name'), max_length=64, blank=True, db_index=True)
    name_source = models.CharField(_('name source'), max_length=1, blank=True, choices=NAME_SOURCES)
    recipient_account_number = models.CharField(_('recipient account number'), max_length=32, blank=True, db_index=True)
    recipient_account_number_changed = models.CharField(_('recipient account number changed'), max_length=1, blank=True)
    remittance_info = models.CharField(_('remittance info'), max_length=32, db_index=True, blank=True)
    messages = models.TextField(_('messages'), blank=True, default='')
    client_messages = models.TextField(_('client messages'), blank=True, default='')
    bank_messages = models.TextField(_('bank messages'), blank=True, default='')

    class Meta:
        verbose_name = _('statement record')
        verbose_name_plural = _('statement records')

    def clean(self):
        self.source_file = self.statement
        self.timestamp = pytz.utc.localize(datetime.combine(self.record_date, time(0, 0)))
        self.description = '{name}: {record_description}'.format(record_description=self.record_description, name=self.name)


class StatementRecordSepaInfo(models.Model):
    record = models.OneToOneField(StatementRecord, verbose_name=_('record'), related_name='sepa_info', on_delete=models.CASCADE)
    reference = models.CharField(_('reference'), max_length=35, blank=True)
    iban_account_number = models.CharField(_('IBAN'), max_length=35, blank=True)
    bic_code = models.CharField(_('BIC'), max_length=35, blank=True)
    recipient_name_detail = models.CharField(_('recipient name detail'), max_length=70, blank=True)
    payer_name_detail = models.CharField(_('payer name detail'), max_length=70, blank=True)
    identifier = models.CharField(_('identifier'), max_length=35, blank=True)
    archive_identifier = models.CharField(_('archive identifier'), max_length=35, blank=True)

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
    institution_identifier = models.CharField(_('institution identifier'), max_length=2)
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
        self.description = '{amount} {remittance_info} {payer_name}'.format(amount=self.amount, remittance_info=self.remittance_info, payer_name=self.payer_name)


class StatementFile(models.Model):
    created = models.DateTimeField(_('created'), default=now, db_index=True, blank=True, editable=False)
    file = models.FileField(_('file'), upload_to='uploads')
    errors = models.TextField(_('errors'), max_length=4086, default='', blank=True)

    class Meta:
        verbose_name = _('account statement file')
        verbose_name_plural = _('account statement files')

    @property
    def full_path(self):
        return join(settings.BASE_DIR, self.file.name) if self.file else ''

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
        return join(settings.BASE_DIR, self.file.name) if self.file else ''

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
        return self.name

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
        if self.messages and self.reference:
            raise ValidationError(_('Both recipient messages and recipient reference cannot be defined simultaneously'))

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

    def generate_msg_id(self):
        self.msg_id = re.sub(r'[^\d]', '', now().date().isoformat()) + 'P' + str(self.id)

    @property
    def state_name(self):
        return choices_label(PAYOUT_STATE, self.state)

    @property
    def is_upload_done(self):
        return PayoutStatus.objects.filter(payout=self, response_code='00').first() is not None

    @property
    def is_accepted(self):
        return self.group_status == 'ACCP'

    @property
    def is_rejected(self):
        return self.group_status == 'RJCT'

    @property
    def group_status(self):
        status = PayoutStatus.objects.filter(payout=self).order_by('-id').first()
        return status.group_status if status else ''


class PayoutStatusManager(models.Manager):
    def is_file_processed(self, filename: str) -> bool:
        return self.filter(file_name=basename(filename)).first() is not None


class PayoutStatus(models.Model):
    objects = PayoutStatusManager()
    payout = models.ForeignKey(Payout, verbose_name=_('payout'), related_name='payoutstatus_set', on_delete=models.PROTECT, null=True, default=None, blank=True)
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
