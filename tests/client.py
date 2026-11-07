"""Kleiner HTTP-Client für die Tests (folgt keinen Weiterleitungen, merkt sich Cookies und CSRF-Token)."""
import http.cookiejar
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid

B = os.environ.get("B", "http://127.0.0.1:8090")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect)
        self.t = ""

    def req(self, path, data=None, files=None, headers=None, raw=False):
        h = {"X-CSRF-Token": self.t}
        h.update(headers or {})
        body = None
        if files:
            bnd = uuid.uuid4().hex
            body = b"".join(
                ('--{}\r\nContent-Disposition: form-data; name="{}"; filename="{}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n').format(bnd, k, fn).encode() + c + b"\r\n"
                for k, (fn, c) in files.items()) + "--{}--\r\n".format(bnd).encode()
            h["Content-Type"] = "multipart/form-data; boundary=" + bnd
        elif data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode()
        try:
            r = self.op.open(urllib.request.Request(B + path, data=body, headers=h))
            st, b, hd = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            st, b, hd = e.code, e.read(), e.headers
        return st, (b if raw else b.decode("utf-8", "replace")), hd

    def tok(self, path):
        b = self.req(path)[1]
        self.t = re.search(r'csrf-token" content="([^"]+)', b).group(1)
        return b

    def login(self, user, password):
        self.tok("/login")
        st, _, _ = self.req("/login", {"username": user, "password": password, "next": "/", "csrf": self.t})
        self.tok("/account")
        return st


_n = [0]


def ok(cond, msg):
    _n[0] += 1
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def done():
    print("ALL", _n[0], "PASSED")


def loc_id(headers):
    return int(headers["Location"].split("/")[-1].split("#")[0].split("?")[0])
