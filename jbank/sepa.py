# pylint: disable=too-many-arguments
import sys
from collections import OrderedDict
from datetime import datetime, date
from typing import Optional, List, Sequence, Union, Any, Dict, Tuple
from xml.etree import ElementTree as ET  # noqa
from xml.etree.ElementTree import Element
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jutil.format import dec2
from jutil.parse import parse_datetime
from jutil.validators import (
    iban_filter,
    iban_validator,
    iso_payment_reference_validator,
    fi_payment_reference_validator,
    ascii_filter,
    country_code_validator,
    bic_validator,
    iban_bank_info,
)
from jutil.xml import xml_to_dict, _xml_element_set_data_r

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo


PAIN001_REMITTANCE_INFO_MSG = "M"
PAIN001_REMITTANCE_INFO_OCR = "O"
PAIN001_REMITTANCE_INFO_OCR_ISO = "I"

PAIN001_REMITTANCE_INFO_TYPE = (
    (PAIN001_REMITTANCE_INFO_MSG, _("message")),
    (PAIN001_REMITTANCE_INFO_OCR, _("OCR")),
    (PAIN001_REMITTANCE_INFO_OCR_ISO, _("OCR/ISO")),
)

PAIN001_REMITTANCE_INFO_VALUES = [t[0] for t in PAIN001_REMITTANCE_INFO_TYPE]


class Pain001Party:
    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        name: str,
        account: str,
        bic: str,
        org_id: str = "",
        address_lines: Optional[Sequence[str]] = None,
        country_code: str = "",
    ):
        if address_lines is None:
            address_lines = []
        account = iban_filter(account)
        iban_validator(account)
        self.name = name
        self.account = account
        self.bic = bic
        self.org_id = org_id
        self.address_lines = address_lines
        self.country_code = country_code

    def get_bic(self) -> str:
        """
        Returns bank BIC code.
        If BIC is set explicitly, it is returned as is. Otherwise, resolving is tried from IBAN account number.
        If BIC cannot be resolved ValidationError is raised.
        Returns: str BIC code
        """
        if self.bic:
            return self.bic
        bic = iban_bank_info(self.account)[0]
        if not bic:
            raise ValidationError(_("BIC missing"))
        return bic


class Pain001Payment:
    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        payment_id: Union[str, int],
        creditor: Pain001Party,
        amount: Decimal,
        remittance_info: str,
        remittance_info_type: str,
        due_date: date,
        end_to_end_id: str,
    ):
        self.payment_id = payment_id
        self.end_to_end_id = end_to_end_id
        self.creditor = creditor
        self.amount = amount
        self.remittance_info = remittance_info
        self.remittance_info_type = remittance_info_type
        self.due_date = due_date

    def clean(self):
        if not self.remittance_info:
            raise ValidationError(_("pain001.remittance.info.missing"))
        if self.remittance_info_type not in PAIN001_REMITTANCE_INFO_VALUES:
            raise ValidationError(_("pain001.remittance.info.type.invalid"))
        if self.remittance_info_type == PAIN001_REMITTANCE_INFO_MSG:
            if not self.remittance_info:
                raise ValidationError(_("Invalid payment reference: {}").format(self.remittance_info))
        elif self.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR:
            fi_payment_reference_validator(self.remittance_info)
        elif self.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR_ISO:
            iso_payment_reference_validator(self.remittance_info)


