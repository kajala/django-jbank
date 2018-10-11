import logging
from os.path import basename
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import ugettext_lazy as _
from jacc.models import Account, AccountType, EntryType
from jbank.models import Statement, StatementRecord, StatementRecordSepaInfo, ReferencePaymentBatch, \
    ReferencePaymentRecord, StatementFile, ReferencePaymentBatchFile, Payout, PayoutStatus, PAYOUT_PAID


ASSIGNABLE_STATEMENT_HEADER_FIELDS = (
    'account_number',
    'statement_number',
    'begin_date',
    'end_date',
    'record_date',
    'customer_identifier',
    'begin_balance_date',
    'begin_balance',
    'record_count',
    'currency_code',
    'account_name',
    'account_limit',
    'owner_name',
    'contact_info_1',
    'contact_info_2',
    'bank_specific_info_1',
    'iban',
    'bic',
)

ASSIGNABLE_STATEMENT_RECORD_FIELDS = (
    'record_number',
    'archive_identifier',
    'record_date',
    'value_date',
    'paid_date',
    'entry_type',
    'record_code',
    'record_description',
    'amount',
    'receipt_code',
    'delivery_method',
    'name',
    'name_source',
    'recipient_account_number',
    'recipient_account_number_changed',
    'remittance_info',
)

ASSIGNABLE_STATEMENT_RECORD_SEPA_INFO_FIELDS = (
    'reference',
    'iban_account_number',
    'bic_code',
    'recipient_name_detail',
    'payer_name_detail',
    'identifier',
    'archive_identifier',
)

ASSIGNABLE_REFERENCE_PAYMENT_BATCH_HEADER_FIELDS = (
    'record_date',
    'institution_identifier',
    'service_identifier',
    'currency_identifier',
)

ASSIGNABLE_REFERENCE_PAYMENT_RECORD_FIELDS = (
    'record_type',
    'account_number',
    'record_date',
    'paid_date',
    'archive_identifier',
    'remittance_info',
    'payer_name',
    'currency_identifier',
    'name_source',
    'amount',
    'correction_identifier',
    'delivery_method',
    'receipt_code',
)

logger = logging.getLogger(__name__)


@transaction.atomic
def create_statement(statement_data: dict, name: str, file: StatementFile, **kw) -> Statement:
    """
    Creates Statement from statement data parsed by parse_tiliote_statements()
    :param statement_data: See parse_tiliote_statements
    :param name: File name of the account statement
    :param file: Source statement file
    :return: Statement
    """
    if 'header' not in statement_data or not statement_data['header']:
        raise ValidationError('Invalid header field in statement data {}: {}'.format(name, statement_data.get('header')))
    header = statement_data['header']

    account_number = header['account_number']
    if not account_number:
        raise ValidationError('{name}: '.format(name=name) + _("account.not.found").format(account_number=''))
    accounts = list(Account.objects.filter(name=account_number))
    if len(accounts) != 1:
        raise ValidationError('{name}: '.format(name=name) + _("account.not.found").format(account_number=account_number))
    account = accounts[0]
    assert isinstance(account, Account)

    if Statement.objects.filter(name=name, account=account).first():
        raise ValidationError('Bank account {} statement {} of processed already'.format(account_number, name))
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
        raise ValidationError(_('entry.type.missing') + ' ({}): {}'.format('settings.E_BANK_DEPOSIT', settings.E_BANK_DEPOSIT))
    if EntryType.objects.filter(code=settings.E_BANK_WITHDRAW).count() == 0:
        raise ValidationError(_('entry.type.missing') + ' ({}): {}'.format('settings.E_BANK_WITHDRAW', settings.E_BANK_WITHDRAW))
    entry_types = {
        '1': EntryType.objects.get(code=settings.E_BANK_DEPOSIT),
        '2': EntryType.objects.get(code=settings.E_BANK_WITHDRAW),
    }

    for rec_data in statement_data['records']:
        e_type = entry_types.get(rec_data['entry_type'])
        rec = StatementRecord(statement=stm, account=account, type=e_type)
        for k in ASSIGNABLE_STATEMENT_RECORD_FIELDS:
            if k in rec_data:
                setattr(rec, k, rec_data[k])
        for k in ('messages', 'client_messages', 'bank_messages'):
            if k in rec_data:
                setattr(rec, k, '\n'.join(rec_data[k]))
        rec.full_clean()
        rec.save()

        if 'sepa' in rec_data:
            sepa_info_data = rec_data['sepa']
            sepa_info = StatementRecordSepaInfo(record=rec)
            for k in ASSIGNABLE_STATEMENT_RECORD_SEPA_INFO_FIELDS:
                if k in sepa_info_data:
                    setattr(sepa_info, k, sepa_info_data[k])
            # pprint(rec_data['sepa'])
            sepa_info.full_clean()
            sepa_info.save()


