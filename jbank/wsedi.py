# pylint: disable=logging-format-interpolation,logging-not-lazy,too-many-arguments,too-many-locals,too-many-statements,c-extension-no-member
import base64
import logging
import traceback
from datetime import date, timedelta, datetime
from typing import Callable, Optional
import requests
import xmlsec
from django.template.loader import get_template
from django.utils.timezone import now
from django.utils.translation import gettext as _
from lxml import etree  # type: ignore
from zeep.wsse import BinarySignature  # type: ignore
from zeep import ns
from jbank.models import WsEdiConnection, WsEdiSoapCall

logger = logging.getLogger(__name__)


def wsse_insert_timestamp(envelope: etree.Element, timestamp: datetime, expires_seconds: int = 3600):
    """Inserts <wsu:Timestamp> element to the beginning of <wsse:Security>."""
    soap_header = envelope.find("{http://schemas.xmlsoap.org/soap/envelope/}Header")
    if soap_header is not None:
        soap_security = soap_header.find("{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}Security")
        if soap_security is not None:
            soap_timestamp = etree.Element(etree.QName(ns.WSU, "Timestamp"), {etree.QName(ns.WSU, "Id"): "timestamp"})
            soap_timestamp_created = etree.SubElement(soap_timestamp, etree.QName(ns.WSU, "Created"))
            soap_timestamp_created.text = timestamp.isoformat()
            soap_timestamp_expires = etree.SubElement(soap_timestamp, etree.QName(ns.WSU, "Expires"))
            soap_timestamp_expires.text = (timestamp + timedelta(seconds=expires_seconds)).isoformat()
            soap_security.insert(0, soap_timestamp)


def wsedi_execute(  # noqa
    ws: WsEdiConnection,
    command: str,
    file_type: str = "",
    status: str = "",
    file_reference: str = "",  # noqa
    file_content: str = "",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    verbose: bool = False,
    cls: Callable = WsEdiSoapCall,
    **kwargs
) -> bytes:
    """
    Args:
        ws
        command
        file_type
        status
        file_reference
        file_content
        start_date
        end_date
        verbose
        cls

    Returns:
        bytes
    """
    if ws and not ws.enabled:
        raise Exception(_("ws.edi.connection.not.enabled").format(ws=ws))

    soap_call = cls(connection=ws, command=command, **kwargs)
    soap_call.full_clean()
    soap_call.save()
    call_str = "WsEdiSoapCall({})".format(soap_call.id)
    try:
        content = ""
        if file_content:
            content = base64.b64encode(file_content.encode()).decode("ascii")

        app = ws.get_application_request(
            command,
            file_type=file_type,
            status=status,
            file_reference=file_reference,
            content=content,
            start_date=start_date,
            end_date=end_date,
        )
        if verbose:
            logger.info("------------------------------------------------------ {} app\n{}".format(call_str, app.decode()))
        debug_output = command in ws.debug_command_list or "ALL" in ws.debug_command_list
        if debug_output:
            with open(soap_call.debug_request_full_path, "wb") as fp:
                fp.write(app)

        signed_app = ws.sign_application_request(app)
        if verbose:
            logger.info("------------------------------------------------------ {} signed_app\n{}".format(call_str, signed_app.decode()))

        if ws.bank_encryption_cert_file:
            enc_app = ws.encrypt_application_request(signed_app)
            if verbose:
                logger.info("------------------------------------------------------ {} enc_app\n{}".format(call_str, enc_app.decode()))
        else:
            enc_app = signed_app
            if verbose:
                logger.info(
                    "------------------------------------------------------ " "{} enc_app\n(no bank_encryption_cert_file, not encrypting)".format(call_str)
                )

        b64_app = ws.encode_application_request(enc_app)
        if verbose:
            logger.info("------------------------------------------------------ {} b64_app\n{}".format(call_str, b64_app.decode()))

        soap_body = get_template("jbank/soap_template.xml").render(
            {
                "soap_call": soap_call,
                "payload": b64_app.decode(),
            }
        )
        if verbose:
            logger.info("------------------------------------------------------ {} soap_body\n{}".format(call_str, soap_body))

        body_bytes = soap_body.encode()
        envelope = etree.fromstring(body_bytes)
        if ws.use_sha256:
            signature_method = xmlsec.constants.TransformRsaSha256
            digest_method = xmlsec.constants.TransformSha256
        else:
            signature_method = None
            digest_method = None
        binary_signature = BinarySignature(ws.signing_key_full_path, ws.signing_cert_full_path, signature_method=signature_method, digest_method=digest_method)
        soap_headers: dict = {}
        envelope, soap_headers = binary_signature.apply(
            envelope, soap_headers
        )  # if you get AttributeError: 'NoneType' object has no attribute 'text' see https://bugs.launchpad.net/lxml/+bug/1960668/
        if ws.use_wsse_timestamp:
            wsse_insert_timestamp(envelope, soap_call.created)
        signed_body_bytes = etree.tostring(envelope)
        if verbose:
            logger.info("------------------------------------------------------ {} HTTP POST\n{}".format(call_str, ws.soap_endpoint))
            logger.info(
                "------------------------------------------------------ {} signed_body_bytes.decode()\n{}".format(call_str, signed_body_bytes.decode())
            )

        http_headers = {
            "Connection": "Close",
            "Content-Type": "text/xml",
            "Method": "POST",
            "SOAPAction": "",
            "User-Agent": "Kajala WS",
        }
        res = requests.post(ws.soap_endpoint, data=signed_body_bytes, headers=http_headers, timeout=600)
        if verbose:
            logger.info("------------------------------------------------------ {} HTTP response {}\n{}".format(call_str, res.status_code, res.text))
        if res.status_code >= 300:
            logger.error("------------------------------------------------------ {} HTTP response {}\n{}".format(call_str, res.status_code, res.text))
            raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))

        envelope = etree.fromstring(res.content)
        app_res_el = envelope.find(".//{http://model.bxd.fi}ApplicationResponse")
        if app_res_el is None:
            logger.error("------------------------------------------------------ {} HTTP response {}\n{}".format(call_str, res.status_code, res.text))
            raise Exception("WS-EDI {} failed, missing ApplicationResponse".format(command))
        app_res_enc = ws.decode_application_response(app_res_el.text.encode())
        if verbose:
            logger.info("------------------------------------------------------ {} app_res_enc\n{}".format(call_str, app_res_enc.decode()))

        if ws.encryption_key_file and ws.encryption_cert_file:
            app_res = ws.decrypt_application_response(app_res_enc)
            if verbose:
                logger.info("------------------------------------------------------ {} app_res\n{}".format(call_str, app_res.decode()))
        else:
            app_res = app_res_enc
            if verbose:
                logger.info(
                    "------------------------------------------------------ "
                    "{} app_res\n(no encryption_key_file or encryption_cert_file, assuming decrypted content)".format(call_str)
                )

        soap_call.executed = now()
        soap_call.save(update_fields=["executed"])

        if debug_output:
            with open(soap_call.debug_response_full_path, "wb") as fp:
                fp.write(app_res)

        return app_res
    except Exception:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=["error"])
        raise
