import logging
import cryptography
from cryptography import x509


logger = logging.getLogger(__name__)


def get_x509_cert_from_file(filename: str) -> x509.Certificate:
    """
    Load X509 certificate from file.
    """
    pem_data = open(filename, 'rb').read()
    return x509.load_pem_x509_certificate(pem_data, cryptography.hazmat.backends.default_backend())


def write_pem_file(filename: str, cert_base64: bytes):
    """
    Writes PEM data to file.
    :param filename: PEM filename
    :param cert_base64: Base64 encoded certificate data without BEGIN CERTIFICATE / END CERTIFICATE
    """
    with open(filename, 'wb') as fp:
        fp.write(b'-----BEGIN CERTIFICATE-----\n')
        blocks = cert_base64
        while blocks:
            block = blocks[:64]
            fp.write(block + b'\n')
            blocks = blocks[64:]
        fp.write(b'-----END CERTIFICATE-----\n')
        logger.info('%s written', filename)
