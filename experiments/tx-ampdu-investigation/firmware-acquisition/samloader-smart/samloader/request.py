# SPDX-License-Identifier: GPL-3.0+
# Copyright (C) 2020 nlscc

""" Build FUS XML requests. """

import xml.etree.ElementTree as ET

def getlogiccheck(inp: str, nonce: str) -> str:
    """ Calculate the request checksum for a given input and nonce. """
    out = ""
    for c in nonce:
        idx = ord(c) & 0xf
        out += inp[idx] if idx < len(inp) else "."
    return out

def build_reqhdr(fusmsg: ET.Element):
    """ Build the FUSHdr of an XML message. """
    fushdr = ET.SubElement(fusmsg, "FUSHdr")
    ET.SubElement(fushdr, "ProtoVer").text = "1.0"
    ET.SubElement(fushdr, "SessionID").text = "0"
    ET.SubElement(fushdr, "MsgID").text = "1"

def build_put(fput: ET.Element, params: dict):
    """ Append a set of Tag/Data parameters to a Put or Get element. """
    for tag, value in params.items():
        setag = ET.SubElement(fput, tag)
        ET.SubElement(setag, "Data").text = str(value)

def binaryinform(fwv: str, model: str, region: str, nonce: str) -> str:
    """ Build a BinaryInform request. """
    fusmsg = ET.Element("FUSMsg")
    build_reqhdr(fusmsg)
    fusbody = ET.SubElement(fusmsg, "FUSBody")
    fput = ET.SubElement(fusbody, "Put")
    ET.SubElement(fput, "CmdID").text = "1"
    build_put(fput, {
        "ACCESS_MODE": 1,
        "BINARY_NATURE": 1,
        "REQUEST_TYPE": 2,
        "LOGIC_CHECK": getlogiccheck(fwv, nonce),
        "BINARY_SW_VERSION": fwv,
        "BINARY_LOCAL_CODE": region,
        "BINARY_MODEL_NAME": model,
    })
    fget = ET.SubElement(fusbody, "Get")
    ET.SubElement(fget, "CmdID").text = "2"
    ET.SubElement(fget, "BINARY_SW_VERSION")
    return ET.tostring(fusmsg)

def binaryinit(filename: str, fwv: str, model_type: str, region: str, nonce: str) -> str:
    """ Build a BinaryInit request. """
    fusmsg = ET.Element("FUSMsg")
    build_reqhdr(fusmsg)
    fusbody = ET.SubElement(fusmsg, "FUSBody")
    fput = ET.SubElement(fusbody, "Put")
    checkinp = filename[len(filename) - 25:len(filename) - 9]
    build_put(fput, {
        "BINARY_NAME": filename,
        "BINARY_SW_VERSION": fwv,
        "DEVICE_LOCAL_CODE": region,
        "DEVICE_MODEL_TYPE": model_type,
        "LOGIC_CHECK": getlogiccheck(checkinp, nonce),
    })
    return ET.tostring(fusmsg)
