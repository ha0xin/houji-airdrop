# SPDX-License-Identifier: GPL-3.0+
# Copyright (C) 2020 nlscc

""" FUS request helper (automatically sign requests and update tokens) """

import requests

from . import auth

class FUSClient:
    """ FUS API client. """
    def __init__(self):
        self.auth = ""
        self.nonce = ""
        self.sess = requests.Session()
        self.makereq("NF_SmartDownloadGenerateNonce.do") # initialize nonce
    def makereq(self, path: str, data: str = "") -> str:
        """ Make a FUS request to a given endpoint. """
        authv = 'FUS nonce="' + self.nonce + '", signature="' + self.auth \
            + '", nc="", type="", realm="", newauth="1"'
        headers = {"Authorization": authv, "User-Agent": "SMART 2.0", "Accept": "application/xml"}
        if data:
            headers["Content-Type"] = "application/xml"
        req = self.sess.post("https://neofussvr.sslcs.cdngc.net/" + path, data=data, headers=headers, timeout=30)
        # The nonce is now a plaintext token; sign it for the next request.
        if req.headers.get("NONCE"):
            self.nonce = req.headers["NONCE"]
            self.auth = auth.getauth(self.nonce)
        req.raise_for_status()
        return req.text
    def downloadfile(self, filename: str, start: int = 0) -> requests.Response:
        """ Make a FUS cloud request to download a given file. """
        authv = 'FUS nonce="' + self.nonce + '", signature="' + self.auth \
            + '", nc="", type="", realm="", newauth="1"'
        headers = {"Authorization": authv, "User-Agent": "SMART 2.0"}
        if start > 0:
            headers["Range"] = "bytes={}-".format(start)
        # The slashes in the file path must not be percent-encoded.
        req = self.sess.get("http://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do"
                            "?file=" + filename, headers=headers, stream=True)
        req.raise_for_status()
        return req
