"""Synthetic cart/checkout transactions against the local mock Boutique only."""

from datetime import datetime, timezone
import time

import httpx

PRODUCT = "0PUK6V6EV0"
BASE_URL = "http://127.0.0.1:8080"


def check_application() -> dict:
    steps = []
    with httpx.Client(base_url=BASE_URL, timeout=10, follow_redirects=True, trust_env=False) as client:
        def request(name, method, path, data=None, marker=None):
            started = time.perf_counter()
            try:
                response = client.request(method, path, data=data)
                success = response.status_code == 200 and (marker is None or marker in response.text)
                result = {"step": name, "status_code": response.status_code,
                          "success": success, "marker_found": None if marker is None else marker in response.text,
                          "latency_s": time.perf_counter() - started}
            except httpx.HTTPError as exc:
                result = {"step": name, "success": False, "status_code": None,
                          "error": type(exc).__name__, "latency_s": time.perf_counter() - started}
            steps.append(result)
            return result["success"]

        try:
            if not request("home", "GET", "/"):
                return {"success": False, "steps": steps}
            if not request("add_cart", "POST", "/cart", {"product_id": PRODUCT, "quantity": "1"}, marker=PRODUCT):
                return {"success": False, "steps": steps}
            if not request("read_cart", "GET", "/cart", marker="Cart (1)"):
                return {"success": False, "steps": steps}
            request("checkout", "POST", "/cart/checkout", {
                "email": "rca-canary@example.invalid", "street_address": "123 Test Street",
                "zip_code": "94043", "city": "Test City", "state": "CA", "country": "United States",
                "credit_card_number": "4111111111111111", "credit_card_expiration_month": "12",
                "credit_card_expiration_year": str(datetime.now(timezone.utc).year + 2), "credit_card_cvv": "123",
            }, marker="Your order is complete!")
            return {"success": all(step["success"] for step in steps), "steps": steps}
        finally:
            # This client owns its cookie/session; do not touch another user's cart.
            if client.cookies:
                request("cleanup_cart", "POST", "/cart/empty")
