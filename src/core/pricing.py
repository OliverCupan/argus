"""
Model Pricing Registry — live fetch with local cache and bundled fallback.

Fetches current model prices from LiteLLM's open-source pricing database
(GitHub raw CDN, free, no auth). Caches to .argus/pricing_cache.json for
24 hours so subsequent startups are instant. Falls back to bundled defaults
when offline or if the fetch fails.

Usage:
    pricing = ModelPricing()
    source = await pricing.fetch_prices()   # 'live', 'cached', or 'fallback'
    price = pricing.get_price("claude-haiku-4-5-20251001")
    # → {"input": 0.80, "output": 4.00}  (per 1M tokens)
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_TTL_HOURS = 24
_FETCH_TIMEOUT = 8.0  # seconds

# Bundled defaults — used when offline and cache is missing/expired.
# Per 1M tokens (input / output) in USD.
_BUNDLED_DEFAULTS: dict[str, dict[str, float]] = {
    "claude-opus-4-20250514":           {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-20250514":         {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":        {"input": 0.80,  "output": 4.00},
    "claude-3-5-haiku-20241022":        {"input": 0.80,  "output": 4.00},
    "claude-3-5-sonnet-20241022":       {"input": 3.00,  "output": 15.00},
    "claude-3-opus-20240229":           {"input": 15.00, "output": 75.00},
    "claude-3-sonnet-20240229":         {"input": 3.00,  "output": 15.00},
    "claude-3-haiku-20240307":          {"input": 0.25,  "output": 1.25},
    # OpenAI (for future multi-provider support)
    "gpt-4o":                           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":                      {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":                      {"input": 10.00, "output": 30.00},
}

# Model family prefix → fallback prices when exact model not found.
# Matched in order — most specific first.
_FAMILY_FALLBACKS: list[tuple[str, dict[str, float]]] = [
    ("claude-opus",             {"input": 15.00, "output": 75.00}),
    ("claude-sonnet",           {"input": 3.00,  "output": 15.00}),
    ("claude-haiku",            {"input": 0.80,  "output": 4.00}),
    ("claude-3-5",              {"input": 3.00,  "output": 15.00}),
    ("claude-3",                {"input": 3.00,  "output": 15.00}),
    ("claude",                  {"input": 3.00,  "output": 15.00}),
    ("gpt-4o-mini",             {"input": 0.15,  "output": 0.60}),
    ("gpt-4",                   {"input": 10.00, "output": 30.00}),
    ("gpt-3.5",                 {"input": 0.50,  "output": 1.50}),
]

_DEFAULT_FALLBACK: dict[str, float] = {"input": 3.00, "output": 15.00}


class ModelPricing:
    """
    Live model pricing registry with caching and offline fallback.

    Call `await fetch_prices()` once at startup. All subsequent lookups
    via `get_price()` are synchronous dict lookups.
    """

    def __init__(self, cache_path: Optional[Path] = None, overrides_path: Optional[Path] = None):
        self._prices: dict[str, dict[str, float]] = dict(_BUNDLED_DEFAULTS)
        self._source: str = "bundled"
        self._model_count: int = len(_BUNDLED_DEFAULTS)
        self._cache_path: Path = cache_path or Path(".argus/pricing_cache.json")
        self._overrides_path: Optional[Path] = overrides_path
        self._cache_age_hours: Optional[float] = None  # set when cache is loaded

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def fetch_prices(self) -> str:
        """
        Initialise prices. Returns one of:
          'live'     — fetched fresh from LiteLLM GitHub
          'cached'   — loaded from local cache (still fresh)
          'fallback' — using bundled defaults (offline / fetch failed)
        """
        # Try cache first (fast path)
        cached = self._load_cache()
        if cached is not None:
            self._apply_prices(cached)
            self._source = "cached"
            logger.debug("Pricing loaded from cache (%d models)", self._model_count)
            self._apply_overrides()
            return "cached"
        # Reset cache age on fresh fetch
        self._cache_age_hours = None

        # Cache missing or stale — try live fetch
        live = await self._fetch_live()
        if live is not None:
            self._apply_prices(live)
            self._save_cache(live)
            self._source = "live"
            logger.debug("Pricing fetched live (%d models)", self._model_count)
            self._apply_overrides()
            return "live"

        # Offline — use bundled defaults
        self._prices = dict(_BUNDLED_DEFAULTS)
        self._model_count = len(_BUNDLED_DEFAULTS)
        self._source = "fallback"
        logger.warning("Pricing: using bundled fallback (offline or fetch failed)")
        self._apply_overrides()
        return "fallback"

    def get_price(self, model: str) -> dict[str, float]:
        """
        Return {input, output} price per 1M tokens for a model.
        Falls back by family prefix if exact model not found.
        """
        if model in self._prices:
            return self._prices[model]

        # Fuzzy match by family prefix
        model_lower = model.lower()
        for prefix, price in _FAMILY_FALLBACKS:
            if model_lower.startswith(prefix):
                logger.debug("Pricing: fuzzy match %s → %s family", model, prefix)
                return price

        logger.warning("Pricing: unknown model %r — using default Sonnet rate", model)
        return _DEFAULT_FALLBACK

    def list_models(self) -> list[str]:
        """Return all known model names (for validation in runtime model selection)."""
        return sorted(self._prices.keys())

    def status_line(self) -> str:
        """One-line description of the pricing source for display in the banner."""
        age_note = ""
        if self._source == "cached" and self._cache_age_hours is not None:
            age_note = f", {self._cache_age_hours:.0f}h old"
        return f"Pricing: {self._source} ({self._model_count} models{age_note})"

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _fetch_live(self) -> Optional[dict[str, dict[str, float]]]:
        """Fetch and parse LiteLLM pricing JSON. Returns None on any failure."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
                resp = await client.get(_LITELLM_URL)
                resp.raise_for_status()
                raw: dict = resp.json()
        except Exception as e:
            logger.warning("Pricing fetch failed: %s", e)
            return None

        return self._parse_litellm(raw)

    @staticmethod
    def _parse_litellm(raw: dict) -> dict[str, dict[str, float]]:
        """
        Convert LiteLLM's per-token pricing to our per-1M format.
        LiteLLM format: { "model-name": { "input_cost_per_token": 0.000003, ... } }
        """
        result: dict[str, dict[str, float]] = {}
        for model_name, data in raw.items():
            if not isinstance(data, dict):
                continue
            inp = data.get("input_cost_per_token")
            out = data.get("output_cost_per_token")
            if inp is None or out is None:
                continue
            try:
                result[model_name] = {
                    "input":  float(inp) * 1_000_000,
                    "output": float(out) * 1_000_000,
                }
            except (TypeError, ValueError):
                continue
        return result

    def _apply_prices(self, prices: dict[str, dict[str, float]]) -> None:
        """Replace internal price dict and update model count."""
        self._prices = {**_BUNDLED_DEFAULTS, **prices}  # bundled as safety floor
        self._model_count = len(self._prices)

    def _apply_overrides(self) -> None:
        """Apply user-supplied price overrides from pricing.yaml (if it exists)."""
        if self._overrides_path is None:
            # Check default location
            default = Path("pricing.yaml")
            if not default.exists():
                return
            self._overrides_path = default

        if not self._overrides_path.exists():
            return

        try:
            import yaml
            with open(self._overrides_path) as f:
                overrides: dict = yaml.safe_load(f) or {}
            for model, prices in overrides.items():
                if isinstance(prices, dict):
                    self._prices[model] = {
                        "input":  float(prices.get("input", 0)),
                        "output": float(prices.get("output", 0)),
                    }
            logger.debug("Pricing: applied overrides from %s", self._overrides_path)
        except Exception as e:
            logger.warning("Pricing: failed to load overrides: %s", e)

    def _load_cache(self) -> Optional[dict[str, dict[str, float]]]:
        """Load cache if it exists and is fresher than TTL. Returns None otherwise.
        Side-effect: sets self._cache_age_hours when cache is valid."""
        if not self._cache_path.exists():
            return None
        try:
            raw = json.loads(self._cache_path.read_text())
            fetched_at = datetime.fromisoformat(raw["fetched_at"])
            now = datetime.now(timezone.utc)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = now - fetched_at
            age_hours = age.total_seconds() / 3600
            if age > timedelta(hours=_CACHE_TTL_HOURS):
                logger.debug("Pricing cache expired (%.1fh old)", age_hours)
                return None
            self._cache_age_hours = age_hours   # cache the age for status_line()
            return raw["prices"]
        except Exception as e:
            logger.debug("Pricing cache unreadable: %s", e)
            return None

    def _save_cache(self, prices: dict[str, dict[str, float]]) -> None:
        """Save fetched prices to local cache file."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "prices": prices,
            }
            self._cache_path.write_text(json.dumps(payload, indent=2))
            logger.debug("Pricing cache saved to %s", self._cache_path)
        except Exception as e:
            logger.warning("Pricing: failed to save cache: %s", e)
