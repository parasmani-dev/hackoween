"""Single-attempt HTTP transport with redirects disabled."""

import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, timeout, *, payload=None, headers=None):
    req = Request(url, headers=headers or {})
    if payload is not None:
        req.data = json.dumps(payload, allow_nan=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            return response.headers.get_content_type(), response.read()
    except HTTPError as error:
        error.close()
        raise
