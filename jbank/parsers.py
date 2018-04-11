import logging
import re
from copy import copy
from os.path import basename
from pprint import pprint
from datetime import time, datetime, date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext as _
from pytz import timezone


REGEX_SIMPLE_FIELD = re.compile(r'^(X|9)+$')
REGEX_VARIABLE_FIELD = re.compile(r'^(X|9)\((\d+)\)$')

TO_FILE_HEADER_TYPES = ('T00', )

TO_FILE_HEADER_DATES = (
    'begin_date',
    'end_date',
    ('record_date', 'record_time'),
    'begin_balance_date',
)

TO_FILE_HEADER_DECIMALS = (
    ('begin_balance', 'begin_balance_sign'),
    'account_limit',
)

TO_FILE_HEADER = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('version', 'X(3)', 'P'),
    ('account_number', 'X(14)', 'P'),
    ('statement_number', '9(3)', 'P'),
    ('begin_date', '9(6)', 'P'),
    ('end_date', '9(6)', 'P'),
    ('record_date', '9(6)', 'P'),
    ('record_time', '9(4)', 'P'),
    ('customer_identifier', 'X(17)', 'P'),
    ('begin_balance_date', '9(6)', 'P'),
    ('begin_balance_sign', 'X', 'P'),
    ('begin_balance', '9(18)', 'P'),
    ('record_count', '9(6)', 'P'),
    ('currency_code', 'X(3)', 'P'),
    ('account_name', 'X(30)', 'V'),
    ('account_limit', '9(18)', 'P'),
    ('owner_name', 'X(35)', 'P'),
    ('contact_info_1', 'X(40)', 'P'),
    ('contact_info_2', 'X(40)', 'V'),
    ('bank_specific_info_1', 'X(30)', 'V'),
    ('iban_and_bic', 'X(30)', 'V'),
)

TO_FILE_RECORD_TYPES = ('T10', 'T80')

TO_FILE_RECORD_DATES = (
    'record_date',
    'value_date',
    'paid_date',
)

TO_FILE_RECORD_DECIMALS = (
    ('amount', 'amount_sign'),
)