@transaction.atomic
def create_reference_payment_batch(batch_data: dict, name: str, file: ReferencePaymentBatchFile, **kw) -> ReferencePaymentBatch:
    """
    Creates ReferencePaymentBatch from data parsed by parse_svm_batches()
    :param batch_data: See parse_svm_batches
    :param name: File name of the batch file
    :return: ReferencePaymentBatch
    """
    if ReferencePaymentBatch.objects.exclude(file=file).filter(name=name).first():
        raise ValidationError('Reference payment batch file {} already exists'.format(name))

    if 'header' not in batch_data or not batch_data['header']:
        raise ValidationError('Invalid header field in reference payment batch data {}: {}'.format(name, batch_data.get('header')))
    header = batch_data['header']

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

    for rec_data in batch_data['records']:
        account_number = rec_data['account_number']
        if not account_number:
            raise ValidationError('{name}: '.format(name=name) + _("account.not.found").format(account_number=''))
        accounts = list(Account.objects.filter(name=account_number))
        if len(accounts) != 1:
            raise ValidationError('{name}: '.format(name=name) + _("account.not.found").format(account_number=account_number))
        account = accounts[0]
        assert isinstance(account, Account)

        rec = ReferencePaymentRecord(batch=batch, account=account, type=e_type)
        for k in ASSIGNABLE_REFERENCE_PAYMENT_RECORD_FIELDS:
            if k in rec_data:
                setattr(rec, k, rec_data[k])
        # pprint(rec_data)
        rec.full_clean()
        rec.save()


def get_or_create_bank_account(account_number: str, currency: str='EUR') -> Account:
    a_type, created = AccountType.objects.get_or_create(code=settings.ACCOUNT_BANK_ACCOUNT, is_asset=True, defaults={'name': _('bank account')})
    acc, created = Account.objects.get_or_create(name=account_number, type=a_type, currency=currency)
    return acc


def process_pain002_file_content(bcontent: bytes, filename: str):
    from jbank.sepa import Pain002

    s = Pain002(bcontent)
    p = Payout.objects.filter(msg_id=s.original_msg_id).first()
    ps = PayoutStatus(payout=p, file_name=basename(filename), msg_id=s.msg_id, original_msg_id=s.original_msg_id, group_status=s.group_status, status_reason=s.status_reason[:255])
    ps.full_clean()
    fields = (
        'payout',
        'file_name',
        'response_code',
        'response_text',
        'msg_id',
        'original_msg_id',
        'group_status',
        'status_reason',
    )
    params = {}
    for k in fields:
        params[k] = getattr(ps, k)
    ps_old = PayoutStatus.objects.filter(**params).first()
    if ps_old:
        ps = ps_old
    else:
        ps.save()
        logger.info('{} status updated {}'.format(p, ps))
    if p:
        if ps.is_accepted:
            p.state = PAYOUT_PAID
            p.paid_date = s.credit_datetime
            p.save(update_fields=['state', 'paid_date'])
            logger.info('{} marked as paid {}'.format(p, ps))
    return ps
