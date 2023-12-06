from typing import Tuple, List, Optional, Dict, Any, Union
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
import os
from jbank.parsers import parse_filename_suffix, parse_records, convert_date_fields, convert_decimal_fields

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

AEB43_STATEMENT_SUFFIXES = ["TXT", "AEB43"]

DEBIT_REC_TYPE = "1"  # 1=debit, 2=credit

ACCOUNT_HEADER_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 11
    ("entity_key", "X(4)", "P"),
    ("office_key", "X(4)", "P"),
    ("account_number", "X(10)", "P"),
    ("initial_date", "9(6)", "P"),
    ("final_date", "9(6)", "P"),
    ("initial_balance_debit_or_credit_code", "9(1)", "P"),  # 1=debit, 2=credit
    ("initial_balance", "X(14)", "P"),
    ("currency_key", "X(3)", "P"),
    ("information_mode", "X(1)", "P"),
    ("name", "X(26)", "P"),
    ("free", "X(3)", "P"),
]

ACCOUNT_HEADER_DATES = ["initial_date", "final_date"]
ACCOUNT_HEADER_DECIMALS = [("initial_balance", "initial_balance_debit_or_credit_code")]

TRANSACTION_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 22
    ("free", "X(4)", "P"),
    ("origin_office_code", "X(4)", "P"),
    ("transaction_date", "X(6)", "P"),
    ("value_date", "X(6)", "P"),
    ("common_concept", "X(2)", "P"),
    ("own_concept", "X(3)", "P"),
    ("debit_or_credit_code", "X(1)", "P"),  # 1=debit, 2=credit
    ("amount", "X(14)", "P"),  # cents, left-padded with zeros
    ("document_number", "X(10)", "P"),
    ("reference_1", "X(12)", "P"),
    ("reference_2", "X(16)", "P"),
]

TRANSACTION_DATES = ["transaction_date", "value_date"]
TRANSACTION_DECIMALS = [("amount", "debit_or_credit_code")]

CONCEPT_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 23
    ("data_code", "X(2)", "P"),
    ("concept", "X(38)", "P"),
    ("concept", "X(38)", "P"),
]

AMOUNT_EQUIVALENCE_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 24
    ("data_code", "X(2)", "P"),
    ("currency_key_origin", "X(3)", "P"),
    ("amount", "X(14)", "P"),
    ("free", "X(59)", "P"),
]

AMOUNT_EQUIVALENCE_DECIMALS = [("amount", "data_code")]

ACCOUNT_SUMMARY_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 33
    ("entity_key", "X(4)", "P"),
    ("office_key", "X(4)", "P"),
    ("account_number", "X(10)", "P"),
    ("no_of_notes_must", "X(5)", "P"),
    ("total_amount_debits", "X(14)", "P"),
    ("no_of_notes_to_have", "X(5)", "P"),
    ("total_amount_credits", "X(14)", "P"),
    ("final_balance_debit_or_credit_code", "X(1)", "P"),
    ("final_balance", "X(14)", "P"),
    ("currency_code", "X(3)", "P"),
    ("free", "X(4)", "P"),
]

ACCOUNT_SUMMARY_DECIMALS: List[Union[Tuple[str, str], str]] = [
    ("final_balance", "final_balance_debit_or_credit_code"),
    "total_amount_credits",
    "total_amount_debits",
]

END_OF_FILE_RECORD: List[Tuple[str, str, str]] = [
    ("registration_code", "9(2)", "P"),  # 88
    ("nine", "X(18)", "P"),
    ("no_of_records", "X(6)", "P"),
    ("free", "X(54)", "P"),
]


def parse_aeb43_statements_from_file(filename: str) -> list:
    if parse_filename_suffix(filename).upper() not in AEB43_STATEMENT_SUFFIXES:
        raise ValidationError(
            _('File {filename} has unrecognized ({suffixes}) suffix for file type "{file_type}"').format(
                filename=filename, suffixes=", ".join(AEB43_STATEMENT_SUFFIXES), file_type="AEB43"
            )
        )
    with open(filename, "rt", encoding="UTF-8") as fp:
        return parse_aeb43_statements(fp.read(), filename=os.path.basename(filename))  # type: ignore


def parse_aeb43_statements(content: str, filename: str) -> list:  # pylint: disable=too-many-locals,unused-argument
    lines = content.split("\n")
    nlines = len(lines)
    line_number = 0
    tz = ZoneInfo("Europe/Madrid")
    batches: List[dict] = []
    header: Optional[Dict[str, Any]] = None
    records: List[Dict[str, Any]] = []
    summary: Optional[Dict[str, Any]] = None
    eof: Optional[Dict[str, Any]] = None
    rec_count = 0

    while line_number < nlines:
        line_number += 1
        line = lines[line_number - 1]
        if line.strip() == "":
            line_number += 1
            continue
        record_type = line[:2]

        if record_type == "11":
            header = parse_records(lines[line_number - 1], ACCOUNT_HEADER_RECORD, line_number=line_number)
            convert_date_fields(header, ACCOUNT_HEADER_DATES, tz)
            convert_decimal_fields(header, ACCOUNT_HEADER_DECIMALS, DEBIT_REC_TYPE)
            rec_count += 1
        elif record_type == "33":
            summary = parse_records(lines[line_number - 1], ACCOUNT_SUMMARY_RECORD, line_number=line_number)
            convert_decimal_fields(summary, ACCOUNT_SUMMARY_DECIMALS, DEBIT_REC_TYPE)
            batches.append({"header": header, "records": records, "summary": summary})
            records = []
            header = summary = None
            rec_count += 1
        elif record_type == "22":
            tx_rec = parse_records(lines[line_number - 1], TRANSACTION_RECORD, line_number=line_number)
            convert_date_fields(tx_rec, TRANSACTION_DATES, tz)
            convert_decimal_fields(tx_rec, TRANSACTION_DECIMALS, DEBIT_REC_TYPE)
            records.append(tx_rec)
            rec_count += 1
        elif record_type == "23":
            sub_rec = parse_records(lines[line_number - 1], CONCEPT_RECORD, line_number=line_number)
            prev = records[len(records) - 1]
            prev.setdefault("concept_records", [])
            prev["concept_records"].append(sub_rec)  # type: ignore
            rec_count += 1
        elif record_type == "24":
            sub_rec = parse_records(lines[line_number - 1], AMOUNT_EQUIVALENCE_RECORD, line_number=line_number)
            convert_decimal_fields(sub_rec, AMOUNT_EQUIVALENCE_DECIMALS, DEBIT_REC_TYPE)
            prev = records[len(records) - 1]
            prev.setdefault("amount_equivalence_records", [])
            prev["amount_equivalence_records"].append(sub_rec)  # type: ignore
            rec_count += 1
        elif record_type == "88":
            eof = parse_records(lines[line_number - 1], END_OF_FILE_RECORD, line_number=line_number)

    if eof is None:
        raise ValidationError(_("EOF record missing"))
    if int(eof["no_of_records"]) != rec_count:
        raise ValidationError(_("Number of records does not match EOF record"))
    return batches
