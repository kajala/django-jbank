from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from django.core.exceptions import ValidationError
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


def get_currency_exchange(
    target_currency: str, record_date: date, source_currency: str = "EUR", unit_currency: str = "EUR", max_age_days: int = 7
) -> CurrencyExchange:
    """
    Returns max week old CurrencyExchange object matching specified search criteria.
    Raises exception if suitable CurrencyExchange not found.

    Args:
        target_currency: Target currency
        record_date: Record date
        source_currency: Source currency. Default "EUR".
        unit_currency: Unit currency. Default "EUR".
        max_age_days: Maximum age (days) from record_date for CurrencyExchange object to be considered valid.

    Returns:
        CurrencyExchange best matching search terms.
    """
    qs = CurrencyExchange.objects.filter(
        target_currency=target_currency,
        source_currency=source_currency,
        unit_currency=unit_currency,
        record_date__gte=record_date - timedelta(days=max_age_days),
        record_date__lte=record_date,
    )
    xchg = qs.order_by("-record_date").first()
    if xchg is None:
        raise ValidationError(_("No exchange rate for {} found for record date {}").format(target_currency, record_date))
    assert isinstance(xchg, CurrencyExchange)
    return xchg


def convert_currency(source_amount: Decimal, source_currency: str, target_currency: str, record_date: Optional[date], unit_currency: str = "EUR") -> Decimal:
    """
    Converts currency from one to another.

    Args:
        source_amount: Amount in source currency
        source_currency: Source currency
        target_currency: Target currency
        record_date: Optional record date. Default today.
        unit_currency: Unit currency which is used for fetching related rates. Default "EUR".

    Returns:
        Amount in target_currency, 6 decimals.
    """
    source_currency = source_currency.upper()
    target_currency = target_currency.upper()
    unit_currency = unit_currency.upper()
    if record_date is None:
        record_date = now().date()
    amt = source_amount

    # calculate source amount in unit currency (default: EUR)
    if source_currency != unit_currency:
        xchg = get_currency_exchange(source_currency, record_date, unit_currency, unit_currency)
        amt /= xchg.exchange_rate

    # calculate target amount in target currency
    if target_currency != unit_currency:
        xchg = get_currency_exchange(target_currency, record_date, unit_currency, unit_currency)
        amt *= xchg.exchange_rate

    return amt.quantize(Decimal("1.000000"))
