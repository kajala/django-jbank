from collections import OrderedDict
from datetime import datetime, timezone, date
from xml.etree import ElementTree as ET
from decimal import Decimal
import pytz
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.utils.translation import ugettext_lazy as _
from jutil.format import dec2
from jutil.parse import parse_datetime
from jutil.validators import iban_filter, iban_validator, iso_payment_reference_validator, \
    fi_payment_reference_validator, ascii_filter
from jutil.xml import dict_to_element, xml_to_dict


PAIN001_REMITTANCE_INFO_MSG = 'M'
PAIN001_REMITTANCE_INFO_OCR = 'O'
PAIN001_REMITTANCE_INFO_OCR_ISO = 'I'

PAIN001_REMITTANCE_INFO_TYPE = (
    (PAIN001_REMITTANCE_INFO_MSG, _('message')),
    (PAIN001_REMITTANCE_INFO_OCR, _('OCR')),
    (PAIN001_REMITTANCE_INFO_OCR_ISO, _('OCR/ISO')),
)

PAIN001_REMITTANCE_INFO_VALUES = [t[0] for t in PAIN001_REMITTANCE_INFO_TYPE]


class Pain001Party(object):
    def __init__(self, name: str, account: str, bic: str, org_id: str='', address_lines: list=list(), country_code: str=''):
        account = iban_filter(account)
        iban_validator(account)
        self.name = name
        self.account = account
        self.bic = bic
        self.org_id = org_id
        self.address_lines = address_lines
        self.country_code = country_code


class Pain001Payment(object):
    def __init__(self, payment_id, creditor: Pain001Party, amount: Decimal, remittance_info: str, remittance_info_type: str, due_date: date):
        self.payment_id = payment_id
        self.creditor = creditor
        self.amount = amount
        self.remittance_info = remittance_info
        self.remittance_info_type = remittance_info_type
        self.due_date = due_date

    def clean(self):
        if not self.remittance_info:
            raise ValidationError(_('pain001.remittance.info.missing'))
        if self.remittance_info_type not in PAIN001_REMITTANCE_INFO_VALUES:
            raise ValidationError(_('pain001.remittance.info.type.invalid'))
        if self.remittance_info_type == PAIN001_REMITTANCE_INFO_MSG:
            if not self.remittance_info:
                raise ValidationError(_('Invalid payment reference: {}').format(self.remittance_info))
        elif self.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR:
            fi_payment_reference_validator(self.remittance_info)
        elif self.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR_ISO:
            iso_payment_reference_validator(self.remittance_info)


