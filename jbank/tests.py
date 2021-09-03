import os
import subprocess
from datetime import date, datetime
from decimal import Decimal
from os.path import join
import pytz
from dateutil.tz import tzutc
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.template.loader import get_template
from django.test import TestCase
from jacc.models import Account
from jbank.csr_helpers import create_private_key, create_csr_pem, get_private_key_pem, strip_pem_header_and_footer
from jbank.ecb import parse_euro_exchange_rates_xml
from jbank.helpers import validate_xml
from jbank.models import WsEdiConnection, WsEdiSoapCall, Payout, PayoutParty
from jbank.tito import parse_tiliote_statements_from_file
from jbank.svm import parse_svm_batches_from_file
from jbank.sepa import Pain001, Pain002, PAIN001_REMITTANCE_INFO_OCR, PAIN001_REMITTANCE_INFO_OCR_ISO
from jbank.x509_helpers import get_x509_cert_from_file
from jutil.format import format_xml
from jutil.validators import iban_bic
from lxml import etree  # type: ignore  # pytype: disable=import-error
from zeep.wsse import BinarySignature  # type: ignore


class Tests(TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_pain001(self):
        debtor_acc = "FI4947300010416310"
        p = Pain001(
            "201802071211XJANITEST",
            "Vuokrahelppi",
            debtor_acc,
            iban_bic(debtor_acc),
            "020840699",
            ["Koukkukankareentie 29", "20320 Turku"],
            "FI",
        )
        creditor_acc = "FI8847304720017517"
        p.add_payment("201802071339A0001", "Jani Kajala", creditor_acc, iban_bic(creditor_acc), Decimal("49.00"), "vuokratilitys")
        p.add_payment(
            "201802071339A0001",
            "Jani Kajala",
            creditor_acc,
            iban_bic(creditor_acc),
            Decimal("49.00"),
            "302300",
            PAIN001_REMITTANCE_INFO_OCR,
        )
        p.add_payment(
            "201802071339A0001",
            "Jani Kajala",
            creditor_acc,
            iban_bic(creditor_acc),
            Decimal("49.00"),
            "RF92 1229",
            PAIN001_REMITTANCE_INFO_OCR_ISO,
        )
        xml_str = format_xml(p.render_to_bytes().decode())
        # print(xml_str)

        filename = "/tmp/pain001.xml"
        with open(filename, "wt", encoding="utf-8") as fp:
            fp.write(xml_str)
            # print(filename, 'written')

        # /usr/bin/xmllint --format --pretty 1 --load-trace --debug --schema $1 $2
        res = subprocess.run(
            [
                "/usr/bin/xmllint",
                "--noout",
                # '--format',
                # '--pretty', '1',
                # '--load-trace',
                # '--debug',
                "--schema",
                join(settings.BASE_DIR, "data/pain001/pain.001.001.03.xsd"),
                filename,
            ]
        )
        self.assertEqual(res.returncode, 0)

    def test_to(self):
        filename = join(settings.BASE_DIR, "data/to/547404896.TO")
        statements = parse_tiliote_statements_from_file(filename)
        rec = statements[0]["records"][0]
        # pprint(rec)
        self.assertEqual(rec["amount"], Decimal("-1799.00"))
        self.assertEqual(rec["archive_identifier"], "180203473047IE5807")
        self.assertEqual(rec["paid_date"], date(2018, 2, 3))
        self.assertEqual(rec["sepa"]["iban_account_number"], "FI8847304720017517")

    def test_svm(self):
        filename = join(settings.BASE_DIR, "data/svm/547392460.SVM")
        batches = parse_svm_batches_from_file(filename)
        recs = batches[0]["records"]
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        # pprint(rec)
        self.assertEqual(rec["amount"], Decimal("49.00"))
        self.assertEqual(rec["archive_identifier"], "02042588WWRV0212")
        self.assertEqual(rec["remittance_info"], "00000000000000013013")

    def test_xp(self):
        filename = join(settings.BASE_DIR, "data/xp/547958656.XP")
        with open(filename, "rb") as fp:
            file_content = fp.read()
        p = Pain002(file_content)
        self.assertEqual(p.original_msg_id, "201802071211XJANITEST")
        self.assertEqual(p.msg_id, "V000000009726773")
        self.assertEqual(p.group_status, "ACCP")
        self.assertEqual(p.is_accepted, True)

    def test_ecb_rates(self):
        filename = join(settings.BASE_DIR, "data/ecb-rates-2019-08-15.xml")
        with open(filename, "rt") as fp:
            content = fp.read()
        rates = parse_euro_exchange_rates_xml(content)
        # for record_date, currency, rate in rates[20:21]:
        # print(record_date, currency, rate)
        record_date, currency, rate = rates[20]
        self.assertEqual(record_date, date(2019, 8, 15))
        self.assertEqual(currency, "HKD")
        self.assertEqual(rate, Decimal("8.744"))

    def normalize_soap_env(self, content: bytes) -> bytes:
        doc = etree.fromstring(content)
        doc.find(".//{http://www.w3.org/2000/09/xmldsig#}DigestValue").text = "x"
        doc.find(".//{http://www.w3.org/2000/09/xmldsig#}Reference").attrib["URI"] = "x"
        doc.find(".//{http://www.w3.org/2000/09/xmldsig#}SignatureValue").text = "x"
        doc.find(".//{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}Reference").attrib["URI"] = "x"
        doc.find(".//{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}BinarySecurityToken").attrib[
            "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Id"
        ] = "x"
        doc.find(".//{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}BinarySecurityToken").text = "x"
        doc.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Body").attrib[
            "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Id"
        ] = "x"
        return etree.tostring(doc)

    def test_x509(self):
        print("MEDIA_ROOT = {}".format(settings.MEDIA_ROOT))
        ws = WsEdiConnection(
            name="test",
            sender_identifier="12319203",
            receiver_identifier="123192031",
            target_identifier="1",
            environment="TEST",
            soap_endpoint="http://localhost",
        )
        ws.signing_cert_file.name = "data/x509/cert.pem"
        ws.signing_key_file.name = "data/x509/key.pem"
        ws.save()
        cert = get_x509_cert_from_file("data/x509/cert.pem")
        not_valid_before, not_valid_after = pytz.utc.localize(cert.not_valid_before), pytz.utc.localize(cert.not_valid_after)
        self.assertEqual(not_valid_before, datetime(2019, 12, 3, 17, 54, 41, tzinfo=tzutc()))
        self.assertEqual(not_valid_after, datetime(2019, 12, 13, 17, 54, 41, tzinfo=tzutc()))
        self.assertEqual(WsEdiConnection.objects.get_by_receiver_identifier("123192031").id, ws.id)
        app = open("data/x509/appreq.xml", "rb").read()
        signed = ws.sign_application_request(app)
        ref_signed = b'<?xml version="1.0"?>\n<ApplicationRequest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns="http://bxd.fi/xmldata/">\n    <CustomerId>061133</CustomerId>\n    <Timestamp>2012-02-20T08:50:59.4319012+01:00</Timestamp>\n    <Environment>TEST</Environment>\n    <FileReferences>\n    <FileReference>1202170046-1202171334</FileReference>\n    </FileReferences>\n    <SoftwareId>DBSEPAClient</SoftwareId>\n    <FileType>pain.002.001.02</FileType>\n    <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">\n        <SignedInfo>\n          <CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>\n          <SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>\n          <Reference URI="">\n            <Transforms>\n              <Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>\n            </Transforms>\n            <DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>\n            <DigestValue>D4Bg9OJdQHgsXMQM2ecyoqNwCn4=</DigestValue>\n          </Reference>\n        </SignedInfo>\n        <SignatureValue>NwocqJ57CuLnQJaIA0Jm8JM2WlNOrNSM65Qt9nq69az4QMCgZBwRIGudZ+uMVWu0\n8TRriTHgrCTw5W50cVnhC/z+sw0mcNuYskXmGpr+4nUASYbcbT6EzktrHHkbhVTZ\nI4X4NdN8skU06TdgzF1Jvw/GzKENrm4l9hBLGbQyIaJppGmK1lN0g1suxnyvzgZK\ngFEEJZdFgFWcP+mCU+88FkcYOVjoX48rLtr0hSXyWRgUC9x3Seiw5GoS3toaY9dE\nmo6GREWUFEX9TjaH2sOy4WexiejlYisCyEGzBnyBGZ/Xi5EdoZSRoGrh60r+XDLX\n3Vu3vCJm5zD3SPX5fV9o/A==</SignatureValue>\n        <KeyInfo>\n          <X509Data>\n            <X509IssuerSerial>\n              <X509IssuerName>Issuer: {{ ws.signing_cert.issuer.rfc4514_string }}</X509IssuerName>\n              <X509SerialNumber>{{ ws.signing_cert.serial_number }}</X509SerialNumber>\n            </X509IssuerSerial>\n            <X509Certificate>MIIDVDCCAjygAwIBAgIUGhIGnbdNdnLHN2Gb3GCgUeSJjQYwDQYJKoZIhvcNAQEL\nBQAwVzELMAkGA1UEBhMCVVMxCzAJBgNVBAgMAlRYMQ8wDQYDVQQHDAZEYWxsYXMx\nFTATBgNVBAoMDEthamFsYSBHcm91cDETMBEGA1UEAwwKa2FqYWxhLmNvbTAeFw0x\nOTEyMDMxNzU0NDFaFw0xOTEyMTMxNzU0NDFaMFcxCzAJBgNVBAYTAlVTMQswCQYD\nVQQIDAJUWDEPMA0GA1UEBwwGRGFsbGFzMRUwEwYDVQQKDAxLYWphbGEgR3JvdXAx\nEzARBgNVBAMMCmthamFsYS5jb20wggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK\nAoIBAQDzpgdgMXiW9SOxnFPLdeFIltlCxH2rAz/nj7bLGK9ycVMMOW08M55jLhMK\nfOnyrHECWSP1aSKV6ZsNrRqm87JK8LEHgnIIDauqmJY4bEqB9nj7SzhAJWw5dJWN\nVNQto0csItaywXm0OjYRo4spDoKlyUbgXEcIouH4nWe91fvpvCfZiTHlfToTqKJy\nHtJ+kPi2+PSySs4+167g3A+v4YnoHgxTJD+q6KSimQQ0VGpqcV4Mt7+U3VV3Jb61\nSJNoanB31DLDugTjCOYm6PfT1/abQgPi7TOkizNa6HIMtMO/KFXC/6P+Bg05cQM9\n1Qq7iZR2zIg62AjC7z41xivdf8PdAgMBAAGjGDAWMBQGA1UdEQQNMAuCCWxvY2Fs\naG9zdDANBgkqhkiG9w0BAQsFAAOCAQEAAn4D/QY8pdvbPYx+yDeYPlHnYv68ErBK\n7Ib2rrtM7jtumBVL9BCneacjdsLmsrXwNdQkQMynxl6bMa+uR3YkXyQSVs+aSwKy\nIkz++rI5ALRI5KQr/DGzWrrmlIbBrXtQkLUR2mnyw9t+ozSMPtdedVCr50c88B5j\ndnksF6odkXet2gpVa5aZ8T2HUl+DtixkKoQ66Ra0/cXXdi3pk6zfRAm8/wtVIzQY\nX2+rur2NOPUuCzdWAvNjzuWCgmH8A2BxCOWBAMJ/GpsVJpqJp0tgm7ah1N+r1y0c\nRqxXZw7bu3NXedB1YqmOvYRcAGK2WXH4aV7I/rDRCc+aMs1GCOocbw==</X509Certificate>\n          </X509Data>\n        </KeyInfo>\n    </Signature>\n</ApplicationRequest>\n'
        self.assertEqual(signed, ref_signed)
        encoded = ws.encode_application_request(signed)
        ref_encoded = b"PEFwcGxpY2F0aW9uUmVxdWVzdCB4bWxuczp4c2k9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hLWluc3RhbmNlIiB4bWxuczp4c2Q9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hIiB4bWxucz0iaHR0cDovL2J4ZC5maS94bWxkYXRhLyI+CiAgICA8Q3VzdG9tZXJJZD4wNjExMzM8L0N1c3RvbWVySWQ+CiAgICA8VGltZXN0YW1wPjIwMTItMDItMjBUMDg6NTA6NTkuNDMxOTAxMiswMTowMDwvVGltZXN0YW1wPgogICAgPEVudmlyb25tZW50PlRFU1Q8L0Vudmlyb25tZW50PgogICAgPEZpbGVSZWZlcmVuY2VzPgogICAgPEZpbGVSZWZlcmVuY2U+MTIwMjE3MDA0Ni0xMjAyMTcxMzM0PC9GaWxlUmVmZXJlbmNlPgogICAgPC9GaWxlUmVmZXJlbmNlcz4KICAgIDxTb2Z0d2FyZUlkPkRCU0VQQUNsaWVudDwvU29mdHdhcmVJZD4KICAgIDxGaWxlVHlwZT5wYWluLjAwMi4wMDEuMDI8L0ZpbGVUeXBlPgogICAgPFNpZ25hdHVyZSB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC8wOS94bWxkc2lnIyI+CiAgICAgICAgPFNpZ25lZEluZm8+CiAgICAgICAgICA8Q2Fub25pY2FsaXphdGlvbk1ldGhvZCBBbGdvcml0aG09Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvMTAveG1sLWV4Yy1jMTRuIyIvPgogICAgICAgICAgPFNpZ25hdHVyZU1ldGhvZCBBbGdvcml0aG09Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvMDkveG1sZHNpZyNyc2Etc2hhMSIvPgogICAgICAgICAgPFJlZmVyZW5jZSBVUkk9IiI+CiAgICAgICAgICAgIDxUcmFuc2Zvcm1zPgogICAgICAgICAgICAgIDxUcmFuc2Zvcm0gQWxnb3JpdGhtPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwLzA5L3htbGRzaWcjZW52ZWxvcGVkLXNpZ25hdHVyZSIvPgogICAgICAgICAgICA8L1RyYW5zZm9ybXM+CiAgICAgICAgICAgIDxEaWdlc3RNZXRob2QgQWxnb3JpdGhtPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwLzA5L3htbGRzaWcjc2hhMSIvPgogICAgICAgICAgICA8RGlnZXN0VmFsdWU+RDRCZzlPSmRRSGdzWE1RTTJlY3lvcU53Q240PTwvRGlnZXN0VmFsdWU+CiAgICAgICAgICA8L1JlZmVyZW5jZT4KICAgICAgICA8L1NpZ25lZEluZm8+CiAgICAgICAgPFNpZ25hdHVyZVZhbHVlPk53b2NxSjU3Q3VMblFKYUlBMEptOEpNMldsTk9yTlNNNjVRdDlucTY5YXo0UU1DZ1pCd1JJR3VkWit1TVZXdTAKOFRScmlUSGdyQ1R3NVc1MGNWbmhDL3orc3cwbWNOdVlza1htR3ByKzRuVUFTWWJjYlQ2RXprdHJISGtiaFZUWgpJNFg0TmROOHNrVTA2VGRnekYxSnZ3L0d6S0VOcm00bDloQkxHYlF5SWFKcHBHbUsxbE4wZzFzdXhueXZ6Z1pLCmdGRUVKWmRGZ0ZXY1ArbUNVKzg4RmtjWU9Wam9YNDhyTHRyMGhTWHlXUmdVQzl4M1NlaXc1R29TM3RvYVk5ZEUKbW82R1JFV1VGRVg5VGphSDJzT3k0V2V4aWVqbFlpc0N5RUd6Qm55QkdaL1hpNUVkb1pTUm9Hcmg2MHIrWERMWAozVnUzdkNKbTV6RDNTUFg1ZlY5by9BPT08L1NpZ25hdHVyZVZhbHVlPgogICAgICAgIDxLZXlJbmZvPgogICAgICAgICAgPFg1MDlEYXRhPgogICAgICAgICAgICA8WDUwOUlzc3VlclNlcmlhbD4KICAgICAgICAgICAgICA8WDUwOUlzc3Vlck5hbWU+SXNzdWVyOiB7eyB3cy5zaWduaW5nX2NlcnQuaXNzdWVyLnJmYzQ1MTRfc3RyaW5nIH19PC9YNTA5SXNzdWVyTmFtZT4KICAgICAgICAgICAgICA8WDUwOVNlcmlhbE51bWJlcj57eyB3cy5zaWduaW5nX2NlcnQuc2VyaWFsX251bWJlciB9fTwvWDUwOVNlcmlhbE51bWJlcj4KICAgICAgICAgICAgPC9YNTA5SXNzdWVyU2VyaWFsPgogICAgICAgICAgICA8WDUwOUNlcnRpZmljYXRlPk1JSURWRENDQWp5Z0F3SUJBZ0lVR2hJR25iZE5kbkxITjJHYjNHQ2dVZVNKalFZd0RRWUpLb1pJaHZjTkFRRUwKQlFBd1Z6RUxNQWtHQTFVRUJoTUNWVk14Q3pBSkJnTlZCQWdNQWxSWU1ROHdEUVlEVlFRSERBWkVZV3hzWVhNeApGVEFUQmdOVkJBb01ERXRoYW1Gc1lTQkhjbTkxY0RFVE1CRUdBMVVFQXd3S2EyRnFZV3hoTG1OdmJUQWVGdzB4Ck9URXlNRE14TnpVME5ERmFGdzB4T1RFeU1UTXhOelUwTkRGYU1GY3hDekFKQmdOVkJBWVRBbFZUTVFzd0NRWUQKVlFRSURBSlVXREVQTUEwR0ExVUVCd3dHUkdGc2JHRnpNUlV3RXdZRFZRUUtEQXhMWVdwaGJHRWdSM0p2ZFhBeApFekFSQmdOVkJBTU1DbXRoYW1Gc1lTNWpiMjB3Z2dFaU1BMEdDU3FHU0liM0RRRUJBUVVBQTRJQkR3QXdnZ0VLCkFvSUJBUUR6cGdkZ01YaVc5U094bkZQTGRlRklsdGxDeEgyckF6L25qN2JMR0s5eWNWTU1PVzA4TTU1akxoTUsKZk9ueXJIRUNXU1AxYVNLVjZac05yUnFtODdKSzhMRUhnbklJRGF1cW1KWTRiRXFCOW5qN1N6aEFKV3c1ZEpXTgpWTlF0bzBjc0l0YXl3WG0wT2pZUm80c3BEb0tseVViZ1hFY0lvdUg0bldlOTFmdnB2Q2ZaaVRIbGZUb1RxS0p5Ckh0SitrUGkyK1BTeVNzNCsxNjdnM0ErdjRZbm9IZ3hUSkQrcTZLU2ltUVEwVkdwcWNWNE10NytVM1ZWM0piNjEKU0pOb2FuQjMxRExEdWdUakNPWW02UGZUMS9hYlFnUGk3VE9raXpOYTZISU10TU8vS0ZYQy82UCtCZzA1Y1FNOQoxUXE3aVpSMnpJZzYyQWpDN3o0MXhpdmRmOFBkQWdNQkFBR2pHREFXTUJRR0ExVWRFUVFOTUF1Q0NXeHZZMkZzCmFHOXpkREFOQmdrcWhraUc5dzBCQVFzRkFBT0NBUUVBQW40RC9RWThwZHZiUFl4K3lEZVlQbEhuWXY2OEVyQksKN0liMnJydE03anR1bUJWTDlCQ25lYWNqZHNMbXNyWHdOZFFrUU15bnhsNmJNYSt1UjNZa1h5UVNWcythU3dLeQpJa3orK3JJNUFMUkk1S1FyL0RHeldycm1sSWJCclh0UWtMVVIybW55dzl0K296U01QdGRlZFZDcjUwYzg4QjVqCmRua3NGNm9ka1hldDJncFZhNWFaOFQySFVsK0R0aXhrS29RNjZSYTAvY1hYZGkzcGs2emZSQW04L3d0Vkl6UVkKWDIrcnVyMk5PUFV1Q3pkV0F2Tmp6dVdDZ21IOEEyQnhDT1dCQU1KL0dwc1ZKcHFKcDB0Z203YWgxTityMXkwYwpScXhYWnc3YnUzTlhlZEIxWXFtT3ZZUmNBR0syV1hINGFWN0kvckRSQ2MrYU1zMUdDT29jYnc9PTwvWDUwOUNlcnRpZmljYXRlPgogICAgICAgICAgPC9YNTA5RGF0YT4KICAgICAgICA8L0tleUluZm8+CiAgICA8L1NpZ25hdHVyZT4KPC9BcHBsaWNhdGlvblJlcXVlc3Q+Cg=="
        self.assertEqual(encoded, ref_encoded)
        timestamp = pytz.timezone("Europe/Helsinki").localize(datetime(2015, 2, 3, 14, 30))
        soap_call = WsEdiSoapCall.objects.create(connection=ws, command="HelloWorld", created=timestamp)
        soap_body = get_template("jbank/soap_template.xml").render({"soap_call": soap_call, "payload": encoded.decode()})
        body_bytes = soap_body.encode()
        envelope = etree.fromstring(body_bytes)
        binary_signature = BinarySignature(ws.signing_key_full_path, ws.signing_cert_full_path)
        soap_headers = {}
        envelope, soap_headers = binary_signature.apply(envelope, soap_headers)
        signed_body_bytes = etree.tostring(envelope)
        ref_bytes = b'<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://model.bxd.fi" xmlns:ns2="http://bxd.fi/CorporateFileService">\n  <SOAP-ENV:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><Signature xmlns="http://www.w3.org/2000/09/xmldsig#">\n<SignedInfo>\n<CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>\n<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>\n<Reference URI="#id-311224dc-3b4c-4729-b057-b3abb3bcd95d">\n<Transforms>\n<Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>\n</Transforms>\n<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>\n<DigestValue>FGoo2ZirnTBuQ9N22TPlCO7eXaw=</DigestValue>\n</Reference>\n</SignedInfo>\n<SignatureValue>MNseos+F09uzlAH+1B1o01OBPslz80vJZWmi4yJ4NniZBXAD0rtPBRhbVwF4gSw1\nbLt1Eb6YKDl+nyRMb0f1H/QUuIibMXvlKu2IVYyTUktovW29oKkmXKfJtix9Eh+w\nzq4XEDZ3BjbkHW7FrS5ZZGRJB+IeePTHSZZ1JSxiBnOg5BQ0MlypUhdEaaHmJUM+\n7vSp/pttXc8vNdB/8pFCV0Yw/DrjANeNbFv2HXG0p1R1Dhmk2Tj3+cWDpWoFuWoj\nOg1y0eoTHW/5SSob9S9mEQPJi6aYr1ni89dF1I78o7kRDxK/JbyTNBq0YnLaXA2m\n6hJlwwfj9iYH3AkYe9YXsw==</SignatureValue>\n<KeyInfo>\n<wsse:SecurityTokenReference><wsse:Reference ValueType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3" URI="#id-e328e4ba-0ee6-4570-8476-89c88611e5ac"/></wsse:SecurityTokenReference></KeyInfo>\n</Signature><wsse:BinarySecurityToken xmlns:ns1="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" ValueType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3" EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary" ns1:Id="id-e328e4ba-0ee6-4570-8476-89c88611e5ac">MIIDVDCCAjygAwIBAgIUGhIGnbdNdnLHN2Gb3GCgUeSJjQYwDQYJKoZIhvcNAQEL\nBQAwVzELMAkGA1UEBhMCVVMxCzAJBgNVBAgMAlRYMQ8wDQYDVQQHDAZEYWxsYXMx\nFTATBgNVBAoMDEthamFsYSBHcm91cDETMBEGA1UEAwwKa2FqYWxhLmNvbTAeFw0x\nOTEyMDMxNzU0NDFaFw0xOTEyMTMxNzU0NDFaMFcxCzAJBgNVBAYTAlVTMQswCQYD\nVQQIDAJUWDEPMA0GA1UEBwwGRGFsbGFzMRUwEwYDVQQKDAxLYWphbGEgR3JvdXAx\nEzARBgNVBAMMCmthamFsYS5jb20wggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK\nAoIBAQDzpgdgMXiW9SOxnFPLdeFIltlCxH2rAz/nj7bLGK9ycVMMOW08M55jLhMK\nfOnyrHECWSP1aSKV6ZsNrRqm87JK8LEHgnIIDauqmJY4bEqB9nj7SzhAJWw5dJWN\nVNQto0csItaywXm0OjYRo4spDoKlyUbgXEcIouH4nWe91fvpvCfZiTHlfToTqKJy\nHtJ+kPi2+PSySs4+167g3A+v4YnoHgxTJD+q6KSimQQ0VGpqcV4Mt7+U3VV3Jb61\nSJNoanB31DLDugTjCOYm6PfT1/abQgPi7TOkizNa6HIMtMO/KFXC/6P+Bg05cQM9\n1Qq7iZR2zIg62AjC7z41xivdf8PdAgMBAAGjGDAWMBQGA1UdEQQNMAuCCWxvY2Fs\naG9zdDANBgkqhkiG9w0BAQsFAAOCAQEAAn4D/QY8pdvbPYx+yDeYPlHnYv68ErBK\n7Ib2rrtM7jtumBVL9BCneacjdsLmsrXwNdQkQMynxl6bMa+uR3YkXyQSVs+aSwKy\nIkz++rI5ALRI5KQr/DGzWrrmlIbBrXtQkLUR2mnyw9t+ozSMPtdedVCr50c88B5j\ndnksF6odkXet2gpVa5aZ8T2HUl+DtixkKoQ66Ra0/cXXdi3pk6zfRAm8/wtVIzQY\nX2+rur2NOPUuCzdWAvNjzuWCgmH8A2BxCOWBAMJ/GpsVJpqJp0tgm7ah1N+r1y0c\nRqxXZw7bu3NXedB1YqmOvYRcAGK2WXH4aV7I/rDRCc+aMs1GCOocbw==</wsse:BinarySecurityToken></wsse:Security></SOAP-ENV:Header><SOAP-ENV:Body xmlns:ns0="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" ns0:Id="id-311224dc-3b4c-4729-b057-b3abb3bcd95d">\n    <ns2:helloWorldin>\n      <ns1:RequestHeader>\n        <ns1:SenderId>12319203</ns1:SenderId>\n        <ns1:RequestId>1</ns1:RequestId>\n        <ns1:Timestamp>2015-02-03T14:30:00+02:00</ns1:Timestamp>\n        <ns1:Language>FI</ns1:Language>\n        <ns1:UserAgent>Kajala WS</ns1:UserAgent>\n        <ns1:ReceiverId>123192031</ns1:ReceiverId>\n      </ns1:RequestHeader>\n    <ns1:ApplicationRequest>PEFwcGxpY2F0aW9uUmVxdWVzdCB4bWxuczp4c2k9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hLWluc3RhbmNlIiB4bWxuczp4c2Q9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hIiB4bWxucz0iaHR0cDovL2J4ZC5maS94bWxkYXRhLyI+CiAgICA8Q3VzdG9tZXJJZD4wNjExMzM8L0N1c3RvbWVySWQ+CiAgICA8VGltZXN0YW1wPjIwMTItMDItMjBUMDg6NTA6NTkuNDMxOTAxMiswMTowMDwvVGltZXN0YW1wPgogICAgPEVudmlyb25tZW50PlRFU1Q8L0Vudmlyb25tZW50PgogICAgPEZpbGVSZWZlcmVuY2VzPgogICAgPEZpbGVSZWZlcmVuY2U+MTIwMjE3MDA0Ni0xMjAyMTcxMzM0PC9GaWxlUmVmZXJlbmNlPgogICAgPC9GaWxlUmVmZXJlbmNlcz4KICAgIDxTb2Z0d2FyZUlkPkRCU0VQQUNsaWVudDwvU29mdHdhcmVJZD4KICAgIDxGaWxlVHlwZT5wYWluLjAwMi4wMDEuMDI8L0ZpbGVUeXBlPgogICAgPFNpZ25hdHVyZSB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC8wOS94bWxkc2lnIyI+CiAgICAgICAgPFNpZ25lZEluZm8+CiAgICAgICAgICA8Q2Fub25pY2FsaXphdGlvbk1ldGhvZCBBbGdvcml0aG09Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvMTAveG1sLWV4Yy1jMTRuIyIvPgogICAgICAgICAgPFNpZ25hdHVyZU1ldGhvZCBBbGdvcml0aG09Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvMDkveG1sZHNpZyNyc2Etc2hhMSIvPgogICAgICAgICAgPFJlZmVyZW5jZSBVUkk9IiI+CiAgICAgICAgICAgIDxUcmFuc2Zvcm1zPgogICAgICAgICAgICAgIDxUcmFuc2Zvcm0gQWxnb3JpdGhtPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwLzA5L3htbGRzaWcjZW52ZWxvcGVkLXNpZ25hdHVyZSIvPgogICAgICAgICAgICA8L1RyYW5zZm9ybXM+CiAgICAgICAgICAgIDxEaWdlc3RNZXRob2QgQWxnb3JpdGhtPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwLzA5L3htbGRzaWcjc2hhMSIvPgogICAgICAgICAgICA8RGlnZXN0VmFsdWU+RDRCZzlPSmRRSGdzWE1RTTJlY3lvcU53Q240PTwvRGlnZXN0VmFsdWU+CiAgICAgICAgICA8L1JlZmVyZW5jZT4KICAgICAgICA8L1NpZ25lZEluZm8+CiAgICAgICAgPFNpZ25hdHVyZVZhbHVlPk53b2NxSjU3Q3VMblFKYUlBMEptOEpNMldsTk9yTlNNNjVRdDlucTY5YXo0UU1DZ1pCd1JJR3VkWit1TVZXdTAKOFRScmlUSGdyQ1R3NVc1MGNWbmhDL3orc3cwbWNOdVlza1htR3ByKzRuVUFTWWJjYlQ2RXprdHJISGtiaFZUWgpJNFg0TmROOHNrVTA2VGRnekYxSnZ3L0d6S0VOcm00bDloQkxHYlF5SWFKcHBHbUsxbE4wZzFzdXhueXZ6Z1pLCmdGRUVKWmRGZ0ZXY1ArbUNVKzg4RmtjWU9Wam9YNDhyTHRyMGhTWHlXUmdVQzl4M1NlaXc1R29TM3RvYVk5ZEUKbW82R1JFV1VGRVg5VGphSDJzT3k0V2V4aWVqbFlpc0N5RUd6Qm55QkdaL1hpNUVkb1pTUm9Hcmg2MHIrWERMWAozVnUzdkNKbTV6RDNTUFg1ZlY5by9BPT08L1NpZ25hdHVyZVZhbHVlPgogICAgICAgIDxLZXlJbmZvPgogICAgICAgICAgPFg1MDlEYXRhPgogICAgICAgICAgICA8WDUwOUlzc3VlclNlcmlhbD4KICAgICAgICAgICAgICA8WDUwOUlzc3Vlck5hbWU+SXNzdWVyOiB7eyB3cy5zaWduaW5nX2NlcnQuaXNzdWVyLnJmYzQ1MTRfc3RyaW5nIH19PC9YNTA5SXNzdWVyTmFtZT4KICAgICAgICAgICAgICA8WDUwOVNlcmlhbE51bWJlcj57eyB3cy5zaWduaW5nX2NlcnQuc2VyaWFsX251bWJlciB9fTwvWDUwOVNlcmlhbE51bWJlcj4KICAgICAgICAgICAgPC9YNTA5SXNzdWVyU2VyaWFsPgogICAgICAgICAgICA8WDUwOUNlcnRpZmljYXRlPk1JSURWRENDQWp5Z0F3SUJBZ0lVR2hJR25iZE5kbkxITjJHYjNHQ2dVZVNKalFZd0RRWUpLb1pJaHZjTkFRRUwKQlFBd1Z6RUxNQWtHQTFVRUJoTUNWVk14Q3pBSkJnTlZCQWdNQWxSWU1ROHdEUVlEVlFRSERBWkVZV3hzWVhNeApGVEFUQmdOVkJBb01ERXRoYW1Gc1lTQkhjbTkxY0RFVE1CRUdBMVVFQXd3S2EyRnFZV3hoTG1OdmJUQWVGdzB4Ck9URXlNRE14TnpVME5ERmFGdzB4T1RFeU1UTXhOelUwTkRGYU1GY3hDekFKQmdOVkJBWVRBbFZUTVFzd0NRWUQKVlFRSURBSlVXREVQTUEwR0ExVUVCd3dHUkdGc2JHRnpNUlV3RXdZRFZRUUtEQXhMWVdwaGJHRWdSM0p2ZFhBeApFekFSQmdOVkJBTU1DbXRoYW1Gc1lTNWpiMjB3Z2dFaU1BMEdDU3FHU0liM0RRRUJBUVVBQTRJQkR3QXdnZ0VLCkFvSUJBUUR6cGdkZ01YaVc5U094bkZQTGRlRklsdGxDeEgyckF6L25qN2JMR0s5eWNWTU1PVzA4TTU1akxoTUsKZk9ueXJIRUNXU1AxYVNLVjZac05yUnFtODdKSzhMRUhnbklJRGF1cW1KWTRiRXFCOW5qN1N6aEFKV3c1ZEpXTgpWTlF0bzBjc0l0YXl3WG0wT2pZUm80c3BEb0tseVViZ1hFY0lvdUg0bldlOTFmdnB2Q2ZaaVRIbGZUb1RxS0p5Ckh0SitrUGkyK1BTeVNzNCsxNjdnM0ErdjRZbm9IZ3hUSkQrcTZLU2ltUVEwVkdwcWNWNE10NytVM1ZWM0piNjEKU0pOb2FuQjMxRExEdWdUakNPWW02UGZUMS9hYlFnUGk3VE9raXpOYTZISU10TU8vS0ZYQy82UCtCZzA1Y1FNOQoxUXE3aVpSMnpJZzYyQWpDN3o0MXhpdmRmOFBkQWdNQkFBR2pHREFXTUJRR0ExVWRFUVFOTUF1Q0NXeHZZMkZzCmFHOXpkREFOQmdrcWhraUc5dzBCQVFzRkFBT0NBUUVBQW40RC9RWThwZHZiUFl4K3lEZVlQbEhuWXY2OEVyQksKN0liMnJydE03anR1bUJWTDlCQ25lYWNqZHNMbXNyWHdOZFFrUU15bnhsNmJNYSt1UjNZa1h5UVNWcythU3dLeQpJa3orK3JJNUFMUkk1S1FyL0RHeldycm1sSWJCclh0UWtMVVIybW55dzl0K296U01QdGRlZFZDcjUwYzg4QjVqCmRua3NGNm9ka1hldDJncFZhNWFaOFQySFVsK0R0aXhrS29RNjZSYTAvY1hYZGkzcGs2emZSQW04L3d0Vkl6UVkKWDIrcnVyMk5PUFV1Q3pkV0F2Tmp6dVdDZ21IOEEyQnhDT1dCQU1KL0dwc1ZKcHFKcDB0Z203YWgxTityMXkwYwpScXhYWnc3YnUzTlhlZEIxWXFtT3ZZUmNBR0syV1hINGFWN0kvckRSQ2MrYU1zMUdDT29jYnc9PTwvWDUwOUNlcnRpZmljYXRlPgogICAgICAgICAgPC9YNTA5RGF0YT4KICAgICAgICA8L0tleUluZm8+CiAgICA8L1NpZ25hdHVyZT4KPC9BcHBsaWNhdGlvblJlcXVlc3Q+Cg==</ns1:ApplicationRequest>\n    </ns2:helloWorldin>\n  </SOAP-ENV:Body>\n</SOAP-ENV:Envelope>'
        self.assertEqual(self.normalize_soap_env(ref_bytes), self.normalize_soap_env(signed_body_bytes))

    def test_validate_xml(self):
        xml = os.path.join(settings.BASE_DIR, "data/finvoice/xsd-test.xml")
        xsd = os.path.join(settings.BASE_DIR, "data/finvoice/xsd-test.xsd")
        with open(xml, "rb") as fp:
            validate_xml(fp.read(), xsd)

    def test_payout_validation(self):
        payer = PayoutParty.objects.all().first()
        recipient = PayoutParty.objects.all().last()
        connection = WsEdiConnection.objects.all().first()
        acc = Account.objects.all().first()
        p = Payout(connection=connection, payer=payer, recipient=recipient, messages="testi", account=acc)
        with self.assertRaisesMessage(ValidationError, "> 0"):
            p.full_clean()

    def test_rsa_csr(self):
        pk = create_private_key()
        csr = create_csr_pem(pk, common_name="kajala.com", country_name="FI", organization_name="Kajala Group Ltd")
        self.assertEqual(csr.decode().split("\n")[0], "-----BEGIN CERTIFICATE REQUEST-----")
        pk_pem = get_private_key_pem(pk)
        self.assertEqual(pk_pem.decode().split("\n")[0], "-----BEGIN PRIVATE KEY-----")
        self.assertFalse(strip_pem_header_and_footer(pk_pem).startswith(b"-----BEGIN"))

    def test_parse_xt(self):
        if os.path.isdir("./downloads/xt"):
            call_command("parse_xt", "downloads/xt", auto_create_accounts=True)
        if os.path.isdir("./downloads/svm"):
            call_command("parse_svm", "downloads/svm", auto_create_accounts=True)
        call_command("parse_xt", "data/xt", auto_create_accounts=True)
        call_command("parse_svm", "data/svm", auto_create_accounts=True)
        call_command("parse_to", "data/to", auto_create_accounts=True)
