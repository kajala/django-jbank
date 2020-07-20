# pylint: disable=logging-format-interpolation,logging-not-lazy,too-many-arguments,too-many-locals,too-many-statements
import logging
import traceback
from typing import Callable, Optional
import requests
from django.utils.timezone import now
from django.utils.translation import ugettext as _

from jbank.csr_helpers import create_private_key, get_private_key_pem, strip_pem_header_and_footer
from jbank.models import WsEdiConnection, WsEdiSoapCall
from lxml import etree  # type: ignore  # pytype: disable=import-error
from jbank.x509_helpers import get_x509_cert_from_file, write_pem_file
from jutil.admin import admin_log
from jutil.format import get_media_full_path, format_xml_bytes

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


def generate_wspki_request(soap_call: WsEdiSoapCall, **kwargs) -> bytes:
    ws = soap_call.connection
    command = soap_call.command
    envelope: Optional[etree.Element] = None

    if command == 'GetBankCertificate':
        if not ws.bank_root_cert_full_path:
            raise Exception('Bank root certificate missing')

        body_bytes = ws.get_pki_request(soap_call, 'jbank/pki_soap_template.xml', **kwargs)
        envelope = etree.fromstring(body_bytes)
        for ns_name in ['elem', 'pkif']:
            if ns_name not in envelope.nsmap:
                raise Exception("WS-PKI {} SOAP template invalid, '{}' namespace missing".format(command, ns_name))
        pkif_ns = '{' + envelope.nsmap['pkif'] + '}'
        elem_ns = '{' + envelope.nsmap['elem'] + '}'

        req_hdr_el = etree_get_element(envelope, pkif_ns, 'RequestHeader')
        req_el = etree.SubElement(req_hdr_el.getparent(), '{}{}Request'.format(elem_ns, command))

        cert = get_x509_cert_from_file(ws.bank_root_cert_full_path)
        logger.info('BankRootCertificateSerialNo %s', cert.serial_number)
        el = etree.SubElement(req_el, '{}BankRootCertificateSerialNo'.format(elem_ns))
        el.text = str(cert.serial_number)
        el = etree.SubElement(req_el, '{}Timestamp'.format(elem_ns))
        el.text = soap_call.timestamp.isoformat()
        el = etree.SubElement(req_el, '{}RequestId'.format(elem_ns))
        el.text = soap_call.request_identifier
    elif command == 'CreateCertificate':
        encryption_pk = create_private_key()
        signing_pk = create_private_key()
        encryption_pk_pem = get_private_key_pem(encryption_pk)
        signing_pk_pem = get_private_key_pem(signing_pk)
        encryption_pk_filename = 'certs/ws{}-{}-{}.pem'.format(ws.id, soap_call.timestamp_digits, 'EncryptionKey')
        signing_pk_filename = 'certs/ws{}-{}-{}.pem'.format(ws.id, soap_call.timestamp_digits, 'SigningKey')
        write_pem_file(get_media_full_path(encryption_pk_filename), encryption_pk_pem)
        write_pem_file(get_media_full_path(signing_pk_filename), signing_pk_pem)
        req = ws.get_pki_request(soap_call, 'jbank/pki_create_certificate_request_template.xml', **{
            'encryption_cert_pkcs10': strip_pem_header_and_footer(encryption_pk_pem).decode().replace('\n', ''),
            'signing_cert_pkcs10': strip_pem_header_and_footer(signing_pk_pem).decode().replace('\n', ''),
        })
        logger.info('CreateCertificate request:\n%s', format_xml_bytes(req).decode())
        enc_req = ws.encrypt_application_request(req)
        logger.info('CreateCertificate request encrypted:\n%s', format_xml_bytes(enc_req).decode())

    if envelope is None:
        raise Exception('{} not implemented'.format(command))
    body_bytes = etree.tostring(envelope)
    return body_bytes


def process_wspki_response(content: bytes, soap_call: WsEdiSoapCall):
    ws = soap_call.connection
    command = soap_call.command

    # find elem namespace if not set
    envelope = etree.fromstring(content)
    if 'elem' not in envelope.nsmap:
        raise Exception("WS-PKI {} SOAP response invalid, 'elem' namespace missing".format(command))
    elem_ns = '{' + envelope.nsmap['elem'] + '}'

    # find response element and check return code
    res_el = etree_get_element(envelope, elem_ns, command + 'Response')
    return_code = etree_get_element(res_el, elem_ns, 'ReturnCode').text
    return_text = etree_get_element(res_el, elem_ns, 'ReturnText').text
    if return_code != '00':
        raise Exception("WS-PKI {} call failed, ReturnCode {} ({})".format(command, return_code, return_text))

    if command == 'GetBankCertificate':
        for cert_name in ['BankEncryptionCert', 'BankSigningCert', 'BankRootCert']:
            data_base64 = etree_get_element(res_el, elem_ns, cert_name).text
            filename = 'certs/ws{}-{}-{}.pem'.format(ws.id, soap_call.timestamp_digits, cert_name)
            write_pem_file(get_media_full_path(filename), data_base64.encode())
            if cert_name == 'BankEncryptionCert':
                ws.bank_encryption_cert_file.name = filename
            elif cert_name == 'BankSigningCert':
                ws.bank_signing_cert_file.name = filename
            elif cert_name == 'BankRootCert':
                ws.bank_root_cert_file.name = filename
            ws.save()
            admin_log([ws], '{} set by system from SOAP call response id={}'.format(cert_name, soap_call.id))
    else:
        raise Exception('{} not implemented'.format(command))


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
        body_bytes: bytes = generate_wspki_request(soap_call, **kwargs)
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

        process_wspki_response(res.content, soap_call)
        return res.content
    except Exception:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=['error'])
        raise
