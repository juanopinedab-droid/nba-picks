import time
import requests


def http_get(url: str, params: dict = None, timeout: int = 15, max_retries: int = 3) -> requests.Response:
    """
    GET con retry exponencial (1s → 2s → 4s).
    Reintenta solo errores transitorios: 5xx, Timeout, ConnectionError.
    No reintenta 4xx (clave inválida, not found, rate limit).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            last_exc = e
            if e.response.status_code < 500:
                raise  # 4xx → sin reintentos
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  ⚠️  HTTP {e.response.status_code} — reintento {attempt + 1}/{max_retries - 1} en {wait}s...")
                time.sleep(wait)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  ⚠️  Error de red — reintento {attempt + 1}/{max_retries - 1} en {wait}s...")
                time.sleep(wait)
    raise last_exc
