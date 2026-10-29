"""TLS front for the proof relay: makes a throwaway CA, then terminates TLS on
:443 and pipes bytes to the relay on 127.0.0.1:<port>. Test harness only.

    python tls_proxy.py <host> <upstream_port> <cert_dir>
"""

import asyncio
import datetime
import ssl
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_certs(host: str, out: Path) -> None:
    now = datetime.datetime.now(datetime.UTC)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kill switch proof CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    out.mkdir(parents=True, exist_ok=True)
    (out / "ca.pem").write_bytes(ca.public_bytes(pem))
    (out / "cert.pem").write_bytes(leaf.public_bytes(pem))
    (out / "key.pem").write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def main(host: str, upstream: int, out: Path) -> None:
    make_certs(host, out)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(out / "cert.pem", out / "key.pem")

    async def handle(r, w):
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", upstream)
        except OSError:
            w.close()
            return
        await asyncio.gather(pipe(r, uw), pipe(ur, w))

    server = await asyncio.start_server(handle, "0.0.0.0", 443, ssl=ctx)
    print("tls proxy up", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])))
