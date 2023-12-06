import logging
import re
from datetime import time, datetime, date
from decimal import Decimal
from typing import Any, Tuple, Optional, Dict, Sequence, Union, List
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

REGEX_SIMPLE_FIELD = re.compile(r"^(X|9)+$")

REGEX_VARIABLE_FIELD = re.compile(r"^(X|9)\((\d+)\)$")

logger = logging.getLogger(__name__)


def parse_record_format(fmt: str) -> Tuple[str, int]:
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
        raise Exception("Failed to parse data format {}".format(fmt))
    return data_type, data_len


def parse_record_value(data_type, data_len, data, name: str, line_number: int) -> str:
    value = data[:data_len]
    if len(value) != data_len:
        raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
    if data_type == "X":
        pass
    elif data_type == "9":
        charset = "0123456789"
        for ch in value:
            if ch not in charset:
                raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
        # logger.info('jbank.parsers.parse_record_value: {} = {}'.format(name, value))
    else:
        raise ValidationError(_('Line {line}: Invalid field "{field}" value "{value}"').format(line=line_number, field=name, value=value))
    return value


def parse_records(
    line: str,
    specs: Sequence[Tuple[str, str, str]],
    line_number: int,
    check_record_length: bool = True,
    record_length: Optional[int] = None,
) -> Dict[str, Union[int, str]]:
    i = 0
    data: Dict[str, Union[int, str]] = {"line_number": line_number}
    for name, fmt, req in specs:  # pylint: disable=unused-variable
        data_type, data_len = parse_record_format(fmt)
        value = parse_record_value(data_type, data_len, line[i:], name=name, line_number=line_number)
        # print('[{}:{}] {}="{}"'.format(i, i+data_len, name, value))
        data[name] = str(value).strip()
        i += data_len
    data["extra_data"] = line[i:]

    rec_len = data.get("record_length", record_length)
    if check_record_length and rec_len:
        data["extra_data"] = str(data["extra_data"]).strip()
        if i != rec_len and data["extra_data"] != "":
            raise ValidationError(
                _("Line {line}: Record length ({record_length}) does not match length of parsed data ({data_length}). Extra data: {extra_data}").format(
                    line=line_number,
                    data_length=i + len(str(data["extra_data"])),
                    record_length=rec_len,
                    extra_data=data["extra_data"],
                )
            )
    return data


def convert_date(v: Optional[str], field_name: str, date_fmt: str = "YYMMDD") -> date:
    if v is None:
        raise ValidationError(_("Date field missing: {}").format(field_name))
    if len(v) != 6 or v == "000000":
        raise ValidationError(_("Date format error in field {}: {}").format(field_name, v))
    if date_fmt == "YYMMDD":
        year = int(v[0:2]) + 2000
        month = int(v[2:4])
        day = int(v[4:6])
    else:
        raise ValidationError(_("Unsupported date format"))
    return date(year=year, month=month, day=day)


def convert_date_opt(v: Optional[str], field_name: str, date_fmt: str = "YYMMDD") -> Optional[date]:
    if v is None or v == "000000":
        return None
    return convert_date(v, field_name, date_fmt)


def convert_time(v: Optional[str], field_name: str) -> time:
    if v is None:
        raise ValidationError(_("Time field missing: {}").format(field_name))
    if not re.match(r"^\d\d\d\d$", v):
        raise ValidationError(_("Time format error in field {}: {}").format(field_name, v))
    return time(int(v[0:2]), int(v[2:4]))


def convert_date_fields(data: dict, date_fields: Sequence[Union[str, Tuple[str, str]]], tz: Any, date_fmt: str = "YYMMDD"):
    for k in date_fields:
        # logger.debug('%s = %s (%s)', k, data.get(k), type(data.get(k)))
        if isinstance(k, str):
            data[k] = convert_date_opt(data.get(k), k, date_fmt)
        elif isinstance(k, tuple):
            if len(k) != 2:
                raise ValidationError(_("Date format error in field {}").format(k))
            k_date, k_time = k
            v_date, v_time = data.get(k_date), data.get(k_time)
            if v_date or v_time:
                assert v_date is None or isinstance(v_date, str)
                assert v_time is None or isinstance(v_time, str)
                v_date = convert_date(v_date, k_date, date_fmt)
                v_time = convert_time(v_time, k_time)
                v_datetime = datetime.combine(v_date, v_time)
                data[k_date] = v_datetime.replace(tzinfo=tz)
                del data[k_time]
        # logger.debug('%s = %s (%s)', k, data.get(k), type(data.get(k)))


