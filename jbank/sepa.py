from collections import OrderedDict
from datetime import datetime, timezone, date
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from decimal import Decimal
import pytz
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from jutil.format import dec2
from jutil.parse import parse_datetime
from jutil.validators import iban_filter, iban_validator
from jutil.xml import dict_to_element, xml_to_dict


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
    def __init__(self, payment_id, creditor: Pain001Party, amount: Decimal, remittance_info: str, due_date: date):
        self.payment_id = payment_id
        self.creditor = creditor
        self.amount = amount
        self.remittance_info = remittance_info
        self.due_date = due_date


class Pain001(object):
    """
    Class for generating pain.001.001.03 SEPA payment XML files.
    """

    pain_element_name = 'CstmrCdtTrfInitn'
    # pain_element_name = 'pain.001.001.03'
    tz_str = 'Europe/Helsinki'
    tz = None
    msg_id = None
    debtor = None
    payments = []

    def __init__(self, msg_id: str,
                 debtor_name: str,
                 debtor_account: str,
                 debtor_bic: str,
                 debtor_org_id: str,
                 debtor_address_lines: list,
                 debtor_country_code: str):
        self.msg_id = msg_id
        self.debtor = Pain001Party(debtor_name, debtor_account, debtor_bic, debtor_org_id, debtor_address_lines, debtor_country_code)

    def add_payment(self, payment_id,
                    creditor_name: str,
                    creditor_account: str,
                    creditor_bic: str,
                    amount: Decimal,
                    remittance_info: str,
                    due_date: date=None):
        creditor = Pain001Party(creditor_name, creditor_account, creditor_bic)
        if not due_date:
            due_date = self._local_time().date()
        p = Pain001Payment(payment_id, creditor, dec2(amount), remittance_info, due_date)
        self.payments.append(p)

    def _ctrl_sum(self) -> Decimal:
        total = Decimal('0.00')
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            total += p.amount
        return total

    def _append_simple(self, parent: Element, tag: str, value):
        e = Element(tag)
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

    def _grp_hdr(self) -> Element:
        g = Element('GrpHdr')
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

    def _pmt_inf(self, p: Pain001Payment) -> Element:
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
                        ('Nm', p.creditor.name),
                    ])),
                    ('CdtrAcct', OrderedDict([
                        ('Id', OrderedDict([
                            ('IBAN', p.creditor.account),
                        ])),
                    ])),
                    ('RmtInf', OrderedDict([
                        ('Ustrd', p.remittance_info),
                    ])),
                ])),
            ]),
        })

    def render(self) -> bytes:
        if len(self.payments) == 0:
            raise ValidationError('No payments in pain.001.001.03')
        doc = Element('Document', xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03")
        pain = Element(self.pain_element_name)
        doc.append(pain)
        pain.append(self._grp_hdr())
        for p in self.payments:
            assert isinstance(p, Pain001Payment)
            pain.append(self._pmt_inf(p))
        xml_bytes = ElementTree.tostring(doc, encoding='utf8', method='xml')
        del doc
        return xml_bytes

    def render_to_file(self, filename: str):
        with open(filename, 'wb') as fp:
            fp.write(self.render())


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
