from os.path import basename
from typing import Union, Dict, List, Optional, Any
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from pytz import timezone
from jbank.parsers import parse_filename_suffix, parse_records, convert_date_fields, convert_decimal_fields

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
    tz = timezone("Europe/Helsinki")
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
