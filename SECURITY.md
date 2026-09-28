# Security policy

Nishro Link carries keystrokes and controls computers, so security reports are
taken seriously and answered first.

## Reporting a vulnerability

**Please do not open a public issue.** Use GitHub's private reporting:
[Report a vulnerability](https://github.com/nishro888/nishro-link/security/advisories/new).

Include what you found, how to reproduce it, and the versions involved
(*Help → Copy details* in the app gives them without anything private). You
will get an answer within a few days, and credit in the release notes if you
want it.

## Supported versions

Nishro Link is in beta. Security fixes go into the latest release only; please
update before reporting.

## What protects the link

In short (the full design is in [docs/security.md](docs/security.md)):

- **Pairing:** devices prove to each other that they know the group's password
  without sending it. What is proved is a slow key (PBKDF2-SHA256, 524,288
  iterations, salted with the hub's device ID), so a recorded handshake cannot
  be cheaply guessed against.
- **Encryption:** every connection runs an ephemeral X25519 exchange bound into
  those proofs; session keys come from HKDF-SHA256 over the exchange and the
  password key; every frame is sealed with ChaCha20-Poly1305. From the
  `cryptography` library. Forward secrecy: keys die with the connection.
- **Scope:** the link is meant for a local network. The control API listens on
  127.0.0.1 only and needs a token.

The design has not been independently audited. Reviews are very welcome.
