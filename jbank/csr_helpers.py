import logging
from typing import Optional
import cryptography
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from django.core.exceptions import ValidationError


logger = logging.getLogger(__name__)


def create_private_key(public_exponent: int = 65537, key_size: int = 2048) -> RSAPrivateKey:
    """
    Creates RSA private key.
    :param public_exponent: int, exponent
    :param key_size: int, bits
    :return: RSAPrivateKey
    """
    return cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key(public_exponent=public_exponent, key_size=key_size)


def get_public_key_pem(public_key: RSAPublicKey) -> bytes:
    pem = public_key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem


def get_private_key_pem(private_key: RSAPrivateKey) -> bytes:
    """Returns private key PEM file bytes.

    Args:
        private_key: RSPrivateKey

    Returns:
        bytes
    """
    return private_key.private_bytes(  # type: ignore
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_key_from_pem_data(pem_data: bytes, password: Optional[bytes] = None) -> RSAPrivateKey:
    res = load_pem_private_key(pem_data, password=password)
    assert isinstance(res, RSAPrivateKey)
    return res


def load_private_key_from_pem_file(filename: str, password: Optional[bytes] = None) -> RSAPrivateKey:
    with open(filename, "rb") as fp:
        return load_private_key_from_pem_data(fp.read(), password)


def write_private_key_pem_file(filename: str, key_base64: bytes):
    """Writes PEM data to file.

    Args:
        filename: PEM filename
        key_base64: Base64 encoded certificate data without BEGIN CERTIFICATE / END CERTIFICATE
    """
    if b"BEGIN" not in key_base64 or b"END" not in key_base64:
        raise ValidationError("write_private_key_pem_file() assumes PEM data does contains BEGIN / END header and footer")
    with open(filename, "wb") as fp:
        fp.write(key_base64)
        logger.info("%s written", filename)


def strip_pem_header_and_footer(pem: bytes) -> bytes:
    """Strips -----BEGIN and -----END parts of the CSR PEM.

    Args:
        pem: bytes

    Returns:
        bytes
    """
    if not pem.startswith(b"-----BEGIN "):
        raise Exception("PEM does not appear to have header: {}...".format(pem[:32].decode() + "..."))
    return b"\n".join(pem.split(b"\n")[1:-2])


def create_csr_pem(  # pylint: disable=too-many-arguments,too-many-locals
    private_key: RSAPrivateKey,
    common_name: str,
    country_name: str,
    dn_qualifier: str = "",
    business_category: str = "",
    domain_component: str = "",
    email_address: str = "",
    generation_qualifier: str = "",
    given_name: str = "",
    jurisdiction_country_name: str = "",
    jurisdiction_locality_name: str = "",
    jurisdiction_state_or_province_name: str = "",
    locality_name: str = "",
    organizational_unit_name: str = "",
    organization_name: str = "",
    postal_address: str = "",
    postal_code: str = "",
    pseudonym: str = "",
    serial_number: str = "",
    state_or_province_name: str = "",
    street_address: str = "",
    surname: str = "",
    title: str = "",
    user_id: str = "",
    x500_unique_identifier: str = "",
) -> bytes:
    """See http://fileformats.archiveteam.org/wiki/PKCS10

    Returns:
        CSR PEM as bytes
    """
    pairs = [
        (common_name, "COMMON_NAME"),
        (country_name, "COUNTRY_NAME"),
        (dn_qualifier, "DN_QUALIFIER"),
        (business_category, "BUSINESS_CATEGORY"),
        (domain_component, "DOMAIN_COMPONENT"),
        (email_address, "EMAIL_ADDRESS"),
        (generation_qualifier, "GENERATION_QUALIFIER"),
        (given_name, "GIVEN_NAME"),
        (jurisdiction_country_name, "JURISDICTION_COUNTRY_NAME"),
        (jurisdiction_locality_name, "JURISDICTION_LOCALITY_NAME"),
        (jurisdiction_state_or_province_name, "JURISDICTION_STATE_OR_PROVINCE_NAME"),
        (locality_name, "LOCALITY_NAME"),
        (organizational_unit_name, "ORGANIZATIONAL_UNIT_NAME"),
        (organization_name, "ORGANIZATION_NAME"),
        (postal_address, "POSTAL_ADDRESS"),
        (postal_code, "POSTAL_CODE"),
        (pseudonym, "PSEUDONYM"),
        (serial_number, "SERIAL_NUMBER"),
        (state_or_province_name, "STATE_OR_PROVINCE_NAME"),
        (street_address, "STREET_ADDRESS"),
        (surname, "SURNAME"),
        (title, "TITLE"),
        (user_id, "USER_ID"),
        (x500_unique_identifier, "X500_UNIQUE_IDENTIFIER"),
    ]
    name_parts = []
    for val, k in pairs:
        if val:
            name_parts.append(x509.NameAttribute(getattr(x509.oid.NameOID, k), val))

    builder = x509.CertificateSigningRequestBuilder()
    builder = builder.subject_name(x509.Name(name_parts))
    request = builder.sign(private_key, hashes.SHA256())
    assert isinstance(request, x509.CertificateSigningRequest)
    return request.public_bytes(serialization.Encoding.PEM)