TO_FILE_RECORD = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('record_number', '9(6)', 'P'),
    ('archive_identifier', 'X(18)', 'V'),
    ('record_date', '9(6)', 'P'),
    ('value_date', '9(6)', 'V'),
    ('paid_date', '9(6)', 'V'),
    ('entry_type', 'X', 'P'),  # 1 = pano, 2 = otto, 3 = panon korjaus, 4 = oton korjaus, 9 = hylätty tapahtuma
    ('record_code', 'X(3)', 'P'),
    ('record_description', 'X(35)', 'P'),
    ('amount_sign', 'X', 'P'),
    ('amount', '9(18)', 'P'),
    ('receipt_code', 'X', 'P'),
    ('delivery_method', 'X', 'P'),
    ('name', 'X(35)', 'V'),
    ('name_source', 'X', 'V'),
    ('recipient_account_number', 'X(14)', 'V'),
    ('recipient_account_number_changed', 'X', 'V'),
    ('remittance_info', 'X(20)', 'V'),
    ('form_number', 'X(8)', 'V'),
    ('level_identifier', 'X', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_TYPES = ('T11', 'T81')

TO_FILE_RECORD_EXTRA_INFO_HEADER = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('extra_info_type', '9(2)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_COUNTS = (
    ('entry_count', '9(8)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_INVOICE = (
    ('customer_number', 'X(10)', 'P'),
    ('pad01', 'X', 'P'),
    ('invoice_number', 'X(15)', 'P'),
    ('pad02', 'X', 'P'),
    ('invoice_date', 'X(6)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_CARD = (
    ('card_number', 'X(19)', 'P'),
    ('pad01', 'X', 'P'),
    ('merchant_reference', 'X(14)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_CORRECTION = (
    ('original_archive_identifier', 'X(18)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_CURRENCY_DECIMALS = (
    ('amount', 'amount_sign'),
)

TO_FILE_RECORD_EXTRA_INFO_CURRENCY = (
    ('amount_sign', 'X', 'P'),
    ('amount', '9(18)', 'P'),
    ('pad01', 'X', 'P'),
    ('currency_code', 'X(3)', 'P'),
    ('pad02', 'X', 'P'),
    ('exchange_rate', '9(11)', 'P'),
    ('rate_reference', 'X(6)', 'V'),
)

TO_FILE_RECORD_EXTRA_INFO_REASON = (
    ('reason_code', '9(3)', 'P'),
    ('pad01', 'X', 'P'),
    ('reason_description', 'X(31)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_NAME_DETAIL = (
    ('name_detail', 'X(35)', 'P'),
)

TO_FILE_RECORD_EXTRA_INFO_SEPA = (
    ('reference', 'X(35)', 'V'),
    ('iban_account_number', 'X(35)', 'V'),
    ('bic_code', 'X(35)', 'V'),
    ('recipient_name_detail', 'X(70)', 'V'),
    ('payer_name_detail', 'X(70)', 'V'),
    ('identifier', 'X(35)', 'V'),
    ('archive_identifier', 'X(35)', 'V'),
)

TO_BALANCE_RECORD_DATES = (
    'record_date',
)

TO_BALANCE_RECORD_DECIMALS = (
    ('end_balance', 'end_balance_sign'),
    ('available_balance', 'available_balance_sign'),
)

TO_BALANCE_RECORD = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('record_date', '9(6)', 'P'),
    ('end_balance_sign', 'X', 'P'),
    ('end_balance', '9(18)', 'P'),
    ('available_balance_sign', 'X', 'P'),
    ('available_balance', '9(18)', 'P'),
)

TO_CUMULATIVE_RECORD_DATES = (
    'period_date',
)

TO_CUMULATIVE_RECORD_DECIMALS = (
    ('deposits_amount', 'deposits_sign'),
    ('withdrawals_amount', 'withdrawals_sign'),
)

TO_CUMULATIVE_RECORD = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('period_identifier', 'X', 'P'),  # 1=day, 2=term, 3=month, 4=year
    ('period_date', '9(6)', 'P'),
    ('deposits_count', '9(8)', 'P'),
    ('deposits_sign', 'X', 'P'),
    ('deposits_amount', '9(18)', 'P'),
    ('withdrawals_count', '9(8)', 'P'),
    ('withdrawals_sign', 'X', 'P'),
    ('withdrawals_amount', '9(18)', 'P'),
)

TO_SPECIAL_RECORD = (
    ('statement_type', 'X', 'P'),
    ('record_type', 'XX', 'P'),
    ('record_length', '9(3)', 'P'),
    ('bank_group_identifier', 'X(3)', 'P'),
)

SVM_FILE_HEADER_DATES = (
    ('record_date', 'record_time'),
)

SVM_FILE_HEADER_TYPES = (
    '0',
)

SVM_FILE_HEADER = (
    ('statement_type', '9(1)', 'P'),
    ('record_date', '9(6)', 'P'),
    ('record_time', '9(4)', 'P'),
    ('institution_identifier', 'X(2)', 'P'),
    ('service_identifier', 'X(9)', 'P'),
    ('currency_identifier', 'X(1)', 'P'),
    ('pad01', 'X(67)', 'P'),
)

SVM_FILE_RECORD_TYPES = ('3', '5')

SVM_FILE_RECORD_DECIMALS = (
    'amount',
)

SVM_FILE_RECORD_DATES = (
    'record_date',
    'paid_date',
)

SVM_FILE_RECORD = (
    ('record_type', '9(1)', 'P'),  # 3=viitesiirto, 5=suoraveloitus
    ('account_number', '9(14)', 'P'),
    ('record_date', '9(6)', 'P'),
    ('paid_date', '9(6)', 'P'),
    ('archive_identifier', 'X(16)', 'P'),
    ('remittance_info', '9(20)', 'P'),
    ('payer_name', 'X(12)', 'P'),
    ('currency_identifier', 'X(1)', 'P'),  # 1=eur
    ('name_source', 'X', 'V'),
    ('amount', '9(10)', 'P'),
    ('correction_identifier', 'X', 'V'),  # 0=normal, 1=correction
    ('delivery_method', 'X', 'P'),  # A=asiakkaalta, K=konttorista, J=pankin jarjestelmasta
    ('receipt_code', 'X', 'P'),
)

SVM_FILE_SUMMARY_TYPES = (
    '9',
)

SVM_FILE_SUMMARY_DECIMALS = (
    'record_amount',
    'correction_amount',
)

SVM_FILE_SUMMARY = (
    ('record_type', '9(1)', 'P'),  # 9
    ('record_count', '9(6)', 'P'),
    ('record_amount', '9(11)', 'P'),
    ('correction_count', '9(6)', 'P'),
    ('correction_amount', '9(11)', 'P'),
    ('pad01', 'X(5)', 'P'),
)

logger = logging.getLogger(__name__)


def parse_record_format(fmt: str) -> tuple:
    """
    :param fmt: Data format used in .TO files
    :return: Data type ('X' or '9'), data length (number of characters)
    """
    res = REGEX_SIMPLE_FIELD.match(fmt)
    data_type, data_len = None, None
    if res:
        data_type = res.group(1)
        data_len = len(fmt)
    else:
        res = REGEX_VARIABLE_FIELD.match(fmt)
        if res:
            data_type = res.group(1)
            data_len = int(res.group(2))
    if not data_type or not data_len:
        raise Exception('Failed to parse data format {}'.format(fmt))
    return data_type, data_len


def parse_record_value(data_type, data_len, data, name: str, line_number: int) -> str:
    value = data[:data_len]
    if len(value) != data_len:
        raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
    if data_type == 'X':
        pass
    elif data_type == '9':
        charset = '0123456789'
        for ch in value:
            if ch not in charset:
                raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
        # logger.info('jbank.parsers.parse_record_value: {} = {}'.format(name, value))
    else:
        raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
    return value


def parse_records(line: str, specs: tuple, line_number: int, check_record_length: bool=True, record_length: int=None) -> dict:
    # print(line)
    i = 0
    data = {}
    for name, fmt, req in specs:
        data_type, data_len = parse_record_format(fmt)
        value = parse_record_value(data_type, data_len, line[i:], name=name, line_number=line_number)
        # print('[{}:{}] {}="{}"'.format(i, i+data_len, name, value))
        data[name] = str(value).strip()
        i += data_len
    data['extra_data'] = line[i:]

    if 'record_length' in data:
        record_length = data['record_length']
    if check_record_length and record_length:
        data['extra_data'] = data['extra_data'].strip()
        if i != record_length and data['extra_data'] != '':
            raise ValidationError(_('Line {line}: Record length ({record_length}) does not match length of parsed data ({data_length}). Extra data: "{extra_data}"').format(line=line_number, data_length=i+len(data['extra_data']), record_length=record_length, extra_data=data['extra_data']))
    return data


def parse_record_messages(extra_data: str) -> str:
    msg = []
    while extra_data:
        msg.append(extra_data[:35])
        extra_data = extra_data[35:]
    return msg


def parse_record_extra_info(record: dict, line: str, line_number: int) -> dict:
    assert line[:3] in TO_FILE_RECORD_EXTRA_INFO_TYPES

    header = parse_records(line, TO_FILE_RECORD_EXTRA_INFO_HEADER, line_number, check_record_length=False)
    extra_info_type = header['extra_info_type']
    # print(line)
    extra_data = copy(header['extra_data'])
    if extra_info_type == '00':
        record['messages'] = parse_record_messages(extra_data)
    elif extra_info_type == '01':
        record['counts'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_COUNTS, line_number, record_length=8)
    elif extra_info_type == '02':
        record['invoice'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_INVOICE, line_number, record_length=33)
    elif extra_info_type == '03':
        record['card'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CARD, line_number, record_length=34)
    elif extra_info_type == '04':
        record['correction'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CORRECTION, line_number, record_length=18)
    elif extra_info_type == '05':
        record['currency'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CURRENCY, line_number, record_length=41)
        convert_decimal_fields(record['currency'], TO_FILE_RECORD_EXTRA_INFO_CURRENCY_DECIMALS)
    elif extra_info_type == '06':
        record['client_messages'] = parse_record_messages(extra_data)
    elif extra_info_type == '07':
        record['bank_messages'] = parse_record_messages(extra_data)
    elif extra_info_type == '08':
        record['reason'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_REASON, line_number, record_length=35)
    elif extra_info_type == '09':
        record['name_detail'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_NAME_DETAIL, line_number, record_length=35)
    elif extra_info_type == '11':
        record['sepa'] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_SEPA, line_number, record_length=323)
    else:
        raise ValidationError(_('Line {line}: Invalid record extra info type "{extra_info_type}"').format(line=line_number, extra_info_type=extra_info_type))


def convert_date(v: str, field_name: str) -> date:
    if v is None:
        raise ValidationError(_("Date field missing: {}").format(field_name))
    if len(v) != 6:
        raise ValidationError(_("Date format error in field {}: {}").format(field_name, v))
    year = int(v[0:2]) + 2000
    month = int(v[2:4])
    day = int(v[4:6])
    return date(year=year, month=month, day=day)


def convert_time(v: str, field_name: str) -> time:
    if v is None:
        raise ValidationError(_("Time field missing: {}").format(field_name))
    if not re.match(r'^\d\d\d\d$', v):
        raise ValidationError(_("Time format error in field {}: {}").format(field_name, v))
    return time(int(v[0:2]), int(v[2:4]))


def convert_date_fields(data: dict, date_fields: tuple, tz: timezone):
    for k in date_fields:
        if isinstance(k, str):
            v_date = data.get(k)
            if v_date:
                data[k] = convert_date(v_date, k)
        elif isinstance(k, tuple):
            if len(k) != 2:
                raise ValidationError(_("Date format error in field {}: {}").format(k, v))
            k_date, k_time = k
            v_date, v_time = data.get(k_date), data.get(k_time)
            # print('Converting {}'.format(k))
            # pprint(data)
            if v_date or v_time:
                v_date = convert_date(v_date, k_date)
                v_time = convert_time(v_time, k_time)
                v_datetime = datetime.combine(v_date, v_time)
                data[k_date] = tz.localize(v_datetime)
                del data[k_time]


def convert_decimal_fields(data: dict, decimal_fields: tuple):
    for field in decimal_fields:
        if isinstance(field, str):
            v_number = data.get(field)
            if v_number is not None:
                v = Decimal(v_number) * Decimal('0.01')
                # logger.info('jbank.parsers.convert_decimal_fields: {} = {}'.format(field, v))
                data[field] = v
        elif isinstance(field, tuple) and len(field) == 2:
            k_number, k_sign = field
            v_number, v_sign = data.get(k_number), data.get(k_sign)
            if v_number is not None:
                v = Decimal(v_number) * Decimal('0.01')
                if v_sign == '-':
                    v = -v
                data[k_number] = v
                # logger.info('jbank.parsers.convert_decimal_fields: {} = {}'.format(k_number, v))
                del data[k_sign]
        else:
            raise ValidationError(_('Invalid decimal field format: {}').format(field))


def combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records) -> dict:
    data = {
        'header': header,
        'records': records,
    }
    if balance is not None:
        data['balance'] = balance
    if cumulative is not None:
        data['cumulative'] = cumulative
    if cumulative_adjustment is not None:
        data['cumulative_adjustment'] = cumulative_adjustment
    if special_records:
        data['special_records'] = special_records
    return data


def parse_tiliote_statements(content: str, filename: str) -> list:
    lines = content.split('\n')
    nlines = len(lines)

    line_number = 1
    tz = timezone('Europe/Helsinki')

    header = None
    records = []
    balance = None
    cumulative = None
    cumulative_adjustment = None
    special_records = []
    statements = []

    while line_number <= nlines:
        line = lines[line_number-1]
        if line.strip() == '':
            line_number += 1
            continue
        # print('parsing line {}: {}'.format(line_number, line))
        record_type = line[:3]

        if record_type in TO_FILE_HEADER_TYPES:
            if header:
                statements.append(combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records))
                header, records, balance, cumulative, cumulative_adjustment, special_records = None, [], None, None, None, []

            header = parse_records(lines[line_number - 1], TO_FILE_HEADER, line_number=line_number)
            convert_date_fields(header, TO_FILE_HEADER_DATES, tz)
            convert_decimal_fields(header, TO_FILE_HEADER_DECIMALS)
            iban_and_bic = header.get('iban_and_bic', '').split(' ')
            if len(iban_and_bic) == 2:
                header['iban'], header['bic'] = iban_and_bic
            line_number += 1

        elif record_type in TO_FILE_RECORD_TYPES:
            record = parse_records(line, TO_FILE_RECORD, line_number=line_number)
            convert_date_fields(record, TO_FILE_RECORD_DATES, tz)
            convert_decimal_fields(record, TO_FILE_RECORD_DECIMALS)

            line_number += 1
            # check for record extra info
            if line_number <= nlines:
                line = lines[line_number-1]
                while line[:3] in TO_FILE_RECORD_EXTRA_INFO_TYPES:
                    parse_record_extra_info(record, line, line_number=line_number)
                    line_number += 1
                    line = lines[line_number-1]

            records.append(record)
        elif record_type == 'T40':
            balance = parse_records(line, TO_BALANCE_RECORD, line_number=line_number)
            convert_date_fields(balance, TO_BALANCE_RECORD_DATES, tz)
            convert_decimal_fields(balance, TO_BALANCE_RECORD_DECIMALS)
            line_number += 1
        elif record_type == 'T50':
            cumulative = parse_records(line, TO_CUMULATIVE_RECORD, line_number=line_number)
            convert_date_fields(cumulative, TO_CUMULATIVE_RECORD_DATES, tz)
            convert_decimal_fields(cumulative, TO_CUMULATIVE_RECORD_DECIMALS)
            line_number += 1
        elif record_type == 'T51':
            cumulative_adjustment = parse_records(line, TO_CUMULATIVE_RECORD, line_number=line_number)
            convert_date_fields(cumulative_adjustment, TO_CUMULATIVE_RECORD_DATES, tz)
            convert_decimal_fields(cumulative_adjustment, TO_CUMULATIVE_RECORD_DECIMALS)
            line_number += 1
        elif record_type == 'T60' or record_type == 'T70':
            special_records.append(parse_records(line, TO_SPECIAL_RECORD, line_number=line_number, check_record_length=False))
            line_number += 1
        else:
            raise ValidationError(_('Unknown record type on {}({}): {}').format(filename, line_number, record_type))

    statements.append(combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records))
    return statements


