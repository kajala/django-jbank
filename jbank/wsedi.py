import base64
import logging
import re
import traceback
from os.path import basename
from pprint import pprint
import pytz
import requests
from django.conf import settings
from django.template.loader import get_template
from django.utils.timezone import now
from zeep.wsse import BinarySignature
from jbank.models import WsEdiConnection, WsEdiSoapCall, Payout


logger = logging.getLogger(__name__)


def wsedi_get(command: str, file_type: str, status: str, file_reference: str='', verbose: bool=False) -> requests.Response:
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
    url = settings.WSEDI_URL + '?command={command}'.format(command=command)
    if file_reference:
        url += '&file-reference=' + file_reference
    if file_type:
        url += '&file-type=' + file_type
    if status:
        url += '&status=' + status
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Token ' + settings.WSEDI_TOKEN,
    }
    res = requests.get(url, headers=headers)
    if res.status_code >= 300:
        logger.error("wsedi_get(command={}, file_type={}, status={}, file_reference={}) response HTTP {}:\n".format(command, file_type, status, file_reference, res.status_code) + res.text)
    elif verbose:
        logger.info("wsedi_get(command={}, file_type={}, status={}, file_reference={}) response HTTP {}:\n".format(command, file_type, status, file_reference, res.status_code) + res.text)
    return res


def wsedi_upload_file(file_content: str, file_type: str, file_name: str, verbose: bool=False) -> requests.Response:
    """
    Upload Finnish bank file. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
    Uses project settings WSEDI_URL and WSEDI_TOKEN.
    :param file_content: File content
    :param file_type: File type, e.g. pain.001.001.03
    :param file_name: File (base) name
    :param verbose: Debug output
    :return: requests.Response
    """
    command = 'UploadFile'
    url = settings.WSEDI_URL
    data = {
        'command': command,
        'file-type': file_type,
        'file-name': basename(file_name),
        'file-content': base64.b64encode(file_content.encode('utf8')).decode('ascii'),
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Token ' + settings.WSEDI_TOKEN,
    }
    res = requests.post(url, data=data, headers=headers)
    if res.status_code >= 300:
        logger.error("wsedi_upload_file(command={}, file_type={}, file_name={}) response HTTP {}:\n".format(command, file_type, file_name, res.status_code) + res.text)
    elif verbose:
        logger.info("wsedi_upload_file(command={}, file_type={}, file_name={}) response HTTP {}:\n".format(command, file_type, file_name, res.status_code) + res.text)
    return res


def dbg_read(filename: str):
    return open('/home/jani/Downloads/{}'.format(filename), 'rb').read()


def dbg_write(filename: str, content: bytes):
    return open('/home/jani/Downloads/{}'.format(filename), 'wb').write(content)


def wsedi_execute(ws: WsEdiConnection, cmd: str, payout: Payout or None = None, verbose: bool = False):
    """
    Debug: ws = WsEdiConnection.objects.first(); from jbank.wsedi import *
    :param ws:
    :param cmd:
    :param payout:
    :param verbose:
    :return:
    """
    from lxml import etree

    soap_call = WsEdiSoapCall(connection=ws, command=cmd, payout=payout)
    soap_call.full_clean()
    soap_call.save()
    try:
        app = ws.get_application_request(cmd)
        if verbose:
            print('------------------------------------------------------ app\n{}'.format(app))
        signed_app = ws.sign_application_request(app)
        if verbose:
            print('------------------------------------------------------ signed_app\n{}'.format(signed_app.decode()))
        enc_app = ws.encrypt_application_request(signed_app)
        if verbose:
            print('------------------------------------------------------ enc_app\n{}'.format(enc_app.decode()))
        b64_app = ws.encode_application_request(enc_app)
        if verbose:
            print('------------------------------------------------------ b64_app\n{}'.format(b64_app.decode()))
        soap_body = get_template('jbank/soap_template2.xml').render({
            'soap_call': soap_call,
            'payload': b64_app.decode(),
        })
        if verbose:
            print('------------------------------------------------------ soap_body\n{}'.format(soap_body))
        body_bytes = soap_body.encode()
        envelope = etree.fromstring(body_bytes)
        binary_signature = BinarySignature(ws.signing_key_full_path, ws.signing_cert_full_path)
        soap_headers = {
        }
        envelope, soap_headers = binary_signature.apply(envelope, soap_headers)
        signed_body_bytes = etree.tostring(envelope)
        if verbose:
            print('------------------------------------------------------ signed_body_bytes\n{}'.format(signed_body_bytes))
        http_headers = {
            'Connection': 'Close',
            'Content-Type': 'text/xml',
            'Method': 'POST',
            'SOAPAction': '',
            'User-Agent': 'Kajala WS',
        }
        if verbose:
            print('HTTP POST {}'.format(ws.soap_endpoint))
        res = requests.post(ws.soap_endpoint, data=signed_body_bytes, headers=http_headers)
        if verbose:
            print('HTTP {}'.format(res.status_code))
            print(res.text)
    except Exception as e:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=['error'])
        raise
