import logging
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management.base import CommandParser
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Generates RSA private key and x509 certificate in .pem format (for testing)
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--key-file", type=str, default="key.pem")
        parser.add_argument("--cert-file", type=str, default="cert.pem")
        parser.add_argument("--country", type=str, default="US")
        parser.add_argument("--state", type=str, default="TX")
        parser.add_argument("--locality", type=str, default="Dallas")
        parser.add_argument("--org-name", type=str, default="Kajala Group")
        parser.add_argument("--common-name", type=str, default="kajala.com")

    def do(self, *args, **options):
        # Generate our key
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())

        # Write to disk unencrypted
        with open(options["key_file"], "wb") as f:
            f.write(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            print("{} written".format(f.name))

        # Various details about who we are. For a self-signed certificate the
        # subject and issuer are always the same.
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(x509.NameOID.COUNTRY_NAME, options["country"]),
                x509.NameAttribute(x509.NameOID.STATE_OR_PROVINCE_NAME, options["state"]),
                x509.NameAttribute(x509.NameOID.LOCALITY_NAME, options["locality"]),
                x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, options["org_name"]),
                x509.NameAttribute(x509.NameOID.COMMON_NAME, options["common_name"]),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(
                # Our certificate will be valid for 10 days
                datetime.utcnow()
                + timedelta(days=10)
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256(), default_backend())
        )

        # Write our certificate out to disk.
        with open(options["cert_file"], "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            print("{} written".format(f.name))
