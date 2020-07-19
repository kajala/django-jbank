import cryptography
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


def create_private_key(public_exponent: int = 65537, key_size: int = 2048) -> RSAPrivateKey:
    backend = cryptography.hazmat.backends.default_backend()
    return cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key(
        public_exponent=public_exponent,
        key_size=key_size,
        backend=backend
    )


def get_private_key_pem(private_key: RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )


def strip_pem_header_and_footer(pem: bytes) -> bytes:
    if not pem.startswith(b'-----BEGIN '):
        raise Exception('PEM does not appear to have header: {}...'.format(pem[:32].decode() + '...'))
    return b'\n'.join(pem.split(b'\n')[1:-2])


def create_csr_pem(private_key: RSAPrivateKey, common_name: str, country_name: str, dn_qualifier: str = '',
                   business_category: str = '', domain_component: str = '', email_address: str = '',
                   generation_qualifier: str = '', given_name: str = '', jurisdiction_country_name: str = '',
                   jurisdiction_locality_name: str = '', jurisdiction_state_or_province_name: str = '',
                   locality_name: str = '', organizational_unit_name: str = '', organization_name: str = '',
                   postal_address: str = '', postal_code: str = '', pseudonym: str = '', serial_number: str = '',
                   state_or_province_name: str = '', street_address: str = '', surname: str = '',
                   title: str = '', user_id: str = '', x500_unique_identifier: str = '') -> bytes:
    pairs = [
        (common_name, 'COMMON_NAME'),
        (country_name, 'COUNTRY_NAME'),
        (dn_qualifier, 'DN_QUALIFIER'),
        (business_category, 'BUSINESS_CATEGORY'),
        (domain_component, 'DOMAIN_COMPONENT'),
        (email_address, 'EMAIL_ADDRESS'),
        (generation_qualifier, 'GENERATION_QUALIFIER'),
        (given_name, 'GIVEN_NAME'),
        (jurisdiction_country_name, 'JURISDICTION_COUNTRY_NAME'),
        (jurisdiction_locality_name, 'JURISDICTION_LOCALITY_NAME'),
        (jurisdiction_state_or_province_name, 'JURISDICTION_STATE_OR_PROVINCE_NAME'),
        (locality_name, 'LOCALITY_NAME'),
        (organizational_unit_name, 'ORGANIZATIONAL_UNIT_NAME'),
        (organization_name, 'ORGANIZATION_NAME'),
        (postal_address, 'POSTAL_ADDRESS'),
        (postal_code, 'POSTAL_CODE'),
        (pseudonym, 'PSEUDONYM'),
        (serial_number, 'SERIAL_NUMBER'),
        (state_or_province_name, 'STATE_OR_PROVINCE_NAME'),
        (street_address, 'STREET_ADDRESS'),
        (surname, 'SURNAME'),
        (title, 'TITLE'),
        (user_id, 'USER_ID'),
        (x500_unique_identifier, 'X500_UNIQUE_IDENTIFIER'),
    ]
    name_parts = []
    for val, k in pairs:
        if val:
            name_parts.append(x509.NameAttribute(getattr(x509.oid.NameOID, k), val))

    builder = x509.CertificateSigningRequestBuilder()
    builder = builder.subject_name(x509.Name(name_parts))
    backend = cryptography.hazmat.backends.default_backend()
    request = builder.sign(private_key, hashes.SHA256(), backend)
    assert isinstance(request, x509.CertificateSigningRequest)
    return request.public_bytes(serialization.Encoding.PEM)
