from datetime import datetime
from decimal import Decimal
from typing import Optional
from jbank.models import AccountBalance


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
