# pylint: disable=logging-format-interpolation,logging-not-lazy,too-many-arguments,too-many-locals,too-many-statements
import base64
import logging
import traceback
from datetime import date
from os.path import basename
from typing import Callable, Optional
import requests
from django.conf import settings
from django.template.loader import get_template
from django.utils.timezone import now
from django.utils.translation import ugettext as _
from zeep.wsse import BinarySignature  # type: ignore
from jbank.models import WsEdiConnection, WsEdiSoapCall
from lxml import etree  # type: ignore  # pytype: disable=import-error


logger = logging.getLogger(__name__)


def wsedi_get(
    command: str, file_type: str, status: str, file_reference: str = "", verbose: bool = False
) -> requests.Response:
    """
    Download Finnish bank files. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
    Uses project settings WSEDI_URL and WSEDI_TOKEN.
    :param command: Command, e.g. DownloadFileList or DownloadFile
    :param file_type: File type, e.g. TO or SVM
    :param status: Status, e.g. DLD or NEW
    :param file_reference: File reference (if command is DownloadFile)
    :param verbose: Debug output
    :return: requests.Response
    """
    url = settings.WSEDI_URL + "?command={command}".format(command=command)
    if file_reference:
        url += "&file-reference=" + file_reference
    if file_type:
        url += "&file-type=" + file_type
    if status:
        url += "&status=" + status
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Token " + settings.WSEDI_TOKEN,
    }
    res = requests.get(url, headers=headers)
    if res.status_code >= 300:
        logger.error(
            "wsedi_get(command={}, file_type={}, status={}, file_reference={}) response HTTP {}:\n".format(
                command, file_type, status, file_reference, res.status_code
            )
            + res.text
        )
    elif verbose:
        logger.info(
            "wsedi_get(command={}, file_type={}, status={}, file_reference={}) response HTTP {}:\n".format(
                command, file_type, status, file_reference, res.status_code
            )
            + res.text
        )

    if res.status_code >= 300:
        raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))
    return res


def wsedi_upload_file(file_content: str, file_type: str, file_name: str, verbose: bool = False) -> requests.Response:
    """
    Upload Finnish bank file. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
    Uses project settings WSEDI_URL and WSEDI_TOKEN.
    :param file_content: File content
    :param file_type: File type, e.g. pain.001.001.03
    :param file_name: File (base) name
    :param verbose: Debug output
    :return: requests.Response
    """
    command = "UploadFile"
    url = settings.WSEDI_URL
    data = {
        "command": command,
        "file-type": file_type,
        "file-name": basename(file_name),
        "file-content": base64.b64encode(file_content.encode("utf8")).decode("ascii"),
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Token " + settings.WSEDI_TOKEN,
    }
    res = requests.post(url, data=data, headers=headers)
    if res.status_code >= 300:
        logger.error(
            "wsedi_upload_file(command={}, file_type={}, file_name={}) response HTTP {}:\n".format(
                command, file_type, file_name, res.status_code
            )
            + res.text
        )
        raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))
    if verbose:
        logger.info(
            "wsedi_upload_file(command={}, file_type={}, file_name={}) response HTTP {}:\n".format(
                command, file_type, file_name, res.status_code
            )
            + res.text
        )
    return res


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
    :param ws:
    :param command:
    :param file_type:
    :param status:
    :param file_reference:
    :param file_content:
    :param start_date:
    :param end_date:
    :param verbose:
    :param cls:
    :return: bytes
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
            logger.info(
                "------------------------------------------------------ {} app\n{}".format(call_str, app.decode())
            )
        debug_output = command in ws.debug_command_list or "ALL" in ws.debug_command_list
        if debug_output:
            with open(soap_call.debug_request_full_path, "wb") as fp:
                fp.write(app)

        signed_app = ws.sign_application_request(app)
        if verbose:
            logger.info(
                "------------------------------------------------------ {} signed_app\n{}".format(
                    call_str, signed_app.decode()
                )
            )

        if ws.bank_encryption_cert_file:
            enc_app = ws.encrypt_application_request(signed_app)
            if verbose:
                logger.info(
                    "------------------------------------------------------ {} enc_app\n{}".format(
                        call_str, enc_app.decode()
                    )
                )
        else:
            enc_app = signed_app
            if verbose:
                logger.info(
                    "------------------------------------------------------ "
                    "{} enc_app\n(no bank_encryption_cert_file, not encrypting)".format(call_str)
                )

        b64_app = ws.encode_application_request(enc_app)
        if verbose:
            logger.info(
                "------------------------------------------------------ {} b64_app\n{}".format(
                    call_str, b64_app.decode()
                )
            )

        soap_body = get_template("jbank/soap_template.xml").render(
            {
                "soap_call": soap_call,
                "payload": b64_app.decode(),
            }
        )
        if verbose:
            logger.info(
                "------------------------------------------------------ {} soap_body\n{}".format(call_str, soap_body)
            )

        body_bytes = soap_body.encode()
        envelope = etree.fromstring(body_bytes)
        binary_signature = BinarySignature(ws.signing_key_full_path, ws.signing_cert_full_path)
        soap_headers: dict = {}
        envelope, soap_headers = binary_signature.apply(envelope, soap_headers)
        signed_body_bytes = etree.tostring(envelope)
        if verbose:
            logger.info(
                "------------------------------------------------------ {} signed_body_bytes\n{}".format(
                    call_str, signed_body_bytes
                )
            )

        http_headers = {
            "Connection": "Close",
            "Content-Type": "text/xml",
            "Method": "POST",
            "SOAPAction": "",
            "User-Agent": "Kajala WS",
        }
        if verbose:
            logger.info("HTTP POST {}".format(ws.soap_endpoint))
        res = requests.post(ws.soap_endpoint, data=signed_body_bytes, headers=http_headers)
        if verbose:
            logger.info(
                "------------------------------------------------------ {} HTTP response {}\n{}".format(
                    call_str, res.status_code, res.text
                )
            )
        if res.status_code >= 300:
            logger.error(
                "------------------------------------------------------ {} HTTP response {}\n{}".format(
                    call_str, res.status_code, res.text
                )
            )
            raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))

        envelope = etree.fromstring(res.content)
        app_res_el = envelope.find(".//{http://model.bxd.fi}ApplicationResponse")
        if app_res_el is None:
            logger.error(
                "------------------------------------------------------ {} HTTP response {}\n{}".format(
                    call_str, res.status_code, res.text
                )
            )
            raise Exception("WS-EDI {} failed, missing ApplicationResponse".format(command))
        app_res_enc = ws.decode_application_response(app_res_el.text.encode())
        if verbose:
            logger.info(
                "------------------------------------------------------ {} app_res_enc\n{}".format(
                    call_str, app_res_enc.decode()
                )
            )

        if ws.encryption_key_file:
            app_res = ws.decrypt_application_response(app_res_enc)
            if verbose:
                logger.info(
                    "------------------------------------------------------ {} app_res\n{}".format(
                        call_str, app_res.decode()
                    )
                )
        else:
            app_res = app_res_enc
            if verbose:
                logger.info(
                    "------------------------------------------------------ "
                    "{} app_res\n(no encryption_key_file, assuming decrypted content)".format(call_str)
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
