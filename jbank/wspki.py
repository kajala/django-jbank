# pylint: disable=c-extension-no-member
import base64
import logging
import traceback
from typing import Optional
import requests
from django.utils.timezone import now
from django.utils.translation import gettext as _
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
from jutil.format import get_media_full_path, format_xml_bytes, camel_case_to_underscore

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
    Args:
        el: Root Element
        ns: Target namespace
        tag: Target tag

    Returns:
        Found Element
    """
    if not ns.startswith("{"):
        ns = "{" + ns + "}"
    els = list(el.iter("{}{}".format(ns, tag)))
    if not els:
        raise Exception("{} not found from {}".format(tag, el))
    if len(els) > 1:
        raise Exception("{} found from {} more than once".format(tag, el))
    return els[0]


def strip_xml_header_bytes(xml: bytes) -> bytes:
    return b"\n".join(xml.split(b"\n")[1:])


def generate_wspki_request(  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    soap_call: WsEdiSoapCall, payout_party: PayoutParty, lowercase_environment: bool = False
) -> bytes:
    ws = soap_call.connection
    command = soap_call.command
    command_lower = command.lower()
    opt_sha256 = "-sha256" if ws.use_sha256 else ""

    if command_lower == "getcertificate":
        soap_template_name = "jbank/pki_get_certificate_soap_template.xml"
    else:
        soap_template_name = "jbank/pki_soap_template.xml"
    soap_body_bytes = ws.get_pki_template(soap_template_name, soap_call, lowercase_environment=lowercase_environment)
    envelope = etree.fromstring(soap_body_bytes)
    if "opc" in envelope.nsmap:
        pkif_ns = "{" + envelope.nsmap["opc"] + "}"
        elem_ns = pkif_ns
    else:
        for ns_name in ["elem", "pkif"]:
            if ns_name not in envelope.nsmap:
                raise Exception("WS-PKI {} SOAP template invalid, '{}' namespace missing".format(command, ns_name))
        pkif_ns = "{" + envelope.nsmap["pkif"] + "}"
        elem_ns = "{" + envelope.nsmap["elem"] + "}"
    req_hdr_el = etree_get_element(envelope, pkif_ns, "RequestHeader")
    cmd_el = req_hdr_el.getparent()

    if command_lower in ["getbankcertificate"]:
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

    elif command_lower in ["createcertificate", "renewcertificate", "getcertificate"]:
        old_signing_cert = ws.signing_cert if ws.signing_cert_file else None
        old_signing_key_full_path = ws.signing_key_full_path if ws.signing_key_file else ""
        old_signing_cert_full_path = ws.signing_cert_full_path if ws.signing_cert_file else ""
        is_renewable = bool(ws.signing_cert_file and ws.signing_key_file)
        is_renew = command_lower == "renewcertificate" or command_lower == "getcertificate" and is_renewable and not ws.pin
        is_create = command_lower in ["createcertificate", "getcertificate"] and not is_renew
        is_encrypted = command_lower in ["createcertificate", "renewcertificate"] and bool(ws.bank_encryption_cert_file)
        if is_renew and command_lower == "getcertificate":
            template_name = f"pki_get_certificate_renew_request_template{opt_sha256}.xml"
        else:
            template_name = "pki_" + camel_case_to_underscore(command) + "_request_template.xml"

        if is_create or is_renew:
            logger.info(
                "To restore old connection:\nws=WsEdiConnection.objects.get(id=%s); ws.signing_cert_file='%s'; ws.signing_key_file='%s'; ws.encryption_cert_file='%s'; ws.encryption_key_file='%s'; ws.save()",  # noqa
                ws.id,
                ws.signing_cert_file,
                ws.signing_key_file,
                ws.encryption_cert_file,
                ws.encryption_key_file,
            )
            encryption_pk = create_private_key()
            signing_pk = create_private_key()
            encryption_pk_pem = get_private_key_pem(encryption_pk)
            signing_pk_pem = get_private_key_pem(signing_pk)
            encryption_pk_filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, "EncryptionKey")
            signing_pk_filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, "SigningKey")
            ws.old_encryption_key_file = ws.encryption_key_file
            ws.old_signing_key_file = ws.signing_key_file
            ws.encryption_key_file.name = encryption_pk_filename
            ws.signing_key_file.name = signing_pk_filename
            ws.save()
            admin_log(
                [ws],
                "Encryption and signing private keys set as {} and {}".format(encryption_pk_filename, signing_pk_filename),
            )
            write_private_key_pem_file(get_media_full_path(encryption_pk_filename), encryption_pk_pem)
            write_private_key_pem_file(get_media_full_path(signing_pk_filename), signing_pk_pem)
        else:
            encryption_pk = load_private_key_from_pem_file(ws.encryption_key_full_path) if is_encrypted else None  # type: ignore
            signing_pk = load_private_key_from_pem_file(ws.signing_key_full_path)

        csr_params = {
            "common_name": payout_party.name,
            "organization_name": payout_party.name,
            "country_name": payout_party.country_code,
            "organizational_unit_name": "IT-services",
            "locality_name": "Helsinki",
            "state_or_province_name": "Uusimaa",
            "surname": ws.sender_identifier,
        }
        encryption_csr = create_csr_pem(encryption_pk, **csr_params) if is_encrypted else None
        logger.info("encryption_csr: %s", encryption_csr)
        signing_csr = create_csr_pem(signing_pk, **csr_params)
        logger.info("signing_csr: %s", signing_csr)
        req = ws.get_pki_template(
            "jbank/" + template_name,
            soap_call,
            **{
                "encryption_cert_pkcs10": strip_pem_header_and_footer(encryption_csr).decode().replace("\n", "") if is_encrypted else None,  # type: ignore
                "signing_cert_pkcs10": strip_pem_header_and_footer(signing_csr).decode().replace("\n", ""),
                "old_signing_cert": old_signing_cert if is_renew else None,
                "lowercase_environment": lowercase_environment,
            },
        )
        logger.info("%s request:\n%s", command, format_xml_bytes(req).decode())

        if is_renew:
            req = ws.sign_pki_request(req, old_signing_key_full_path, old_signing_cert_full_path)
            logger.info("%s request signed:\n%s", command, format_xml_bytes(req).decode())

        if is_encrypted:
            logger.debug("Encrypting PKI request...")
            enc_req_bytes = ws.encrypt_pki_request(req)
            logger.info("%s request encrypted:\n%s", command, format_xml_bytes(enc_req_bytes).decode())
            req_el = etree.fromstring(enc_req_bytes)
            cmd_el.insert(cmd_el.index(req_hdr_el) + 1, req_el)
        else:
            logger.debug("Base64 encoding PKI request...")
            req_b64 = base64.encodebytes(req)
            req_el = etree.SubElement(cmd_el, "{}ApplicationRequest".format(elem_ns))
            req_el.text = req_b64

    elif command_lower in ["certificatestatus", "getowncertificatelist"]:
        cert = get_x509_cert_from_file(ws.signing_cert_full_path)
        req = ws.get_pki_template(
            "jbank/pki_certificate_status_request_template.xml",
            soap_call,
            **{
                "certs": [cert],
            },
        )
        logger.info("%s request:\n%s", command, format_xml_bytes(req).decode())

        req = ws.sign_pki_request(req, ws.signing_key_full_path, ws.signing_cert_full_path)
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
    command_lower = command.lower()
    envelope = etree.fromstring(content)

    # check for errors
    return_code: str = ""
    return_text: str = ""
    for el in envelope.iter():
        # print(el.tag)
        if el.tag and (el.tag.endswith("}ResponseCode") or el.tag.endswith("}ReturnCode")):
            return_code = el.text
            return_text_el = list(envelope.iter(el.tag[:-4] + "Text"))[0]
            return_text = return_text_el.text if return_text_el is not None else ""
    if return_code not in ["00", "0"]:
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
        elif ns_url.endswith("OPCertificateService"):
            pkif_ns = "{" + ns_url + "}"
            elem_ns = "{http://op.fi/mlp/xmldata/}"
    if not pkif_ns:
        raise Exception("WS-PKI {} SOAP response invalid, PKIFactoryService namespace missing".format(command))
    if not elem_ns:
        raise Exception("WS-PKI {} SOAP response invalid, PKIFactoryService/elements namespace missing".format(command))

    if command_lower == "getbankcertificate":
        res_el = etree_get_element(envelope, elem_ns, command + "Response")
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

    elif command_lower == "createcertificate":
        res_el = etree_get_element(envelope, elem_ns, command + "Response")
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

    elif command_lower == "renewcertificate":
        res_el = etree_get_element(envelope, elem_ns, command + "Response")
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

    elif command_lower == "getcertificate":
        app_res = envelope.find(
            "{http://schemas.xmlsoap.org/soap/envelope/}Body/{http://mlp.op.fi/OPCertificateService}getCertificateout/{http://mlp.op.fi/OPCertificateService}ApplicationResponse"  # noqa
        )
        if app_res is None:
            raise Exception("{} not found from {}".format("ApplicationResponse", envelope))
        data_base64 = base64.decodebytes(str(app_res.text).encode())
        cert_app_res = etree.fromstring(data_base64)
        if cert_app_res is None:
            raise Exception("Failed to create XML document from decoded ApplicationResponse")
        cert_el = cert_app_res.find("./{http://op.fi/mlp/xmldata/}Certificates/{http://op.fi/mlp/xmldata/}Certificate/{http://op.fi/mlp/xmldata/}Certificate")
        if cert_el is None:
            raise Exception("{} not found from {}".format("Certificate", cert_app_res))
        cert_bytes = base64.decodebytes(str(cert_el.text).encode())
        cert_name = "SigningCert"
        filename = "certs/ws{}-{}-{}.pem".format(ws.id, soap_call.timestamp_digits, cert_name)
        cert_full_path = get_media_full_path(filename)
        with open(cert_full_path, "wb") as fp:
            fp.write(cert_bytes)
            logger.info("%s written", cert_full_path)
        ws.signing_cert_file.name = filename
        admin_log([ws], "soap_call(id={}): signing_cert_file={}".format(soap_call.id, filename))
        ws.save()

    else:
        raise Exception("{} unsupported".format(command))


def wspki_execute(  # pylint: disable=too-many-arguments
    ws: WsEdiConnection,
    payout_party: PayoutParty,
    command: str,
    soap_action_header: bool = False,
    xml_sig: bool = False,
    lowercase_environment: bool = False,
    verbose: bool = False,
) -> bytes:
    """
    Args:
        ws
        payout_party
        command
        soap_action_header
        xml_sig
        lowercase_environment
        use_sha256
        verbose

    Returns:
        str
    """
    if ws and not ws.enabled:
        raise Exception(_("ws.edi.connection.not.enabled").format(ws=ws))

    soap_call = WsEdiSoapCall(connection=ws, command=command)
    soap_call.full_clean()
    soap_call.save()
    logger.info("Executing %s", soap_call)
    try:
        http_headers = {
            "Connection": "Close",
            "Content-Type": "text/xml; charset=UTF-8",
            "Method": "POST",
            "SOAPAction": '"{}"'.format(command) if soap_action_header else "",
            "User-Agent": "Kajala WS",
        }

        body_bytes: bytes = generate_wspki_request(soap_call, payout_party, lowercase_environment=lowercase_environment)
        if xml_sig and not body_bytes.startswith(b'<?xml version="1.0"'):
            body_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body_bytes
        pki_endpoint = ws.pki_endpoint
        if verbose:
            logger.info("------------------------------------------------------ HTTP POST %s\n%s", now().isoformat(), pki_endpoint)
            logger.info(
                "------------------------------------------------------ HTTP headers\n%s",
                "\n".join(["{}: {}".format(k, v) for k, v in http_headers.items()]),
            )
            logger.info(
                "------------------------------------------------------ HTTP request body\n%s",
                body_bytes.decode(),
            )
        debug_output = command in ws.debug_command_list or "ALL" in ws.debug_command_list
        if debug_output and soap_call.debug_request_full_path:
            with open(soap_call.debug_request_full_path, "wb") as fp:
                fp.write(body_bytes)

        res = requests.post(pki_endpoint, data=body_bytes, headers=http_headers, timeout=120)
        if verbose and res.status_code < 300:
            logger.info(
                "------------------------------------------------------ HTTP response %s\n%s",
                res.status_code,
                format_xml_bytes(res.content).decode(),
            )
        if debug_output and soap_call.debug_response_full_path:
            with open(soap_call.debug_response_full_path, "wb") as fp:
                fp.write(res.content)
        if res.status_code >= 300:
            logger.error(
                "------------------------------------------------------ HTTP response %s\n%s",
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
