"""A scratch CA and a server certificate for relay.localhost, localhost and 127.0.0.1, valid for two days.

    python tests/live/phonelink_scratch_cert.py <folder>

Writes ca.pem, cert.pem and key.pem. tests/live/phonelink_emulator.sh serves the PhoneLink harness relay with
cert.pem and installs ca.pem as a user CA in the emulator, which only debug builds of the phone app trust.
"""

import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc)
ca_key = ec.generate_private_key(ec.SECP256R1())
ca_name = x509.Name(
    [x509.NameAttribute(NameOID.COMMON_NAME, "Kollab scratch relay CA")]
)
ca = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(minutes=5))
    .not_valid_after(now + datetime.timedelta(days=2))
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(
        x509.KeyUsage(False, False, False, False, False, True, True, False, False),
        critical=True,
    )
    .sign(ca_key, hashes.SHA256())
)
key = ec.generate_private_key(ec.SECP256R1())
cert = (
    x509.CertificateBuilder()
    .subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "relay.localhost")])
    )
    .issuer_name(ca_name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(minutes=5))
    .not_valid_after(now + datetime.timedelta(days=2))
    .add_extension(
        x509.SubjectAlternativeName(
            [
                x509.DNSName("relay.localhost"),
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]
        ),
        critical=False,
    )
    .add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
        critical=False,
    )
    .sign(ca_key, hashes.SHA256())
)
(out / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
(out / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(out / "key.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
print(f"wrote ca.pem cert.pem key.pem in {out}")
