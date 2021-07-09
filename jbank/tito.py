from copy import copy
from os.path import basename
from typing import Dict, Any, List
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from pytz import timezone
from jbank.parsers import parse_filename_suffix, parse_records, convert_date_fields, convert_decimal_fields

TO_STATEMENT_SUFFIXES = ("TO", "TXT", "TITO")

TO_FILE_HEADER_TYPES = ("T00",)

TO_FILE_HEADER_DATES = (
    "begin_date",
    "end_date",
    ("record_date", "record_time"),
    "begin_balance_date",
)

TO_FILE_HEADER_DECIMALS = (
    ("begin_balance", "begin_balance_sign"),
    "account_limit",
)

TO_FILE_HEADER = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("version", "X(3)", "P"),
    ("account_number", "X(14)", "P"),
    ("statement_number", "9(3)", "P"),
    ("begin_date", "9(6)", "P"),
    ("end_date", "9(6)", "P"),
    ("record_date", "9(6)", "P"),
    ("record_time", "9(4)", "P"),
    ("customer_identifier", "X(17)", "P"),
    ("begin_balance_date", "9(6)", "P"),
    ("begin_balance_sign", "X", "P"),
    ("begin_balance", "9(18)", "P"),
    ("record_count", "9(6)", "P"),
    ("currency_code", "X(3)", "P"),
    ("account_name", "X(30)", "V"),
    ("account_limit", "9(18)", "P"),
    ("owner_name", "X(35)", "P"),
    ("contact_info_1", "X(40)", "P"),
    ("contact_info_2", "X(40)", "V"),
    ("bank_specific_info_1", "X(30)", "V"),
    ("iban_and_bic", "X(30)", "V"),
)

TO_FILE_RECORD_TYPES = ("T10", "T80")

TO_FILE_RECORD_DATES = (
    "record_date",
    "value_date",
    "paid_date",
)

TO_FILE_RECORD_DECIMALS = (("amount", "amount_sign"),)

TO_FILE_RECORD = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("record_number", "9(6)", "P"),
    ("archive_identifier", "X(18)", "V"),
    ("record_date", "9(6)", "P"),
    ("value_date", "9(6)", "V"),
    ("paid_date", "9(6)", "V"),
    ("entry_type", "X", "P"),  # 1 = pano, 2 = otto, 3 = panon korjaus, 4 = oton korjaus, 9 = hylätty tapahtuma
    ("record_code", "X(3)", "P"),
    ("record_description", "X(35)", "P"),
    ("amount_sign", "X", "P"),
    ("amount", "9(18)", "P"),
    ("receipt_code", "X", "P"),
    ("delivery_method", "X", "P"),
    ("name", "X(35)", "V"),
    ("name_source", "X", "V"),
    ("recipient_account_number", "X(14)", "V"),
    ("recipient_account_number_changed", "X", "V"),
    ("remittance_info", "X(20)", "V"),
    ("form_number", "X(8)", "V"),
    ("level_identifier", "X", "P"),
)

TO_FILE_RECORD_EXTRA_INFO_TYPES = ("T11", "T81")

TO_FILE_RECORD_EXTRA_INFO_HEADER = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("extra_info_type", "9(2)", "P"),
)

TO_FILE_RECORD_EXTRA_INFO_COUNTS = (("entry_count", "9(8)", "P"),)

TO_FILE_RECORD_EXTRA_INFO_INVOICE = (
    ("customer_number", "X(10)", "P"),
    ("pad01", "X", "P"),
    ("invoice_number", "X(15)", "P"),
    ("pad02", "X", "P"),
    ("invoice_date", "X(6)", "P"),
)

TO_FILE_RECORD_EXTRA_INFO_CARD = (
    ("card_number", "X(19)", "P"),
    ("pad01", "X", "P"),
    ("merchant_reference", "X(14)", "P"),
)

TO_FILE_RECORD_EXTRA_INFO_CORRECTION = (("original_archive_identifier", "X(18)", "P"),)

TO_FILE_RECORD_EXTRA_INFO_CURRENCY_DECIMALS = (("amount", "amount_sign"),)

TO_FILE_RECORD_EXTRA_INFO_CURRENCY = (
    ("amount_sign", "X", "P"),
    ("amount", "9(18)", "P"),
    ("pad01", "X", "P"),
    ("currency_code", "X(3)", "P"),
    ("pad02", "X", "P"),
    ("exchange_rate", "9(11)", "P"),
    ("rate_reference", "X(6)", "V"),
)

