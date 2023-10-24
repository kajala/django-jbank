import logging
from cryptography import x509
from django.core.exceptions import ValidationError
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


def get_x509_cert_from_file(filename: str) -> x509.Certificate:
    """
    Load X509 certificate from file.
    """
    with open(filename, "rb") as fp:
        pem_data = fp.read()
    return x509.load_pem_x509_certificate(pem_data, default_backend())  # noqa


def write_cert_pem_file(filename: str, cert_base64: bytes):
    """Writes PEM data to file.

    Args:
        filename: PEM filename
        cert_base64: Base64 encoded certificate data without BEGIN CERTIFICATE / END CERTIFICATE
    """
    if b"BEGIN" in cert_base64 or b"END" in cert_base64:
        raise ValidationError("write_cert_pem_file() assumes PEM data does not contain header/footer")
    with open(filename, "wb") as fp:
        fp.write(b"-----BEGIN CERTIFICATE-----\n")
        blocks = cert_base64
        while blocks:
            block = blocks[:64]
            fp.write(block.strip() + b"\n")
            blocks = blocks[64:]
        fp.write(b"-----END CERTIFICATE-----\n")
        logger.info("%s written", filename)
