"""HTTP notification client.

`init()` wires this up so `notify(...)` writes to the configured endpoint.
v1 supports `http` only; `sock` is a stub raising NotImplementedError (see
the plan's open question on sock framing).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional


def _post_notification(
    notification: dict,
    data: bytes,
    content_type: Optional[str] = None,
) -> None:
    """Send `data` to the configured endpoint.

    Args:
      notification: dict with "type" ("http" only in v1) and "endpoint"
                    (full URL for http).
      data: payload bytes (already serialized by notify()).
      content_type: explicit Content-Type for http; defaults to
                    application/octet-stream when not provided.

    Raises:
      NotImplementedError: notification["type"] == "sock" (deferred to v2).
      ValueError:          unknown notification type.
      urllib.error.URLError / OSError: on network failure.
    """
    ntype = notification.get("type")
    if ntype == "http":
        headers = {"Content-Type": content_type or "application/octet-stream"}
        req = urllib.request.Request(
            notification["endpoint"],
            data=data,
            headers=headers,
            method="POST",
        )
        # nosec — endpoint is set by the caller, not from untrusted input.
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            resp.read()
        return

    if ntype == "sock":
        raise NotImplementedError(
            "sock transport deferred to v2 — see open questions in the plan"
        )

    raise ValueError(f"unsupported notification type: {ntype!r}")
