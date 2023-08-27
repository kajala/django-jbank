from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from django.core.exceptions import ValidationError
from django.db.models import QuerySet, Sum, Q, F
from django.utils.timezone import now
from django.utils.translation import gettext as _
from jbank.models import AccountBalance, CurrencyExchange


def create_account_balance(  # pylint: disable=too-many-arguments
    record_datetime: datetime,
    account_number: str,
    bic: str,
    balance: Decimal,
    available_balance: Decimal,
    credit_limit: Optional[Decimal] = None,
    currency: str = "EUR",
    **kwargs  # noqa  # type: ignore
):
    return AccountBalance.objects.get_or_create(
        record_datetime=record_datetime,
        account_number=account_number,
        bic=bic,
        balance=balance,
        available_balance=available_balance,
        credit_limit=credit_limit,
        currency=currency,
    )[0]


def find_currency_exchange(target_currency: str, record_date: date, source_currency: str = "EUR", max_age_days: int = 7) -> Optional[CurrencyExchange]:
    qs = CurrencyExchange.objects.filter(
        target_currency=target_currency,
        source_currency=source_currency,
        unit_currency=source_currency,
        record_date__gte=record_date - timedelta(days=max_age_days),
        record_date__lte=record_date,
    ).exclude(exchange_rate=None)
    return qs.order_by("-record_date").first()


def get_currency_exchange_rate(target_currency: str, record_date: date, source_currency: str = "EUR", max_age_days: int = 7) -> Decimal:
    """
    Returns max week old currency exchange rate matching specified search criteria.
    Raises exception if suitable currency exchange rate not found.

    Args:
        target_currency: Target currency
        record_date: Preferred record date
        source_currency: Source currency. Default "EUR".
        max_age_days: Maximum age (days) from record_date for CurrencyExchange object to be considered valid.

    Returns:
        Decimal currency exchange rate from source currency to target currency.
    """
    xchg = find_currency_exchange(target_currency, record_date, source_currency, max_age_days)
    if xchg is None:
        raise ValidationError(_("No exchange rate for {} found for record date {}").format(target_currency, record_date))
    assert isinstance(xchg, CurrencyExchange)
    rate = xchg.exchange_rate
    assert isinstance(rate, Decimal)
    return rate


def convert_currency(  # pylint: disable=too-many-arguments
    source_amount: Decimal, source_currency: str, target_currency: str, record_date: Optional[date], unit_currency: str = "EUR", max_age_days: int = 7
) -> Decimal:
    """
    Converts currency from one to another.

    Args:
        source_amount: Amount in source currency
        source_currency: Source currency
        target_currency: Target currency
        record_date: Optional preferred record date. Default today.
        unit_currency: Unit currency which is used for fetching related rates. Default "EUR".
        max_age_days: Max age (days) for currency conversion data to be considered valid.

    Returns:
        Amount in target currency
    """
    source_currency = source_currency.upper()
    target_currency = target_currency.upper()
    unit_currency = unit_currency.upper()
    if record_date is None:
        record_date = now().date()
    amt = source_amount

    # calculate source amount in unit currency (default: EUR)
    if source_currency != unit_currency:
        amt /= get_currency_exchange_rate(source_currency, record_date, unit_currency)

    # calculate target amount in target currency
    if target_currency != unit_currency:
        amt *= get_currency_exchange_rate(target_currency, record_date, unit_currency, max_age_days)

    return amt.quantize(Decimal("1.000000"))


def filter_settlements_for_bank_reconciliation(queryset: QuerySet) -> QuerySet:
    """
    Returns settlements which potentially need bank reconciliation:
    1) is original (no parent) settlement type entry, and
    2) is not marked as reconciled, and
    3) sum amount of children is less than the amount

    Args:
        queryset: QuerySet

    Returns:
        QuerySet
    """
    queryset = queryset.filter(type__is_settlement=True, parent=None)  # original (non-derived) settlements only
    queryset = queryset.exclude(marked_reconciled=True)  # ignore entries marked as reconciled
    queryset = queryset.annotate(child_set_amount=Sum("child_set__amount"))  # sum amount of children
    return queryset.filter(Q(child_set=None) | Q(child_set_amount__lt=F("amount")))  # return those which don't have children or children amount not enough
