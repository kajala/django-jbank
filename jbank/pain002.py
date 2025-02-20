from datetime import datetime
from os.path import basename
from typing import Optional
from django.utils.timezone import now
from jbank.helpers import logger
from jbank.models import Payout, PayoutStatus, PAYOUT_PAID
from jbank.sepa import Pain002
from jutil.format import strip_media_root


def process_pain002_file_content(bcontent: bytes, filename: str, created: Optional[datetime] = None) -> Pain002:
    if not created:
        created = now()
    ps_file = Pain002(bcontent)
    for s in ps_file.payment_states:
        p = Payout.objects.filter(msg_id=s.original_payment_info_id).first()
        ps = PayoutStatus(
            payout=p,
            file_name=basename(filename),
            file_path=strip_media_root(filename),
            msg_id=ps_file.msg_id,
            original_msg_id=ps_file.original_msg_id,
            group_status=s.group_status,
            status_reason=s.status_reason[:255],
            created=created,
            timestamp=ps_file.credit_datetime,
        )
        ps.full_clean()
        fields = (
            "payout",
            "file_name",
            "response_code",
            "response_text",
            "msg_id",
            "original_msg_id",
            "group_status",
            "status_reason",
        )
        params = {}
        for k in fields:
            params[k] = getattr(ps, k)
        ps_old = PayoutStatus.objects.filter(**params).first()
        if ps_old:
            ps = ps_old
        else:
            ps.save()
            logger.info("%s status updated %s", p, ps)
        if p is not None and ps.is_accepted and (p.paid_date is None or p.state != PAYOUT_PAID):
            p.state = PAYOUT_PAID
            p.paid_date = ps_file.credit_datetime
            p.save(update_fields=["state", "paid_date"])
            logger.info("%s marked as paid %s", p, ps)
    return ps_file
