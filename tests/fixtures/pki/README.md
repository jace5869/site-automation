# Test certificates (no keys)

Four certificates for the inventory tests (tests/windows, tests/certs): what each report must read
from them. Made with openssl; the private keys were not kept.

| File | Is | Key | Signed by | Names |
|---|---|---|---|---|
| `root.pem` | root CA (CA:TRUE, self-signed) | RSA 3072 | itself | - |
| `int.pem` | intermediate CA (CA:TRUE) | EC P-256 | Test Root CA | - |
| `web.pem` | server certificate | RSA 2048 | Test Issuing CA | DNS:web01.example.mil, DNS:web.example.mil, IP:192.0.2.10 |
| `self.pem` | self-signed server certificate | EC P-384 | itself | DNS:self01.example.mil |
