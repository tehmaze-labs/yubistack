"""
ykval.utils
~~~~~~~~~~~

Helper utilities for Yubikey Validator (YK-VAL)
"""

import base64
from binascii import hexlify, unhexlify
from cgi import parse_qs
from Crypto.Cipher import AES
from datetime import datetime
import hashlib
import hmac
import logging
from random import getrandbits
import re
import sys

import requests

logger = logging.getLogger(__name__)

if sys.version_info < (3,):
    import string
    MODHEX_MAP = string.maketrans('cbdefghijklnrtuv', '0123456789abcdef')
else:
    MODHEX_MAP = str.maketrans('cbdefghijklnrtuv', '0123456789abcdef')

def parse_querystring(query_string):
    """
    Parse request's query string
    >>> r = parse_querystring('/foo?bar=1&moo=abc&other=-123')
    >>> r == {'/foo?bar': 1, 'moo': 'abc', 'other': -123}
    True
    """
    params = {}
    for key, val in parse_qs(query_string).items():
        if ((val[0].isdigit() and not val[0].startswith('0')) or
            (val[0].startswith('-') and val[0][1:].isdigit())):
            params[key] = int(val[0])
        else:
            params[key] = val[0]
    return params

def modhex2hex(modhex):
    """
    Turn modhex into regular hex
    >>> modhex2hex('aabbccddeeffnrfrjeelfvehntlnlvgejvcrtdvivfbh')
    'aa1100223344bc4c833a4f36bdabaf538f0cd2f7f416'
    """
    # Python2 fails to translate unicode
    return str(modhex).translate(MODHEX_MAP)

def aes128ecb_decrypt(key, cipher):
    """
    Decrypt ciphertext with key using AES128CBC encryption
    >>> key = 'abcdef0123456789abcdef0123456789'
    >>> cipher = '16e1e5d9d3991004452007e302000000'
    >>> plain = aes128ecb_decrypt(key, cipher).decode()
    >>> plain == '46b029d5340bbd23b39c6c9154d095b1'
    True
    """
    key = unhexlify(key)
    iv = unhexlify('00000000000000000000000000000000')
    cipher = modhex2hex(cipher)
    cipher = unhexlify(cipher)
    decryptor = AES.new(key, AES.MODE_ECB, iv)
    plain = decryptor.decrypt(cipher)
    plain = hexlify(plain)
    return plain

def calculate_crc(token):
    """
    Calculate CRC-16
    >>> calculate_crc('16e1e5d9d3991004452007e302000000')
    22744
    """
    crc = 0xffff
    token = unhexlify(token)
    for i in range(16):
        if isinstance(token[i], str):
            # Python 2.X
            crc = crc ^ ord(token[i])
        else:
            crc = crc ^ token[i]
        for _ in range(8):
            last_bit_true = crc & 0x1
            crc = crc >> 1
            if last_bit_true:
                crc = crc ^ 0x8408
    return crc

def check_crc(token):
    """ Check the value of the CRC function """
    return calculate_crc(token) == 0xf0b8

def sign(data, apikey):
    """
    Sign a http query string in the array of key-value pairs
    return b64 encoded hmac hash

    https://github.com/Yubico/yubikey-val/blob/master/doc/Validation_Protocol_V2.0.adoc#generating-signatures
    """
    # Alphabetically sort the set of key/value pairs by key order.
    keys = sorted(data.keys())
    # Construct a single line with each ordered key/value pair concatenated using &,
    # and each key and value contatenated with =. Do not add any linebreaks.
    # Do not add whitespace. For example: a=2&b=1&c=3.
    query_string = '&'.join(['%s=%s' % (k, data[k]) for k in keys])
    # Apply the HMAC-SHA-1 algorithm on the line as an octet string using the API key as key
    signature = hmac.new(apikey, query_string.encode(), hashlib.sha1).digest()
    # Base 64 encode the resulting value according to RFC 4648
    signature = base64.b64encode(signature).decode()
    logger.debug('SIGN: %s H=%s', query_string, signature)
    return signature

def decrypt_otp(otp, urls=None, decryptor=None):
    """
    Call KSM url to decrypt OTP
    Response is processed further
    """
    if decryptor:
        data = decryptor.decrypt(otp)
        return dict([(k, int(v, 16)) for k, v in data.items()])
    elif urls:
        # TODO: Support for async req for multiple servers
        for url in urls:
            req = requests.get(url, params={'otp': otp})
            logger.debug('YK-KSM response: %s (status_code: %s)', req.text, req.status_code)
            if req.text.startswith('OK'):
                data = req.text.split()[1:]
                resp = {}
                for i in data:
                    key, val = i.split('=')
                    resp[key] = int(val, 16)
                return resp
    else:
        logger.error("No KSM service provided. Can't decrypt OTP.")
    return False

