from datetime import datetime
from typing import Tuple
import pytz
import cryptography
from cryptography import x509


def get_x509_cert_from_file(filename: str):
    """
    Returns not_valid_before, not_valid_after pair
    """
    pem_data = open(filename, 'rb').read()
    return x509.load_pem_x509_certificate(pem_data, cryptography.hazmat.backends.default_backend())


def get_x509_cert_validity_from_file(filename: str) -> Tuple[datetime, datetime]:
    """
    Returns not_valid_before, not_valid_after pair in UTC
    """
    cert = get_x509_cert_from_file(filename)
    return pytz.utc.localize(cert.not_valid_before), pytz.utc.localize(cert.not_valid_after)
