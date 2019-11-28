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


def wsedi_execute(ws: WsEdiConnection, cmd: str, payout: Payout or None = None):
    soap_call = WsEdiSoapCall(connection=ws, command=cmd, payout=payout)
    soap_call.full_clean()
    soap_call.save()
    try:
        app = ws.get_application_request(cmd)
        signed_app = ws.sign_application_request(app)
        enc_app = ws.encrypt_application_request(signed_app)
        b64_app = ws.encode_application_request(enc_app)
        soap_body = get_template('jbank/soap_template.xml').render({
            'soap_call': soap_call,
            'payload': b64_app,
        })
        print(soap_body)
    except Exception as e:
        soap_call.error = traceback.format_exc()
        soap_call.save(update_fields=['error'])
        raise
