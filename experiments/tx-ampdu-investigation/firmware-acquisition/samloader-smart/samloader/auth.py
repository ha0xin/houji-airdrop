# SPDX-License-Identifier: GPL-3.0+
# Copyright (C) 2020 nlscc

""" FUS authentication functions (signing the server nonce) """

import binascii
from Cryptodome.Cipher import AES

# Auth key for the Smart FUS protocol, provided by @henr1kas and @ducthoe.
AUTH_KEY = bytes([0x42, 0x2e, 0x73, 0x73, 0x36, 0x17, 0xae, 0x2b,
                  0x19, 0x89, 0x40, 0xfd, 0x4e, 0x32, 0xb0, 0xa5])

def getauth(nonce: str) -> str:
    """ Calculate the signature token from a given (plaintext) nonce. """
    block = bytearray(b"0" * 16)
    raw = nonce.encode()
    block[:min(len(raw), 16)] = raw[:16]
    enc = AES.new(AUTH_KEY, AES.MODE_ECB).encrypt(bytes(block))
    return binascii.hexlify(enc).decode()
