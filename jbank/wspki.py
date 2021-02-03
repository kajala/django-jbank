import logging
import traceback
from typing import Callable, Optional
import requests
from django.utils.timezone import now
from django.utils.translation import ugettext as _
from jbank.csr_helpers import (
    create_private_key,
    get_private_key_pem,
    strip_pem_header_and_footer,
    create_csr_pem,
    write_private_key_pem_file,
    load_private_key_from_pem_file,
)
from jbank.models import WsEdiConnection, WsEdiSoapCall, PayoutParty
from lxml import etree  # type: ignore  # pytype: disable=import-error
from jbank.x509_helpers import get_x509_cert_from_file, write_cert_pem_file
from jutil.admin import admin_log
from jutil.format import get_media_full_path, format_xml_bytes


logger = logging.getLogger(__name__)


def etree_find_element(el: etree.Element, ns: str, tag: str) -> Optional[etree.Element]:
    """
    :param el: Root Element
    :param ns: Target namespace
    :param tag: Target tag
    :return: Element if found
    """
    if not ns.startswith("{"):
        ns = "{" + ns + "}"
    els = list(el.iter("{}{}".format(ns, tag)))
    if not els:
        return None
    if len(els) > 1:
        return None
    return els[0]


def etree_get_element(el: etree.Element, ns: str, tag: str) -> etree.Element:
    """
    :param el: Root Element
    :param ns: Target namespace
    :param tag: Target tag
    :return: Found Element
    """
    if not ns.startswith("{"):
        ns = "{" + ns + "}"
    els = list(el.iter("{}{}".format(ns, tag)))
    if not els:
        raise Exception("{} not found from {}".format(tag, el))
    if len(els) > 1:
        raise Exception("{} found from {} more than once".format(tag, el))
    return els[0]


