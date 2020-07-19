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


def write_pem_file(filename: str, cert_base64: str):
    """
    Writes PEM data to file.
    :param filename: PEM filename
    :param cert_base64: Base64 encoded certificate data without BEGIN CERTIFICATE / END CERTIFICATE
    """
    with open(filename, 'wt') as fp:
        fp.write('-----BEGIN CERTIFICATE-----\n')
        blocks = cert_base64
        while blocks:
            block = blocks[:64]
            fp.write(block + '\n')
            blocks = blocks[64:]
        fp.write('-----END CERTIFICATE-----\n')
        logger.info('%s written', filename)
