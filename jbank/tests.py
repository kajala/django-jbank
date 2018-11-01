import subprocess
from datetime import date

from decimal import Decimal
from os.path import join
from pprint import pprint

from django.conf import settings
from django.test import TestCase
from jbank.parsers import parse_tiliote_statements_from_file, parse_svm_batches_from_file
from jbank.sepa import Pain001, Pain002, PAIN001_REMITTANCE_INFO_OCR, PAIN001_REMITTANCE_INFO_OCR_ISO
from jutil.format import format_xml
from jutil.validators import iban_bic


class Tests(TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_pain001(self):
        debtor_acc = 'FI4947300010416310'
        p = Pain001('201802071211XJANITEST', 'Vuokrahelppi', debtor_acc, iban_bic(debtor_acc), '020840699',
                    ['Koukkukankareentie 29', '20320 Turku'], 'FI')
        creditor_acc = 'FI8847304720017517'
        p.add_payment('201802071339A0001', 'Jani Kajala', creditor_acc, iban_bic(creditor_acc), Decimal('49.00'), 'vuokratilitys')
        p.add_payment('201802071339A0001', 'Jani Kajala', creditor_acc, iban_bic(creditor_acc), Decimal('49.00'), '302300', PAIN001_REMITTANCE_INFO_OCR)
        p.add_payment('201802071339A0001', 'Jani Kajala', creditor_acc, iban_bic(creditor_acc), Decimal('49.00'), 'RF92 1229', PAIN001_REMITTANCE_INFO_OCR_ISO)
        xml_str = format_xml(p.render().decode())
        # print(xml_str)

        filename = '/tmp/pain001.xml'
        with open(filename, 'wt') as fp:
            fp.write(xml_str)
            # print(filename, 'written')

        # /usr/bin/xmllint --format --pretty 1 --load-trace --debug --schema $1 $2
        res = subprocess.run([
            '/usr/bin/xmllint',
            '--noout',
            # '--format',
            # '--pretty', '1',
            # '--load-trace',
            # '--debug',
            '--schema',
            join(settings.BASE_DIR, 'data/pain001/pain.001.001.03.xsd'),
            filename,
        ])
        self.assertEqual(res.returncode, 0)

    def test_to(self):
        filename = join(settings.BASE_DIR, 'data/to/547404896.TO')
        statements = parse_tiliote_statements_from_file(filename)
        rec = statements[0]['records'][0]
        # pprint(rec)
        self.assertEqual(rec['amount'], Decimal('-1799.00'))
        self.assertEqual(rec['archive_identifier'], '180203473047IE5807')
        self.assertEqual(rec['paid_date'], date(2018, 2, 3))
        self.assertEqual(rec['sepa']['iban_account_number'], 'FI8847304720017517')

    def test_svm(self):
        filename = join(settings.BASE_DIR, 'data/svm/547392460.SVM')
        batches = parse_svm_batches_from_file(filename)
        recs = batches[0]['records']
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        # pprint(rec)
        self.assertEqual(rec['amount'], Decimal('49.00'))
        self.assertEqual(rec['archive_identifier'], '02042588WWRV0212')
        self.assertEqual(rec['remittance_info'], '00000000000000013013')

    def test_xp(self):
        filename = join(settings.BASE_DIR, 'data/xp/547958656.XP')
        with open(filename, 'rb') as fp:
            file_content = fp.read()
        p = Pain002(file_content)
        self.assertEqual(p.original_msg_id, '201802071211XJANITEST')
        self.assertEqual(p.msg_id, 'V000000009726773')
        self.assertEqual(p.group_status, 'ACCP')
        self.assertEqual(p.is_accepted, True)
