import logging
import re
from datetime import time, datetime, date
from decimal import Decimal
from typing import Any, Tuple, Optional, Dict, Sequence, Union
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

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
                _("Line {line}: Record length ({record_length}) does not match length of " 'parsed data ({data_length}). Extra data: "{extra_data}"').format(
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
                data[k_date] = tz.localize(v_datetime)
                del data[k_time]
        # logger.debug('%s = %s (%s)', k, data.get(k), type(data.get(k)))


def convert_decimal_fields(data: dict, decimal_fields: Sequence[Union[Tuple[str, str], str]], neg_sign_val: str = "-"):
    for field in decimal_fields:
        if isinstance(field, str):
            v_number = data.get(field)
            if v_number is not None:
                v = Decimal(v_number) * Decimal("0.01")
                # logger.info('jbank.parsers.convert_decimal_fields: {} = {}'.format(field, v))
                data[field] = v
        elif isinstance(field, tuple) and len(field) == 2:
            k_number, k_sign = field
            v_number, v_sign = data.get(k_number), data.get(k_sign)
            if v_number is not None:
                v = Decimal(v_number) * Decimal("0.01")
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
