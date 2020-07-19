# pylint: disable=logging-format-interpolation,logging-not-lazy,too-many-arguments,too-many-locals,too-many-statements
import logging
import traceback
from typing import Callable, Optional
import requests
from django.utils.timezone import now
from django.utils.translation import ugettext as _
from jbank.models import WsEdiConnection, WsEdiSoapCall
from lxml import etree  # type: ignore  # pytype: disable=import-error
from jbank.x509_helpers import get_x509_cert_from_file, write_pem_file
from jutil.admin import admin_log
from jutil.format import get_media_full_path


logger = logging.getLogger(__name__)


def etree_get_element(el: etree.Element, ns: str, tag: str) -> etree.Element:
    """
    :param el: Root Element
    :param ns: Target namespace
    :param tag: Target tag
    :return: Found Element
    """
    if not ns.startswith('{'):
        ns = '{' + ns + '}'
    els = list(el.iter('{}{}'.format(ns, tag)))
    if not els:
        raise Exception('{} not found from {}'.format(tag, el))
    if len(els) > 1:
        raise Exception('{} found from {} more than once'.format(tag, el))
    return els[0]


def process_wspki_response(content: bytes, soap_call: WsEdiSoapCall, elem_ns: str = ''):
    ws = soap_call.connection
    command = soap_call.command

    # find elem namespace if not set
    if not elem_ns:
        envelope = etree.fromstring(content)
        if 'elem' not in envelope.nsmap:
            raise Exception("WS-PKI {} SOAP response invalid, 'elem' namespace missing".format(command))
        elem_ns = '{' + envelope.nsmap['elem'] + '}'

    # find response element and check return code
    envelope = etree.fromstring(content)
    res_el = etree_get_element(envelope, elem_ns, command + 'Response')
    return_code = etree_get_element(res_el, elem_ns, 'ReturnCode').text
    return_text = etree_get_element(res_el, elem_ns, 'ReturnText').text
    if return_code != '00':
        raise Exception("WS-PKI {} call failed, ReturnCode {} ({})".format(command, return_code, return_text))

    for cert_name in ['BankEncryptionCert', 'BankSigningCert', 'BankRootCert']:
        data_base64 = etree_get_element(res_el, elem_ns, cert_name).text
        filename = 'certs/ws{}-{}-{}.pem'.format(ws.id, soap_call.timestamp_digits, cert_name)
        write_pem_file(get_media_full_path(filename), data_base64)
        if cert_name == 'BankEncryptionCert':
            ws.bank_encryption_cert_file.name = filename
        elif cert_name == 'BankSigningCert':
            ws.bank_signing_cert_file.name = filename
        elif cert_name == 'BankRootCert':
            ws.bank_root_cert_file.name = filename
        ws.save()
        admin_log([ws], '{} set by system from SOAP call response id={}'.format(cert_name, soap_call.id))


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
        envelope: Optional[etree.Element] = None

        if command == 'GetBankCertificate':
            body_bytes = ws.get_pki_request(soap_call, 'jbank/pki_get_bank_certificate_soap_template.xml', **kwargs)
            envelope = etree.fromstring(body_bytes)
            if 'elem' not in envelope.nsmap:
                raise Exception("WS-PKI {} SOAP template invalid, 'elem' namespace missing".format(command))
            elem_ns = '{' + envelope.nsmap['elem'] + '}'
            req_el = etree_get_element(envelope, elem_ns, command + 'Request')

            if not ws.bank_root_cert_full_path:
                raise Exception('Bank root certificate missing')
            cert = get_x509_cert_from_file(ws.bank_root_cert_full_path)
            logger.info('BankRootCertificateSerialNo %s', cert.serial_number)
            el = etree.SubElement(req_el, '{}BankRootCertificateSerialNo'.format(elem_ns))
            el.text = str(cert.serial_number)
            el = etree.SubElement(req_el, '{}Timestamp'.format(elem_ns))
            el.text = soap_call.timestamp.isoformat()
            el = etree.SubElement(req_el, '{}RequestId'.format(elem_ns))
            el.text = soap_call.request_identifier

        if envelope is None:
            raise Exception('{} not implemented'.format(command))
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

        process_wspki_response(res.content, soap_call, elem_ns)

        return res.content
    except Exception:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=['error'])
        raise