class Pain001(object):
    """
    Class for generating pain.001.001.03 SEPA payment XML files.
    """

    pain_element_name = 'CstmrCdtTrfInitn'
    tz_str = 'Europe/Helsinki'
    tz = None

    def __init__(self, msg_id: str,
                 debtor_name: str,
                 debtor_account: str,
                 debtor_bic: str,
                 debtor_org_id: str,
                 debtor_address_lines: list,
                 debtor_country_code: str):
        self.msg_id = msg_id
        self.debtor = Pain001Party(debtor_name, debtor_account, debtor_bic, debtor_org_id, debtor_address_lines, debtor_country_code)
        self.payments = []

    def add_payment(self, payment_id,
                    creditor_name: str,
                    creditor_account: str,
                    creditor_bic: str,
                    amount: Decimal,
                    remittance_info: str,
                    remittance_info_type: str=PAIN001_REMITTANCE_INFO_MSG,
                    due_date: date=None,
                    ):
        if not due_date:
            due_date = self._local_time().date()
        creditor = Pain001Party(creditor_name, creditor_account, creditor_bic)
        p = Pain001Payment(payment_id, creditor, dec2(amount), remittance_info, remittance_info_type, due_date)
        p.clean()
        self.payments.append(p)

    def _ctrl_sum(self) -> Decimal:
        total = Decimal('0.00')
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            total += p.amount
        return total

    def _append_simple(self, parent: ET.Element, tag: str, value):
        e = ET.Element(tag)
        e.text = str(value)
        parent.append(e)
        return e

    def _local_time(self, t: datetime=None) -> datetime:
        if not t:
            t = now()
        if not self.tz:
            self.tz = pytz.timezone(self.tz_str)
        return t.astimezone(self.tz)

    def _timestamp(self, t: datetime) -> str:
        return self._local_time(t).isoformat()

    def _grp_hdr(self) -> ET.Element:
        g = ET.Element('GrpHdr')
        self._append_simple(g, 'MsgId', self.msg_id)
        self._append_simple(g, 'CreDtTm', self._timestamp(now()))
        self._append_simple(g, 'NbOfTxs', len(self.payments))
        self._append_simple(g, 'CtrlSum', self._ctrl_sum())
        # self._append_simple(g, 'BtchBookg', 'true')  # debit all at once
        # self._append_simple(g, 'Grpg', 'MIXD')
        g.append(dict_to_element({
            'InitgPty': OrderedDict([
                ('Nm', self.debtor.name),
                ('PstlAdr', OrderedDict([
                    ('Ctry', self.debtor.country_code),
                    ('AdrLine', [{'@': l} for l in self.debtor.address_lines]),
                ])),
            ]),
        }))
        return g

    def _pmt_inf(self, p: Pain001Payment) -> ET.Element:
        if p.remittance_info_type == PAIN001_REMITTANCE_INFO_MSG:
            rmt_inf = ['RmtInf', OrderedDict([
                ('Ustrd', p.remittance_info),
            ])]
        elif p.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR:
            rmt_inf = ['RmtInf', OrderedDict([
                ('Strd', OrderedDict([
                    ('CdtrRefInf', OrderedDict([
                        ('Tp', OrderedDict([
                            ('CdOrPrtry', OrderedDict([
                                ('Cd', 'SCOR'),
                            ])),
                        ])),
                        ('Ref', p.remittance_info),
                    ])),
                ])),
            ])]
        elif p.remittance_info_type == PAIN001_REMITTANCE_INFO_OCR_ISO:
            rmt_inf = ['RmtInf', OrderedDict([
                ('Strd', OrderedDict([
                    ('CdtrRefInf', OrderedDict([
                        ('Tp', OrderedDict([
                            ('CdOrPrtry', OrderedDict([
                                ('Cd', 'SCOR'),
                            ])),
                            ('Issr', 'ISO'),
                        ])),
                        ('Ref', p.remittance_info),
                    ])),
                ])),
            ])]
        else:
            raise ValidationError(_('Invalid remittance info type: {}').format(p.remittance_info_type))

        return dict_to_element({
            'PmtInf': OrderedDict([
                ('PmtInfId', str(p.payment_id)),
                ('PmtMtd', 'TRF'),  # payment order
                ('ReqdExctnDt', p.due_date.isoformat()),
                ('Dbtr', OrderedDict([
                    ('Nm', self.debtor.name),
                    ('PstlAdr', OrderedDict([
                        ('Ctry', self.debtor.country_code),
                        ('AdrLine', [{'@': l} for l in self.debtor.address_lines]),
                    ])),
                    ('Id', OrderedDict([
                        ('OrgId', OrderedDict([
                            ('Othr', OrderedDict([
                                ('Id', self.debtor.org_id),
                                ('SchmeNm', OrderedDict([
                                    ('Cd', 'BANK'),
                                ])),
                            ])),
                        ])),
                    ])),
                ])),
                ('DbtrAcct', OrderedDict([
                    ('Id', OrderedDict([
                        ('IBAN', self.debtor.account),
                    ])),
                ])),
                ('DbtrAgt', OrderedDict([
                    ('FinInstnId', OrderedDict([
                        ('BIC', self.debtor.bic),
                    ])),
                ])),
                ('ChrgBr', 'SLEV'),  # FollowingService level
                ('CdtTrfTxInf', OrderedDict([
                    ('PmtId', OrderedDict([
                        ('EndToEndId', str(p.payment_id)),
                    ])),
                    ('Amt', OrderedDict([
                        ('InstdAmt', {'@': str(p.amount), '@Ccy': 'EUR'}),
                    ])),
                    ('UltmtDbtr', OrderedDict([
                        ('Nm', self.debtor.name),
                    ])),
                    ('CdtrAgt', OrderedDict([
                        ('FinInstnId', OrderedDict([
                            ('BIC', p.creditor.bic),
                        ])),
                    ])),
                    ('Cdtr', OrderedDict([
                        ('Nm', ascii_filter(p.creditor.name)),
                    ])),
                    ('CdtrAcct', OrderedDict([
                        ('Id', OrderedDict([
                            ('IBAN', p.creditor.account),
                        ])),
                    ])),
                    rmt_inf,
                ])),
            ]),
        })

    def render_to_element(self) -> ET.Element:
        if len(self.payments) == 0:
            raise ValidationError('No payments in pain.001.001.03')
        doc = ET.Element('Document', xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03")
        pain = ET.Element(self.pain_element_name)
        doc.append(pain)
        pain.append(self._grp_hdr())
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            pain.append(self._pmt_inf(p))
        return doc

    def render_to_bytes(self, doc: ET.Element or None = None) -> bytes:
        doc = doc or self.render_to_element()
        xml_bytes = ET.tostring(doc, encoding='utf-8', method='xml')
        return xml_bytes

    def render_to_file(self, filename: str, xml_bytes: bytes or None = None):
        xml_bytes = xml_bytes or self.render_to_bytes()
        with open(filename, 'wb') as fp:
            fp.write(xml_bytes)


class Pain002(object):
    """
    Class for parsing pain.002.001.03 SEPA payment status XML files.
    """
    credit_datetime = None
    msg_id = None
    original_msg_id = None
    group_status = None
    status_reason = None

    def __init__(self, file_content: bytes):
        self.data = xml_to_dict(file_content)

        rpt = self.data.get('CstmrPmtStsRpt', {})

        grp_hdr = rpt.get('GrpHdr', {})
        credit_datetime_str = grp_hdr.get('CreDtTm')
        self.credit_datetime = parse_datetime(credit_datetime_str)
        self.msg_id = grp_hdr.get('MsgId')

        grp_inf = rpt.get('OrgnlGrpInfAndSts', {})
        self.original_msg_id = grp_inf.get('OrgnlMsgId')
        self.group_status = grp_inf.get('GrpSts')
        self.status_reason = grp_inf.get('StsRsnInf', {}).get('Rsn', {}).get('Prtry', '')

        if not self.msg_id:
            raise ValidationError('MsgId missing')
        if not self.original_msg_id:
            raise ValidationError('OrgnlMsgId missing')
        if not self.group_status:
            raise ValidationError('GrpSts missing')

    def __str__(self):
        return '{}: {} {} {}'.format(self.msg_id, self.original_msg_id, self.group_status, self.status_reason)

    @property
    def is_accepted(self):
        return self.group_status == 'ACCP'

    @property
    def is_rejected(self):
        return self.group_status == 'RJCT'