def generate_nonce():
    """
    Generate a random nonce
    """
    return hex(getrandbits(128))[2:-1]

def counters_eq(params1, params2):
    """
    Function to compare two set of parameters
    >>> p1 = {'yk_counter': 123, 'yk_use': 200}
    >>> p2 = {'yk_counter': 123, 'yk_use': 200}
    >>> counters_eq(p1, p2)
    True
    >>> p2 = {'yk_counter': 1, 'yk_use': 200}
    >>> counters_eq(p1, p2)
    False
    """
    return params1['yk_counter'] == params2['yk_counter'] \
        and params1['yk_use'] == params2['yk_use']

def counters_gt(params1, params2):
    """
    Function to compare two set of parameters
    >>> p1 = {'yk_counter': 123, 'yk_use': 200}
    >>> p2 = {'yk_counter': 123, 'yk_use': 200}
    >>> counters_gt(p1, p2)
    False
    >>> p2 = {'yk_counter': 80, 'yk_use': 200}
    >>> counters_gt(p1, p2)
    True
    >>> p2 = {'yk_counter': 80, 'yk_use': 201}
    >>> counters_gt(p1, p2)
    True
    """
    return params1['yk_counter'] > params2['yk_counter'] \
        or (params1['yk_counter'] == params2['yk_counter'] \
        and params1['yk_use'] > params2['yk_use'])

def counters_gte(params1, params2):
    """
    Function to compare two set of parameters
    >>> p1 = {'yk_counter': 123, 'yk_use': 200}
    >>> p2 = {'yk_counter': 123, 'yk_use': 200}
    >>> counters_gte(p1, p2)
    True
    >>> p2 = {'yk_counter': 80, 'yk_use': 200}
    >>> counters_gte(p1, p2)
    True
    >>> p1 = {'yk_counter': 80, 'yk_use': 201}
    >>> counters_gte(p1, p2)
    True
    """
    return params1['yk_counter'] > params2['yk_counter'] \
        or (params1['yk_counter'] == params2['yk_counter'] \
        and params1['yk_use'] >= params2['yk_use'])

def parse_sync_response(sync_response):
    """ Parsing query string parameters into a dict """
    params = [line.split('=', 1) for line in sync_response.split('\r\n') if '=' in line]
    params = dict(params)
    checks = {
        'modified': r'(-1|\d+)',
        'yk_publicname': r'[cbdefghijklnrtuv]+',
        'yk_counter': r'(-1|\d+)',
        'yk_use': r'(-1|[0-9]+)',
        'yk_high': r'(-1|[0-9]+)',
        'yk_low': r'(-1|[0-9]+)',
        'nonce': r'[a-zA-Z0-9]+',
    }
    for name, regex in checks.items():
        if name not in params or not re.match(regex, params[name]):
            logger.error('Cannot parse "%s". Response from sync server:\n%s', name, sync_response)
        else:
            # Convert numbers to int
            params[name] = int(params[name]) if params[name].isdigit() else params[name]
    return params

def wsgi_response(resp, start_response, apikey=b'', extra=None, status=200):
    """ Function to return a proper WSGI response """
    # yubikey-val's getUTCTimeStamp() function...
    now = datetime.utcnow().isoformat().replace('.', 'Z')[:-2]
    resp_data = {'status': resp, 't': now}
    for key, val in extra.items():
        resp_data[key] = val
    signature = sign(resp_data, apikey)

    # Bulding response string
    response = 'h=%s\r\n' % signature
    response += 't=%s\r\n' % now
    for key, val in extra.items():
        response += '%s=%s\r\n' % (key, val)
    response += 'status=%s\r\n' % resp
    response += '\r\n'
    logger.debug('Response: %s', response)
    start_response('%d OK' % status, [('Content-Type', 'text/plain')])
    return [response.encode()]

def current_ts():
    """
    Return current timestamp with second precision
    >>> now = current_ts()
    >>> isinstance(now, int)
    True
    >>> now > 1428791250
    True
    """
    return int(datetime.utcnow().strftime('%s'))

if __name__ == '__main__':
    import doctest
    doctest.testmod()