TO_FILE_RECORD_EXTRA_INFO_REASON = (
    ("reason_code", "9(3)", "P"),
    ("pad01", "X", "P"),
    ("reason_description", "X(31)", "P"),
)

TO_FILE_RECORD_EXTRA_INFO_NAME_DETAIL = (("name_detail", "X(35)", "P"),)

TO_FILE_RECORD_EXTRA_INFO_SEPA = (
    ("reference", "X(35)", "V"),
    ("iban_account_number", "X(35)", "V"),
    ("bic_code", "X(35)", "V"),
    ("recipient_name_detail", "X(70)", "V"),
    ("payer_name_detail", "X(70)", "V"),
    ("identifier", "X(35)", "V"),
    ("archive_identifier", "X(35)", "V"),
)

TO_BALANCE_RECORD_DATES = ("record_date",)

TO_BALANCE_RECORD_DECIMALS = (
    ("end_balance", "end_balance_sign"),
    ("available_balance", "available_balance_sign"),
)

TO_BALANCE_RECORD = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("record_date", "9(6)", "P"),
    ("end_balance_sign", "X", "P"),
    ("end_balance", "9(18)", "P"),
    ("available_balance_sign", "X", "P"),
    ("available_balance", "9(18)", "P"),
)

TO_CUMULATIVE_RECORD_DATES = ("period_date",)

TO_CUMULATIVE_RECORD_DECIMALS = (
    ("deposits_amount", "deposits_sign"),
    ("withdrawals_amount", "withdrawals_sign"),
)

TO_CUMULATIVE_RECORD = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("period_identifier", "X", "P"),  # 1=day, 2=term, 3=month, 4=year
    ("period_date", "9(6)", "P"),
    ("deposits_count", "9(8)", "P"),
    ("deposits_sign", "X", "P"),
    ("deposits_amount", "9(18)", "P"),
    ("withdrawals_count", "9(8)", "P"),
    ("withdrawals_sign", "X", "P"),
    ("withdrawals_amount", "9(18)", "P"),
)

TO_SPECIAL_RECORD = (
    ("statement_type", "X", "P"),
    ("record_type", "XX", "P"),
    ("record_length", "9(3)", "P"),
    ("bank_group_identifier", "X(3)", "P"),
)


def parse_tiliote_statements_from_file(filename: str) -> list:
    if parse_filename_suffix(filename).upper() not in TO_STATEMENT_SUFFIXES:
        raise ValidationError(
            _('File {filename} has unrecognized ({suffixes}) suffix for file type "{file_type}"').format(
                filename=filename, suffixes=", ".join(TO_STATEMENT_SUFFIXES), file_type="tiliote"
            )
        )
    with open(filename, "rt", encoding="ISO-8859-1") as fp:
        return parse_tiliote_statements(fp.read(), filename=basename(filename))  # type: ignore


