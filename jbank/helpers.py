# pylint: disable=c-extension-no-member
import logging
import os
from datetime import date, timedelta, timezone
from typing import Any, Tuple, Optional, List
from django.conf import settings
from django.core.files import File
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jacc.models import Account, AccountType, EntryType
import re
from lxml import etree, objectify  # type: ignore  # pytype: disable=import-error
from jutil.command import get_date_range_by_name
from jutil.parse import parse_datetime
from jutil.format import strip_media_root, is_media_full_path

MESSAGE_STATEMENT_RECORD_FIELDS = ("messages", "client_messages", "bank_messages")

logger = logging.getLogger(__name__)


def get_or_create_bank_account_entry_types() -> List[EntryType]:
    e_type_codes = [
        settings.E_BANK_DEPOSIT,
        settings.E_BANK_WITHDRAW,
        settings.E_BANK_REFERENCE_PAYMENT,
        settings.E_BANK_REFUND,
        settings.E_BANK_PAYOUT,
    ]
    e_types: List[EntryType] = []
    for code in e_type_codes:
        e_type = EntryType.objects.get_or_create(
            code=code,
            defaults={
                "identifier": code,
                "name": code,
                "is_settlement": True,
                "is_payment": code in [settings.E_BANK_DEPOSIT, settings.E_BANK_REFERENCE_PAYMENT],
            },
        )[0]
        e_types.append(e_type)
    return e_types


def get_or_create_bank_account(account_number: str, currency: str = "EUR") -> Account:
    a_type = AccountType.objects.get_or_create(code=settings.ACCOUNT_BANK_ACCOUNT, is_asset=True, defaults={"name": _("bank account")})[0]
    acc, created = Account.objects.get_or_create(name=account_number, type=a_type, currency=currency)
    if created:
        get_or_create_bank_account_entry_types()
    return acc


def make_msg_id() -> str:
    return re.sub(r"[^\d]", "", now().isoformat())[:-4]


def validate_xml(content: bytes, xsd_file_name: str):
    """Validates XML using XSD"""
    schema = etree.XMLSchema(file=xsd_file_name)
    parser = objectify.makeparser(schema=schema)
    objectify.fromstring(content, parser)


def parse_date_or_relative_date(value: str, tz: Any = None) -> Optional[date]:
    try:
        return parse_datetime(value, tz=tz).date()
    except Exception:
        return get_date_range_by_name(value.replace("-", "_"), tz=tz)[0].date()


def parse_start_and_end_date(tz: Any, **options) -> Tuple[Optional[date], Optional[date]]:
    start_date = None
    end_date = None
    time_now = now().astimezone(tz if tz else timezone.utc)
    if options["start_date"]:
        start_date = parse_date_or_relative_date(options["start_date"], tz=tz)
        end_date = time_now.astimezone(tz).date() + timedelta(days=1)
    if options["end_date"]:
        end_date = parse_date_or_relative_date(options["end_date"], tz=tz)
    return start_date, end_date


def save_or_store_media(file: models.FileField, filename: str):
    """Saves FileField filename as relative path if it's under MEDIA_ROOT.
    Otherwise writes file under media root.
    """
    if is_media_full_path(filename):
        file.name = strip_media_root(filename)  # type: ignore
    else:
        with open(filename, "rb") as fp:
            plain_filename = os.path.basename(filename)
            file.save(plain_filename, File(fp))  # type: ignore  # noqa


def limit_filename_length(name: str, max_length: int, hellip: str = "...") -> str:
    if len(name) > max_length:
        parts = name.rsplit(".", 1)
        suffix = parts[-1] if len(parts) > 1 else ""
        max_prefix_len = max(0, max_length - len(suffix) - 1)
        name = parts[0][:max_prefix_len] + hellip + suffix
    return name
