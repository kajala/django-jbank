# pylint: disable=logging-format-interpolation,logging-not-lazy,too-many-arguments,too-many-locals,too-many-statements
import logging
import traceback
from typing import Callable
import requests
from django.utils.timezone import now
from django.utils.translation import ugettext as _
from jbank.models import WsEdiConnection, WsEdiSoapCall
from lxml import etree  # type: ignore  # pytype: disable=import-error
from jbank.x509_helpers import get_x509_cert_from_file


logger = logging.getLogger(__name__)


def wspki_execute(ws: WsEdiConnection, command: str,
                  verbose: bool = False, cls: Callable = WsEdiSoapCall, **kwargs) -> bytes:
    """
    :param ws:
    :param command:
    :param verbose:
    :param cls:
    :return: str
    """
    if ws and not ws.enabled:
        raise Exception(_('ws.edi.connection.not.enabled').format(ws=ws))

    soap_call = cls(connection=ws, command=command, **kwargs)
    assert isinstance(soap_call, WsEdiSoapCall)
    soap_call.full_clean()
    soap_call.save()
    call_str = 'WsEdiSoapCall({})'.format(soap_call.id)
    try:
        body_bytes = ws.get_pki_soap_request(soap_call, **kwargs)
        envelope = etree.fromstring(body_bytes)
        if 'elem' not in envelope.nsmap:
            raise Exception("WS-PKI {} SOAP template invalid, elem namespace missing".format(command))
        elem_ns = '{' + envelope.nsmap['elem'] + '}'
        req_el = list(envelope.iter('{}{}Request'.format(elem_ns, command)))[0]

        # command = 'GetBankCertificate'
        # from lxml import etree
        # ws = WsEdiConnection.objects.last()
        # soap_call = WsEdiSoapCall.objects.last()
        # body_bytes = ws.get_pki_soap_request(soap_call)
        # envelope = etree.fromstring(body_bytes)
        # ns2 = '{' + envelope.nsmap['ns2'] + '}'
        # req_el = list(envelope.iter('{}{}Request'.format(ns2, command)))[0]
        # from jbank.x509_helpers import *

        if command == 'GetBankCertificate':
            cert = get_x509_cert_from_file(ws.bank_root_cert_full_path)
            logger.info('BankRootCertificateSerialNo %s', cert.serial_number)
            el = etree.SubElement(req_el, '{}BankRootCertificateSerialNo'.format(elem_ns))
            el.text = str(cert.serial_number)
            el = etree.SubElement(req_el, '{}Timestamp'.format(elem_ns))
            el.text = soap_call.timestamp.isoformat()
            el = etree.SubElement(req_el, '{}RequestId'.format(elem_ns))
            el.text = soap_call.request_identifier
            pass

        body_bytes = etree.tostring(envelope)

        if verbose:
            logger.info('------------------------------------------------------ {} body_bytes\n{}'.format(call_str, body_bytes.decode()))
        debug_output = command in ws.debug_command_list or 'ALL' in ws.debug_command_list
        if debug_output:
            with open(soap_call.debug_application_request_full_path, 'wb') as fp:
                fp.write(body_bytes)

        http_headers = {
            'Connection': 'Close',
            'Content-Type': 'text/xml',
            'Method': 'POST',
            'SOAPAction': '',
            'User-Agent': 'Kajala WS',
        }
        if verbose:
            logger.info('HTTP POST {}'.format(ws.pki_endpoint))
        res = requests.post(ws.pki_endpoint, data=body_bytes, headers=http_headers)
        if debug_output:
            with open(soap_call.debug_application_response_full_path, 'wb') as fp:
                fp.write(res.content)
        if verbose:
            logger.info('------------------------------------------------------ {} HTTP response {}\n{}'.format(call_str, res.status_code, res.text))
        if res.status_code >= 300:
            logger.error('------------------------------------------------------ {} HTTP response {}\n{}'.format(call_str, res.status_code, res.text))
            raise Exception("WS-PKI {} HTTP {}".format(command, res.status_code))

        soap_call.executed = now()
        soap_call.save(update_fields=['executed'])

        if debug_output:
            with open(soap_call.debug_application_response_full_path, 'wb') as fp:
                fp.write(res.content)

        return res.content
    except Exception:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=['error'])
        raise