def parse_tiliote_statements(content: str, filename: str) -> List[dict]:  # pylint: disable=too-many-locals
    lines = content.split("\n")
    nlines = len(lines)

    line_number = 1
    tz = timezone("Europe/Helsinki")

    header = None
    records = []
    balance = None
    cumulative = None
    cumulative_adjustment = None
    special_records = []
    statements = []

    while line_number <= nlines:
        line = lines[line_number - 1]
        if line.strip() == "":
            line_number += 1
            continue
        # print('parsing line {}: {}'.format(line_number, line))
        record_type = line[:3]

        if record_type in TO_FILE_HEADER_TYPES:
            if header:
                statements.append(combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records))
                header, records, balance, cumulative, cumulative_adjustment, special_records = (
                    None,
                    [],
                    None,
                    None,
                    None,
                    [],
                )

            header = parse_records(lines[line_number - 1], TO_FILE_HEADER, line_number=line_number)
            convert_date_fields(header, TO_FILE_HEADER_DATES, tz)
            convert_decimal_fields(header, TO_FILE_HEADER_DECIMALS)
            iban_and_bic = str(header.get("iban_and_bic", "")).split(" ")
            if len(iban_and_bic) == 2:
                header["iban"], header["bic"] = iban_and_bic
            line_number += 1

        elif record_type in TO_FILE_RECORD_TYPES:
            record = parse_records(line, TO_FILE_RECORD, line_number=line_number)
            convert_date_fields(record, TO_FILE_RECORD_DATES, tz)
            convert_decimal_fields(record, TO_FILE_RECORD_DECIMALS)

            line_number += 1
            # check for record extra info
            if line_number <= nlines:
                line = lines[line_number - 1]
                while line[:3] in TO_FILE_RECORD_EXTRA_INFO_TYPES:
                    parse_record_extra_info(record, line, line_number=line_number)
                    line_number += 1
                    line = lines[line_number - 1]

            records.append(record)
        elif record_type == "T40":
            balance = parse_records(line, TO_BALANCE_RECORD, line_number=line_number)
            convert_date_fields(balance, TO_BALANCE_RECORD_DATES, tz)
            convert_decimal_fields(balance, TO_BALANCE_RECORD_DECIMALS)
            line_number += 1
        elif record_type == "T50":
            cumulative = parse_records(line, TO_CUMULATIVE_RECORD, line_number=line_number)
            convert_date_fields(cumulative, TO_CUMULATIVE_RECORD_DATES, tz)
            convert_decimal_fields(cumulative, TO_CUMULATIVE_RECORD_DECIMALS)
            line_number += 1
        elif record_type == "T51":
            cumulative_adjustment = parse_records(line, TO_CUMULATIVE_RECORD, line_number=line_number)
            convert_date_fields(cumulative_adjustment, TO_CUMULATIVE_RECORD_DATES, tz)
            convert_decimal_fields(cumulative_adjustment, TO_CUMULATIVE_RECORD_DECIMALS)
            line_number += 1
        elif record_type in ("T60", "T70"):
            special_records.append(parse_records(line, TO_SPECIAL_RECORD, line_number=line_number, check_record_length=False))
            line_number += 1
        else:
            raise ValidationError(_("Unknown record type on {}({}): {}").format(filename, line_number, record_type))

    statements.append(combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records))
    return statements


def combine_statement(header, records, balance, cumulative, cumulative_adjustment, special_records) -> Dict[str, Any]:  # pylint: disable=too-many-arguments
    data = {
        "header": header,
        "records": records,
    }
    if balance is not None:
        data["balance"] = balance
    if cumulative is not None:
        data["cumulative"] = cumulative
    if cumulative_adjustment is not None:
        data["cumulative_adjustment"] = cumulative_adjustment
    if special_records:
        data["special_records"] = special_records
    return data


def parse_record_extra_info(record: Dict[str, Any], line: str, line_number: int):
    if line[:3] not in TO_FILE_RECORD_EXTRA_INFO_TYPES:
        raise ValidationError("SVM record extra info validation error on line {}".format(line_number))

    header = parse_records(line, TO_FILE_RECORD_EXTRA_INFO_HEADER, line_number, check_record_length=False)
    extra_info_type = header["extra_info_type"]
    # print(line)
    extra_data = copy(header["extra_data"])
    assert isinstance(extra_data, str)
    if extra_info_type == "00":
        record["messages"] = parse_record_messages(extra_data)
    elif extra_info_type == "01":
        record["counts"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_COUNTS, line_number, record_length=8)
    elif extra_info_type == "02":
        record["invoice"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_INVOICE, line_number, record_length=33)
    elif extra_info_type == "03":
        record["card"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CARD, line_number, record_length=34)
    elif extra_info_type == "04":
        record["correction"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CORRECTION, line_number, record_length=18)
    elif extra_info_type == "05":
        record["currency"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_CURRENCY, line_number, record_length=41)
        convert_decimal_fields(record["currency"], TO_FILE_RECORD_EXTRA_INFO_CURRENCY_DECIMALS)
    elif extra_info_type == "06":
        record["client_messages"] = parse_record_messages(extra_data)
    elif extra_info_type == "07":
        record["bank_messages"] = parse_record_messages(extra_data)
    elif extra_info_type == "08":
        record["reason"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_REASON, line_number, record_length=35)
    elif extra_info_type == "09":
        record["name_detail"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_NAME_DETAIL, line_number, record_length=35)
    elif extra_info_type == "11":
        record["sepa"] = parse_records(extra_data, TO_FILE_RECORD_EXTRA_INFO_SEPA, line_number, record_length=323)
    else:
        raise ValidationError(_('Line {line}: Invalid record extra info type "{extra_info_type}"').format(line=line_number, extra_info_type=extra_info_type))


def parse_record_messages(extra_data: str) -> List[str]:
    msg = []
    while extra_data:
        msg.append(extra_data[:35])
        extra_data = extra_data[35:]
    return msg