def convert_decimal_fields(data: dict, decimal_fields: Sequence[Union[Tuple[str, str], str]], neg_sign_val: str = "-"):
    for field in decimal_fields:
        if isinstance(field, str):
            v_number = data.get(field)
            if v_number is not None:
                v = Decimal(v_number.replace(",", "")) * Decimal("0.01")
                # logger.info('jbank.parsers.convert_decimal_fields: {} = {}'.format(field, v))
                data[field] = v
        elif isinstance(field, tuple) and len(field) == 2:
            k_number, k_sign = field
            v_number, v_sign = data.get(k_number), data.get(k_sign)
            if v_number is not None:
                v = Decimal(v_number.replace(",", "")) * Decimal("0.01")
                if v_sign == neg_sign_val:
                    v = -v
                data[k_number] = v
                # logger.info('jbank.parsers.convert_decimal_fields: {} = {}'.format(k_number, v))
                del data[k_sign]
        else:
            raise ValidationError(_("Invalid decimal field format: {}").format(field))


def parse_filename_suffix(filename: str) -> str:
    a = filename.rsplit(".", 1)
    return a[len(a) - 1]


def parse_nordea_balance_query(content: str) -> Dict[str, Any]:
    if not content:
        raise Exception("No Nordea balance query content to parse")
    if content[0] != "1":
        raise Exception("Invalid file format (not matching expected Nordea SALDO)")
    SALDO_FIELDS = (
        ("file_format_identifier", "9(1)", "P"),
        ("account_number", "9(14)", "P"),
        ("pad_1", "X(15)", "P"),
        ("balance_sign", "X(1)", "P"),
        ("balance", "9(14)", "P"),
        ("available_balance_sign", "X(1)", "P"),
        ("available_balance", "9(14)", "P"),
        ("record_datetime", "9(6)", "P"),
        ("record_time", "9(4)", "P"),
        ("credit_limit_sign", "X(1)", "P"),
        ("credit_limit", "9(14)", "P"),
        ("currency", "X(3)", "P"),
        ("pad_2", "X(2)", "P"),
    )
    SALDO_DATE_FIELDS = (("record_datetime", "record_time"),)
    SALDO_DECIMAL_FIELDS = (
        ("balance", "balance_sign"),
        ("available_balance", "available_balance_sign"),
        ("credit_limit", "credit_limit_sign"),
    )
    tz = ZoneInfo("Europe/Helsinki")
    lines = content.split("\n")
    for line in lines:
        if line.strip():
            res = parse_records(content, SALDO_FIELDS, line_number=1)
            convert_date_fields(res, SALDO_DATE_FIELDS, tz)
            convert_decimal_fields(res, SALDO_DECIMAL_FIELDS)
            return res
    return {}


def parse_samlink_real_time_statement(content: str) -> Dict[str, Any]:
    if not content:
        raise Exception("No Samlink real time statement (.RA) content to parse")
    RA_HEADER_FIELDS = (
        ("heading", "X(24)", "P"),
        ("currency_unit", "X(1)", "P"),  # "1" == euro
        ("account_number", "9(14)", "P"),
        ("record_date", "9(6)", "P"),
    )
    RA_BALANCE_FIELDS = (
        ("pad_1", "9(1)", "P"),
        ("record_time", "9(4)", "P"),
        ("balance", "X(16)", "P"),
        ("balance_sign", "X(1)", "P"),
        ("available_balance", "X(16)", "P"),
        ("available_balance_sign", "X(1)", "P"),
    )
    RA_TRANSACTION_FIELDS = (
        ("const_1", "X(1)", "P"),
        ("record_date", "9(6)", "P"),
        ("record_number", "X(3)", "P"),
        ("currency_unit", "X(1)", "P"),  # "1" == euro
        ("record_code", "X(3)", "P"),
        ("amount", "X(16)", "P"),
        ("amount_sign", "X(1)", "P"),
        ("remittance_info", "X(20)", "P"),
        ("payer_name", "X(20)", "P"),
        ("record_description", "X(12)", "P"),
    )
    lines = content.split("\n")
    if len(lines) < 3:
        raise Exception("Invalid Samlink real time statement (.RA) content, less than 3 lines")
    tz = ZoneInfo("Europe/Helsinki")
    header = parse_records(lines[0], RA_HEADER_FIELDS, line_number=1)
    convert_date_fields(header, ["record_date"], tz)
    balance = parse_records(lines[1], RA_BALANCE_FIELDS, line_number=2)
    balance["record_time"] = convert_time(balance.get("record_time"), "record_time")  # type: ignore
    convert_decimal_fields(balance, [("available_balance", "available_balance_sign"), ("balance", "balance_sign")])
    records: List[Dict[str, Any]] = []
    for ix, line in enumerate(lines[2:]):
        if line.strip():
            line_number = ix + 3
            record = parse_records(line, RA_TRANSACTION_FIELDS, line_number)
            convert_decimal_fields(record, [("amount", "amount_sign")])
            convert_date_fields(record, ["record_date"], tz)
            records.append(record)
    return {
        **header,
        **balance,
        "record_datetime": datetime.combine(header["record_date"], balance["record_time"], tzinfo=tz),  # type: ignore
        "records": records,
    }
