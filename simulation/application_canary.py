"""Synthetic cart/checkout transactions against the local mock Boutique only."""

import time
from datetime import datetime, timezone

import httpx

PRODUCT = "0PUK6V6EV0"
BASE_URL = "http://127.0.0.1:8080"
MAX_RESPONSE_BYTES = 1024 * 1024
ALLOWED_PATHS = {"/", "/cart", "/cart/checkout", "/cart/empty"}


class ProbeBudgetExceededError(ValueError):
    pass


class ResponseTooLargeError(ValueError):
    pass


class UnsafeRedirectError(ValueError):
    pass


def check_application(*, transport=None, deadline_seconds: float = 25) -> dict:
    if not 1 <= deadline_seconds <= 30:
        raise ValueError("Canary deadline must be between 1 and 30 seconds")
    steps = []
    deadline = time.perf_counter() + deadline_seconds
    with httpx.Client(base_url=BASE_URL, timeout=10, follow_redirects=False,
                      trust_env=False, transport=transport) as client:
        def request(name, method, path, data=None, marker=None, *, end=deadline):
            started = time.perf_counter()
            try:
                url = httpx.URL(BASE_URL).join(path)
                for redirect in range(4):
                    if (url.scheme != "http" or url.host != "127.0.0.1" or url.port != 8080
                            or url.username or url.password or url.path not in ALLOWED_PATHS or url.query):
                        raise UnsafeRedirectError()
                    remaining = end - time.perf_counter()
                    if remaining <= 0:
                        raise ProbeBudgetExceededError()
                    with client.stream(method, url, data=data, timeout=min(10, remaining)) as response:
                        code = response.status_code
                        if code in {301, 302, 303, 307, 308}:
                            if redirect == 3 or not response.headers.get("location"):
                                raise UnsafeRedirectError()
                            url = url.join(response.headers["location"])
                            if code in {301, 302, 303}:
                                method, data = "GET", None
                            continue
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ResponseTooLargeError()
                            if time.perf_counter() >= end:
                                raise ProbeBudgetExceededError()
                            chunks.append(chunk)
                        body = b"".join(chunks).decode(errors="replace")
                        break
                if time.perf_counter() >= end:
                    raise ProbeBudgetExceededError()
                found = None if marker is None else marker in body
                result = {"step": name, "status_code": code,
                          "success": code == 200 and (marker is None or found), "marker_found": found,
                          "latency_s": time.perf_counter() - started}
            except (httpx.HTTPError, ProbeBudgetExceededError, ResponseTooLargeError, UnsafeRedirectError) as exc:
                kind = ("TimeoutException" if isinstance(exc, httpx.TimeoutException)
                        else "HTTPError" if isinstance(exc, httpx.HTTPError) else type(exc).__name__.removesuffix("Error"))
                result = {"step": name, "success": False, "status_code": None,
                          "error": kind, "latency_s": time.perf_counter() - started}
            steps.append(result)
            return result["success"]

        try:
            ready = (request("home", "GET", "/")
                     and request("add_cart", "POST", "/cart", {"product_id": PRODUCT, "quantity": "1"}, marker=PRODUCT)
                     and request("read_cart", "GET", "/cart", marker="Cart (1)"))
            if ready:
                request("checkout", "POST", "/cart/checkout", {
                    "email": "rca-canary@example.invalid", "street_address": "123 Test Street",
                    "zip_code": "94043", "city": "Test City", "state": "CA", "country": "United States",
                    "credit_card_number": "4111111111111111", "credit_card_expiration_month": "12",
                    "credit_card_expiration_year": str(datetime.now(timezone.utc).year + 2), "credit_card_cvv": "123",
                }, marker="Your order is complete!")
        finally:
            # This client owns its cookie/session; do not touch another user's cart.
            if client.cookies:
                request("cleanup_cart", "POST", "/cart/empty", end=time.perf_counter() + 3)
    names = [step["step"] for step in steps]
    complete = names == ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]
    return {"success": complete and all(step["success"] for step in steps), "steps": steps}