def generate_wspki_request(  # pylint: disable=too-many-locals,too-many-statements
    soap_call: WsEdiSoapCall, payout_party: PayoutParty, **kwargs
) -> bytes:
    ws = soap_call.connection
    command = soap_call.command

    soap_body_bytes = ws.get_pki_template("jbank/pki_soap_template.xml", soap_call, **kwargs)
    envelope = etree.fromstring(soap_body_bytes)
    for ns_name in ["elem", "pkif"]:
        if ns_name not in envelope.nsmap:
            raise Exception("WS-PKI {} SOAP template invalid, '{}' namespace missing".format(command, ns_name))
    pkif_ns = "{" + envelope.nsmap["pkif"] + "}"
    elem_ns = "{" + envelope.nsmap["elem"] + "}"
    req_hdr_el = etree_get_element(envelope, pkif_ns, "RequestHeader")
    cmd_el = req_hdr_el.getparent()

    if command == "GetBankCertificate":
        if not ws.bank_root_cert_full_path:
            raise Exception("Bank root certificate missing")

        req_el = etree.SubElement(cmd_el, "{}{}Request".format(elem_ns, command))
        cert = get_x509_cert_from_file(ws.bank_root_cert_full_path)
        logger.info("BankRootCertificateSerialNo %s", cert.serial_number)
        el = etree.SubElement(req_el, "{}BankRootCertificateSerialNo".format(elem_ns))
        el.text = str(cert.serial_number)
        el = etree.SubElement(req_el, "{}Timestamp".format(elem_ns))
        el.text = soap_call.timestamp.isoformat()
        el = etree.SubElement(req_el, "{}RequestId".format(elem_ns))
        el.text = soap_call.request_identifier

    elif command in ["CreateCertificate", "RenewCertificate"]:
        is_create = command == "CreateCertificate"
        is_renew = command == "RenewCertificate"

        if is_create:
            encryption_pk = create_private_key()
            signing_pk = create_private_key()
            encryption_pk_pem = get_private_key_pem(encryption_pk)
            signing_pk_pem = get_private_key_pem(signing_pk)
            encryption_pk_filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, "EncryptionKey")
            signing_pk_filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, "SigningKey")
            ws.encryption_key_file.name = encryption_pk_filename
            ws.signing_key_file.name = signing_pk_filename
            ws.save()
            admin_log(
                [ws],
                "Encryption and signing private keys set as {} and {}".format(
                    encryption_pk_filename, signing_pk_filename
                ),
            )
            write_private_key_pem_file(get_media_full_path(encryption_pk_filename), encryption_pk_pem)
            write_private_key_pem_file(get_media_full_path(signing_pk_filename), signing_pk_pem)
        else:
            encryption_pk = load_private_key_from_pem_file(ws.encryption_key_full_path)
            signing_pk = load_private_key_from_pem_file(ws.signing_key_full_path)

        csr_params = {
            "common_name": payout_party.name,
            "organization_name": payout_party.name,
            "country_name": payout_party.country_code,
            "organizational_unit_name": "IT-services",
            "locality_name": "Helsinki",
            "state_or_province_name": "Uusimaa",
        }
        encryption_csr = create_csr_pem(encryption_pk, **csr_params)
        signing_csr = create_csr_pem(signing_pk, **csr_params)
        req = ws.get_pki_template(
            "jbank/pki_create_certificate_request_template.xml",
            soap_call,
            **{
                "encryption_cert_pkcs10": strip_pem_header_and_footer(encryption_csr).decode().replace("\n", ""),
                "signing_cert_pkcs10": strip_pem_header_and_footer(signing_csr).decode().replace("\n", ""),
                "old_signing_cert": ws.signing_cert if is_renew else None,
            }
        )
        logger.info("%s request:\n%s", command, format_xml_bytes(req).decode())

        if is_renew:
            req = ws.sign_pki_request(req)
            logger.info("%s request signed:\n%s", command, format_xml_bytes(req).decode())

        enc_req_bytes = ws.encrypt_pki_request(req)
        logger.info("%s request encrypted:\n%s", command, format_xml_bytes(enc_req_bytes).decode())
        req_el = etree.fromstring(enc_req_bytes)

        cmd_el.insert(cmd_el.index(req_hdr_el) + 1, req_el)

    elif command in ["CertificateStatus", "GetOwnCertificateList"]:
        cert = get_x509_cert_from_file(ws.signing_cert_full_path)
        req = ws.get_pki_template(
            "jbank/pki_certificate_status_request_template.xml",
            soap_call,
            **{
                "certs": [cert],
            }
        )
        logger.info("%s request:\n%s", command, format_xml_bytes(req).decode())

        req = ws.sign_pki_request(req)
        logger.info("%s request signed:\n%s", command, format_xml_bytes(req).decode())
        req_el = etree.fromstring(req)
        cmd_el.insert(cmd_el.index(req_hdr_el) + 1, req_el)

    else:
        raise Exception("{} not implemented".format(command))

    body_bytes = etree.tostring(envelope)
    return body_bytes