class Pain001:
    """Class for generating pain.001.001.03 SEPA payment XML files."""

    pain_element_name = "CstmrCdtTrfInitn"
    tz_str = "Europe/Helsinki"
    tz: Any = None
    xml_declaration: Any = None

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        msg_id: str,
        debtor_name: str,
        debtor_account: str,
        debtor_bic: str,
        debtor_org_id: str,
        debtor_address_lines: Sequence[str],
        debtor_country_code: str,
    ):
        if not debtor_org_id or len(debtor_org_id) < 5:
            raise ValidationError({"debtor_org_id": _("invalid value")})
        if not debtor_name or len(debtor_name) < 2:
            raise ValidationError({"debtor_name": _("invalid value")})
        if not debtor_address_lines:
            raise ValidationError({"debtor_address_lines": _("invalid value")})
        bic_validator(debtor_bic)
        country_code_validator(debtor_country_code)
        iban_validator(debtor_account)

        self.msg_id = msg_id
        self.debtor = Pain001Party(debtor_name, debtor_account, debtor_bic, debtor_org_id, debtor_address_lines, debtor_country_code)
        self.payments: List[Pain001Payment] = []

    def add_payment(  # pylint: disable=too-many-positional-arguments
        self,
        payment_id: str,
        creditor_name: str,
        creditor_account: str,
        creditor_bic: str,
        amount: Decimal,
        remittance_info: str,
        remittance_info_type: str = PAIN001_REMITTANCE_INFO_MSG,
        due_date: Optional[date] = None,
        end_to_end_id: str = "",
    ):
        if not due_date:
            due_date = self.local_time().date()
        creditor = Pain001Party(creditor_name, creditor_account, creditor_bic)
        p = Pain001Payment(payment_id, creditor, dec2(amount), remittance_info, remittance_info_type, due_date, end_to_end_id)
        p.clean()
        if not end_to_end_id and self.payments:
            raise ValidationError(_("Adding multiple payments to single pain.001 file requires end-to-end identifier for the payments"))
        self.payments.append(p)

    def _ctrl_sum(self) -> Decimal:
        total = Decimal("0.00")
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            total += p.amount
        return total

    def _append_simple(self, parent: Element, tag: str, value):
        e = Element(tag)
        e.text = str(value)
        parent.append(e)
        return e

    def local_time(self, t: Optional[datetime] = None) -> datetime:
        if not t:
            t = now()
        if not self.tz:
            self.tz = ZoneInfo(self.tz_str)
        return t.astimezone(self.tz)

    def _timestamp(self, t: datetime) -> str:
        return self.local_time(t).isoformat()

    @staticmethod
    def _dict_to_element(doc: Dict[str, Any], value_key: str = "@", attribute_prefix: str = "@") -> Element:
        if len(doc) != 1:
            raise Exception("Invalid data dict for XML generation, document root must have single element")
        for tag, data in doc.items():
            el = Element(tag)
            assert isinstance(el, Element)
            _xml_element_set_data_r(el, data, value_key, attribute_prefix)
            return el  # pytype: disable=bad-return-type
        return Element("empty")

    def _grp_hdr(self) -> Element:
        g = Element("GrpHdr")
        self._append_simple(g, "MsgId", self.msg_id)
        self._append_simple(g, "CreDtTm", self._timestamp(now()))
        self._append_simple(g, "NbOfTxs", len(self.payments))
        self._append_simple(g, "CtrlSum", self._ctrl_sum())
        # self._append_simple(g, 'BtchBookg', 'true')  # debit all at once
        # self._append_simple(g, 'Grpg', 'MIXD')
        g.append(
            self._dict_to_element(
                {
                    "InitgPty": OrderedDict(
                        [
                            ("Nm", self.debtor.name),
                            (
                                "PstlAdr",
                                OrderedDict(
                                    [
                                        ("Ctry", self.debtor.country_code),
                                        ("AdrLine", [{"@": al} for al in self.debtor.address_lines]),
                                    ]
                                ),
                            ),
                        ]
                    ),
                }
            )
        )
        return g

    def _pmt_inf(self, payment_list: List[Pain001Payment]) -> Element:
        if not payment_list:
            raise ValidationError("PmtInf requires a list of payments")
        payment_id = ""
        due_date: Optional[date] = None
        for payment in payment_list:
            if not payment_id:
                payment_id = payment.payment_id
                due_date = payment.due_date
                continue
            if payment.payment_id != payment_id:
                raise ValidationError("All payments in PmtInf element must have identical PmtInfId")
            due_date = min(due_date, payment.due_date)
        assert isinstance(due_date, date)

        return self._dict_to_element(
            {
                "PmtInf": OrderedDict(
                    [
                        ("PmtInfId", str(payment_id)),
                        ("PmtMtd", "TRF"),  # payment order
                        ("ReqdExctnDt", due_date.isoformat()),
                        (
                            "Dbtr",
                            OrderedDict(
                                [
                                    ("Nm", self.debtor.name),
                                    (
                                        "PstlAdr",
                                        OrderedDict(
                                            [
                                                ("Ctry", self.debtor.country_code),
                                                ("AdrLine", [{"@": al} for al in self.debtor.address_lines]),
                                            ]
                                        ),
                                    ),
                                    (
                                        "Id",
                                        OrderedDict(
                                            [
                                                (
                                                    "OrgId",
                                                    OrderedDict(
                                                        [
                                                            (
                                                                "Othr",
                                                                OrderedDict(
                                                                    [
                                                                        ("Id", self.debtor.org_id),
                                                                        (
                                                                            "SchmeNm",
                                                                            OrderedDict(
                                                                                [
                                                                                    ("Cd", "BANK"),
                                                                                ]
                                                                            ),
                                                                        ),
                                                                    ]
                                                                ),
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "DbtrAcct",
                            OrderedDict(
                                [
                                    (
                                        "Id",
                                        OrderedDict(
                                            [
                                                ("IBAN", self.debtor.account),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "DbtrAgt",
                            OrderedDict(
                                [
                                    (
                                        "FinInstnId",
                                        OrderedDict(
                                            [
                                                ("BIC", self.debtor.get_bic()),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                        ("ChrgBr", "SLEV"),  # FollowingService level
                        ("CdtTrfTxInf", [self._cdt_trf_tx_inf(p) for p in payment_list]),
                    ]
                ),
            }
        )

    def _cdt_trf_tx_inf(self, p: Pain001Payment) -> OrderedDict:
        rmt_inf: Tuple[str, Any]
        if p.remittance_info_type == PAIN001_REMITTANCE_INFO_MSG:
            rmt_inf = (
                "RmtInf",
                OrderedDict(
                    [
                        ("Ustrd", p.remittance_info),
                    ]
                ),
            )
        elif p.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR:
            rmt_inf = (
                "RmtInf",
                OrderedDict(
                    [
                        (
                            "Strd",
                            OrderedDict(
                                [
                                    (
                                        "CdtrRefInf",
                                        OrderedDict(
                                            [
                                                (
                                                    "Tp",
                                                    OrderedDict(
                                                        [
                                                            (
                                                                "CdOrPrtry",
                                                                OrderedDict(
                                                                    [
                                                                        ("Cd", "SCOR"),
                                                                    ]
                                                                ),
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                                ("Ref", p.remittance_info),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                    ]
                ),
            )
        elif p.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR_ISO:
            rmt_inf = (
                "RmtInf",
                OrderedDict(
                    [
                        (
                            "Strd",
                            OrderedDict(
                                [
                                    (
                                        "CdtrRefInf",
                                        OrderedDict(
                                            [
                                                (
                                                    "Tp",
                                                    OrderedDict(
                                                        [
                                                            (
                                                                "CdOrPrtry",
                                                                OrderedDict(
                                                                    [
                                                                        ("Cd", "SCOR"),
                                                                    ]
                                                                ),
                                                            ),
                                                            ("Issr", "ISO"),
                                                        ]
                                                    ),
                                                ),
                                                ("Ref", p.remittance_info),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                    ]
                ),
            )
        else:
            raise ValidationError(_("Invalid remittance info type: {}").format(p.remittance_info_type))

        payload = OrderedDict(
            [
                (
                    "PmtId",
                    OrderedDict(
                        [
                            ("EndToEndId", str(p.end_to_end_id or p.payment_id)),
                        ]
                    ),
                ),
                (
                    "Amt",
                    OrderedDict(
                        [
                            ("InstdAmt", {"@": str(p.amount), "@Ccy": "EUR"}),
                        ]
                    ),
                ),
                (
                    "UltmtDbtr",
                    OrderedDict(
                        [
                            ("Nm", self.debtor.name),
                        ]
                    ),
                ),
                (
                    "CdtrAgt",
                    OrderedDict(
                        [
                            (
                                "FinInstnId",
                                OrderedDict(
                                    [
                                        ("BIC", p.creditor.get_bic()),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                (
                    "Cdtr",
                    OrderedDict(
                        [
                            ("Nm", ascii_filter(p.creditor.name)),
                        ]
                    ),
                ),
                (
                    "CdtrAcct",
                    OrderedDict(
                        [
                            (
                                "Id",
                                OrderedDict(
                                    [
                                        ("IBAN", p.creditor.account),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                rmt_inf,
            ]
        )
        return payload

    def render_to_element(self) -> Element:
        if not self.payments:
            raise ValidationError("No payments in pain.001.001.03")
        doc = Element("Document", xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03")
        pain = Element(self.pain_element_name)
        doc.append(pain)
        pain.append(self._grp_hdr())
        payments_by_payment_id: Dict[str, list] = {}
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            payment_id = str(p.payment_id)
            payments_by_payment_id.setdefault(payment_id, [])
            payments_by_payment_id[payment_id].append(p)
        for payment_id, payment_list in payments_by_payment_id.items():
            pain.append(self._pmt_inf(payment_list))
        return doc

    def render_to_bytes(self, doc: Optional[Element] = None) -> bytes:
        doc = doc or self.render_to_element()
        if sys.version_info.major == 3 and sys.version_info.minor < 8:
            xml_bytes = ET.tostring(doc, encoding="utf-8", method="xml")
        else:
            xml_bytes = ET.tostring(doc, encoding="utf-8", method="xml", xml_declaration=self.xml_declaration)
        return xml_bytes

    def render_to_file(self, filename: str, xml_bytes: Optional[bytes] = None):
        xml_bytes = xml_bytes or self.render_to_bytes()
        with open(filename, "wb") as fp:
            fp.write(xml_bytes)


class Pain002PaymentState:
    original_payment_info_id: str = ""
    original_end_to_end_id: str = ""
    group_status: str = ""
    status_reason: str = ""

    @property
    def is_accepted(self):
        return self.group_status in ["ACCP", "ACSC", "ACSP"]

    @property
    def is_technically_accepted(self):
        return self.group_status == "ACTC"

    @property
    def is_accepted_with_change(self):
        return self.group_status == "ACWC"

    @property
    def is_partially_accepted(self):
        return self.group_status == "PART"

    @property
    def is_pending(self):
        return self.group_status == "PDNG"

    @property
    def is_rejected(self):
        return self.group_status == "RJCT"


class Pain002:
    """Class for parsing pain.002.001.03 SEPA payment status XML files."""

    data: dict
    credit_datetime: datetime
    msg_id: str = ""
    original_msg_id: str = ""
    group_status: str = ""
    number_of_txs: int = 0
    payment_states: List[Pain002PaymentState]

    def __init__(self, file_content: bytes):
        self.data = xml_to_dict(file_content, array_tags=["StsRsnInf", "OrgnlPmtInfAndSts", "TxInfAndSts", "NbOfTxsPerSts"])

        rpt = self.data.get("CstmrPmtStsRpt", {})
        grp_hdr = rpt.get("GrpHdr", {})
        credit_datetime = parse_datetime(grp_hdr.get("CreDtTm"))
        if credit_datetime is None:
            raise ValidationError("CreDtTm missing")
        assert isinstance(credit_datetime, datetime)
        self.credit_datetime = credit_datetime
        self.msg_id = grp_hdr.get("MsgId")
        if not self.msg_id:
            raise ValidationError("MsgId missing")

        grp_inf = rpt.get("OrgnlGrpInfAndSts", {})
        self.original_msg_id = grp_inf.get("OrgnlMsgId") or ""
        self.group_status = grp_inf.get("GrpSts") or ""
        self.number_of_txs = int(grp_inf.get("OrgnlNbOfTxs") or 0)

        self.payment_states = []
        pmt_inf_list = rpt.get("OrgnlPmtInfAndSts") or []
        for pmt_inf in pmt_inf_list:
            ps = Pain002PaymentState()
            ps.original_payment_info_id = pmt_inf.get("OrgnlPmtInfId") or ""
            ps.group_status = pmt_inf.get("PmtInfSts") or ""
            ps.status_reason = ""
            for sts_rsn_inf in pmt_inf.get("StsRsnInf") or []:
                if ps.status_reason:
                    ps.status_reason += "\n"
                ps.status_reason += sts_rsn_inf.get("AddtlInf") or ""
            if not ps.original_payment_info_id:
                raise ValidationError("OrgnlPmtInfId missing")
            if not ps.group_status:
                raise ValidationError("PmtInfSts missing")
            self.payment_states.append(ps)
            if ps.group_status == "PART":
                tx_inf_list = pmt_inf.get("TxInfAndSts") or []
                for tx_inf in tx_inf_list:
                    ps_tx = Pain002PaymentState()
                    ps_tx.original_payment_info_id = ""
                    ps_tx.original_end_to_end_id = tx_inf.get("OrgnlEndToEndId") or ""
                    if not ps_tx.original_end_to_end_id:
                        raise ValidationError("OrgnlEndToEndId missing")
                    ps_tx.group_status = tx_inf.get("TxSts") or ""
                    if not ps_tx.group_status:
                        raise ValidationError("TxSts missing")
                    ps_tx.status_reason = ""
                    for sts_rsn_inf in tx_inf.get("StsRsnInf") or []:
                        if ps_tx.status_reason:
                            ps_tx.status_reason += "\n"
                        ps_tx.status_reason += sts_rsn_inf.get("AddtlInf") or ""
                    self.payment_states.append(ps_tx)

    def __str__(self):
        return "{}: {} {}".format(self.msg_id, self.original_msg_id, self.group_status)

    @property
    def is_accepted(self):
        return self.group_status in ["ACCP", "ACSC", "ACSP"]

    @property
    def is_technically_accepted(self):
        return self.group_status == "ACTC"

    @property
    def is_accepted_with_change(self):
        return self.group_status == "ACWC"

    @property
    def is_partially_accepted(self):
        return self.group_status == "PART"

    @property
    def is_pending(self):
        return self.group_status == "PDNG"

    @property
    def is_rejected(self):
        return self.group_status == "RJCT"