def parse_filename_suffix(filename: str) -> str:
    a = filename.rsplit('.', 1)
    return a[len(a)-1]


def parse_tiliote_statements_from_file(filename: str) -> dict:
    if parse_filename_suffix(filename).upper() not in ('TO', 'TXT'):
        raise ValidationError(_('Not "tiliote" (.TO) file') + ': {}'.format(filename))
    with open(filename, 'rt', encoding='ISO-8859-1') as fp:
        return parse_tiliote_statements(fp.read(), filename=basename(filename))


def combine_svm_batch(header: dict, records: list, summary: dict) -> dict:
    data = {'header': header, 'records': records}
    if summary is not None:
        data['summary'] = summary
    return data


def parse_svm_batches(content: str, filename: str) -> list:
    lines = content.split('\n')
    nlines = len(lines)

    line_number = 1
    tz = timezone('Europe/Helsinki')

    batches = []
    header = None
    summary = None
    records = []

    while line_number <= nlines:
        line = lines[line_number-1]
        if line.strip() == '':
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
            raise ValidationError(_('Unknown record type on {}({}): {}').format(filename, line_number, record_type))

    batches.append(combine_svm_batch(header, records, summary))
    return batches


def parse_svm_batches_from_file(filename: str) -> dict:
    if parse_filename_suffix(filename).upper() not in ('SVM', 'TXT', 'KTL'):
        raise ValidationError(_('Not "saapuvat viitemaksut" (.SVM) file') + ': {}'.format(filename))
    with open(filename, 'rt', encoding='ISO-8859-1') as fp:
        return parse_svm_batches(fp.read(), filename=basename(filename))