def process_wspki_response(content: bytes, soap_call: WsEdiSoapCall):  # noqa
    ws = soap_call.connection
    command = soap_call.command
    envelope = etree.fromstring(content)

    # check for errors
    return_code: str = ""
    return_text: str = ""
    for el in envelope.iter():
        if el.tag and el.tag.endswith("}ReturnCode"):
            return_code = el.text
            return_text_el = list(envelope.iter(el.tag[:-4] + "Text"))[0]
            return_text = return_text_el.text if return_text_el is not None else ""
    if return_code != "00":
        raise Exception("WS-PKI {} call failed, ReturnCode {} ({})".format(command, return_code, return_text))

    # find namespaces
    pkif_ns = ""
    elem_ns = ""
    for ns_name, ns_url in envelope.nsmap.items():
        assert isinstance(ns_name, str)
        if ns_url.endswith("PKIFactoryService/elements"):
            elem_ns = "{" + ns_url + "}"
        elif ns_url.endswith("PKIFactoryService"):
            pkif_ns = "{" + ns_url + "}"
    if not pkif_ns:
        raise Exception("WS-PKI {} SOAP response invalid, PKIFactoryService namespace missing".format(command))
    if not elem_ns:
        raise Exception("WS-PKI {} SOAP response invalid, PKIFactoryService/elements namespace missing".format(command))

    # find response element
    res_el = etree_get_element(envelope, elem_ns, command + "Response")

    if command == "GetBankCertificate":
        for cert_name in ["BankEncryptionCert", "BankSigningCert", "BankRootCert"]:
            data_base64 = etree_get_element(res_el, elem_ns, cert_name).text
            filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, cert_name)
            write_cert_pem_file(get_media_full_path(filename), data_base64.encode())
            if cert_name == "BankEncryptionCert":
                ws.bank_encryption_cert_file.name = filename
            elif cert_name == "BankSigningCert":
                ws.bank_signing_cert_file.name = filename
            elif cert_name == "BankRootCert":
                ws.bank_root_cert_file.name = filename
            ws.save()
            admin_log([ws], "{} set by system from SOAP call response id={}".format(cert_name, soap_call.id))

    elif command in ["CreateCertificate", "RenewCertificate"]:
        for cert_name in ["EncryptionCert", "SigningCert", "CACert"]:
            data_base64 = etree_get_element(res_el, elem_ns, cert_name).text
            filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, cert_name)
            write_cert_pem_file(get_media_full_path(filename), data_base64.encode())
            if cert_name == "EncryptionCert":
                ws.encryption_cert_file.name = filename
                admin_log([ws], "soap_call(id={}): encryption_cert_file={}".format(soap_call.id, filename))
            elif cert_name == "SigningCert":
                ws.signing_cert_file.name = filename
                admin_log([ws], "soap_call(id={}): signing_cert_file={}".format(soap_call.id, filename))
            elif cert_name == "CACert":
                ws.ca_cert_file.name = filename
                admin_log([ws], "soap_call(id={}): ca_cert_file={}".format(soap_call.id, filename))
            ws.save()

    elif command in ["CertificateStatus", "GetOwnCertificateList"]:
        pass

    else:
        raise Exception("{} not implemented".format(command))


def wspki_execute(
    ws: WsEdiConnection,
    payout_party: PayoutParty,
    command: str,
    verbose: bool = False,
    cls: Callable = WsEdiSoapCall,
    **kwargs
) -> bytes:
    """
    :param ws:
    :param payout_party:
    :param command:
    :param verbose:
    :param cls:
    :return: str
    """
    if ws and not ws.enabled:
        raise Exception(_("ws.edi.connection.not.enabled").format(ws=ws))

    soap_call = cls(connection=ws, command=command, **kwargs)
    assert isinstance(soap_call, WsEdiSoapCall)
    soap_call.full_clean()
    soap_call.save()
    call_str = "WsEdiSoapCall({})".format(soap_call.id)
    try:
        body_bytes: bytes = generate_wspki_request(soap_call, payout_party, **kwargs)
        if verbose:
            logger.info(
                "------------------------------------------------------ %s body_bytes\n%s",
                call_str,
                body_bytes.decode(),
            )
        debug_output = command in ws.debug_command_list or "ALL" in ws.debug_command_list
        if debug_output:
            with open(soap_call.debug_request_full_path, "wb") as fp:
                fp.write(body_bytes)

        http_headers = {
            "Connection": "Close",
            "Content-Type": "text/xml",
            "Method": "POST",
            "SOAPAction": "",
            "User-Agent": "Kajala WS",
        }
        if verbose:
            logger.info("HTTP POST %s", ws.pki_endpoint)
        res = requests.post(ws.pki_endpoint, data=body_bytes, headers=http_headers)
        if debug_output:
            with open(soap_call.debug_response_full_path, "wb") as fp:
                fp.write(res.content)
        if verbose:
            logger.info(
                "------------------------------------------------------ %s HTTP response %s\n%s",
                call_str,
                res.status_code,
                format_xml_bytes(res.content).decode(),
            )
        if res.status_code >= 300:
            logger.error(
                "------------------------------------------------------ %s HTTP response %s\n%s",
                call_str,
                res.status_code,
                format_xml_bytes(res.content).decode(),
            )
            raise Exception("WS-PKI {} HTTP {}".format(command, res.status_code))

        process_wspki_response(res.content, soap_call)

        soap_call.executed = now()
        soap_call.save(update_fields=["executed"])
        return res.content
    except Exception:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=["error"])
        raise
