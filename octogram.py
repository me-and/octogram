#!/usr/bin/env python3
"""
octogram.py — Octopus Agile price checker & Telegram notifier.

Fetches upcoming half-hour electricity unit rates for your Agile tariff,
then sends a Telegram message listing any slots where the price is at or
below a configured threshold (default: 0p/kWh, i.e. free or negative).

Usage:
    python3 octogram.py [--dry-run] [--config /path/to/octogram.conf]

Config is read from (first match wins):
    1. Path given via --config (fails if the file does not exist)
    2. $XDG_CONFIG_HOME/octogram.conf or $XDG_CONFIG_HOME/octogram/octogram.conf
       (XDG_CONFIG_HOME defaults to ~/.config)
    3. For each dir in $XDG_CONFIG_DIRS: $dir/octogram.conf or $dir/octogram/octogram.conf
"""

import argparse
import configparser
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OCTOPUS_API_BASE = "https://api.octopus.energy/v1"
TELEGRAM_API_BASE = "https://api.telegram.org"

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def find_config(explicit: str | None) -> Path:
    import os

    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {explicit}")
        return p

    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", "") or (Path.home() / ".config"))
    xdg_config_dirs_raw = os.environ.get("XDG_CONFIG_DIRS", "")
    xdg_config_dirs = [Path(d) for d in xdg_config_dirs_raw.split(":") if d] if xdg_config_dirs_raw else []

    def _candidates_for(base: Path):
        yield base / "octogram.conf"
        yield base / "octogram" / "octogram.conf"

    for p in _candidates_for(xdg_config_home):
        if p.is_file():
            return p

    for d in xdg_config_dirs:
        for p in _candidates_for(d):
            if p.is_file():
                return p

    raise FileNotFoundError(
        "Config file not found. Place octogram.conf in $XDG_CONFIG_HOME (default: ~/.config) "
        "or one of the $XDG_CONFIG_DIRS directories, or specify a path with --config."
    )


def load_config(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    required = [
        ("octopus", "api_key"),
        ("octopus", "account_number"),
        ("telegram", "bot_token"),
        ("telegram", "chat_id"),
    ]
    for section, key in required:
        if not cfg.has_option(section, key) or not cfg.get(section, key).strip():
            raise ValueError(f"Missing required config: [{section}] {key}")
    return cfg

# ---------------------------------------------------------------------------
# Octopus API helpers
# ---------------------------------------------------------------------------

def octopus_get(path: str, api_key: str, params: dict | None = None) -> dict:
    url = f"{OCTOPUS_API_BASE}{path}"
    resp = requests.get(url, auth=(api_key, ""), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_active_tariff_code(api_key: str, account_number: str) -> str:
    """
    Walk the account's electricity meter agreements to find the currently
    active tariff code (e.g. 'E-1R-AGILE-24-10-01-C').
    """
    data = octopus_get(f"/accounts/{account_number}/", api_key)
    now = datetime.now(timezone.utc)

    for prop in data.get("properties", []):
        for emp in prop.get("electricity_meter_points", []):
            for agreement in emp.get("agreements", []):
                valid_from = _parse_dt(agreement.get("valid_from"))
                valid_to = _parse_dt(agreement.get("valid_to"))
                # Active if: valid_from <= now AND (valid_to is None OR valid_to > now)
                if valid_from and valid_from <= now:
                    if valid_to is None or valid_to > now:
                        tariff_code = agreement.get("tariff_code", "")
                        if tariff_code:
                            log.info("Active tariff code: %s", tariff_code)
                            return tariff_code

    raise RuntimeError(
        "Could not find an active electricity tariff in your Octopus account. "
        "Verify your account number and that you have an active Agile agreement."
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Octopus returns ISO 8601 strings; handle both Z and +00:00
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def tariff_code_to_product_code(tariff_code: str) -> str:
    """
    Extract product code from tariff code.
    Tariff codes look like:  E-1R-AGILE-24-10-01-C
    Product codes look like: AGILE-24-10-01
    The product code is everything between the 3rd and last dash-separated segment.
    """
    # Format: {fuel}-{payment_type}-{product_code}-{region_char}
    # Strip leading fuel+payment prefix (e.g. "E-1R-") and trailing region char (e.g. "-C")
    parts = tariff_code.split("-")
    # parts[0] = fuel (E/G), parts[1] = payment type (1R/2R), parts[-1] = region
    product_parts = parts[2:-1]
    return "-".join(product_parts)


def fetch_unit_rates(
    api_key: str,
    product_code: str,
    tariff_code: str,
    period_from: datetime,
) -> list[dict]:
    """Fetch all published future unit rate slots from period_from onwards, handling pagination."""
    path = f"/products/{product_code}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params = {
        "period_from": period_from.isoformat(),
        "page_size": 100,
    }
    results = []
    while True:
        data = octopus_get(path, api_key, params)
        results.extend(data.get("results", []))
        next_url = data.get("next")
        if not next_url:
            break
        # Extract page param from next URL for subsequent calls
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(next_url).query)
        params = {**params, "page": qs["page"][0]}

    # Sort chronologically
    results.sort(key=lambda r: r.get("valid_from", ""))
    return results

# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()

# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_slot_time(valid_from: str, valid_to: str) -> str:
    dt_from = _parse_dt(valid_from).astimezone()
    dt_to = _parse_dt(valid_to).astimezone()
    day = dt_from.strftime("%a %-d %b")
    t_from = dt_from.strftime("%H:%M")
    t_to = dt_to.strftime("%H:%M")
    return f"{day} {t_from}–{t_to}"


def build_message(slots: list[dict]) -> str:
    lines = ["⚡ <b>Octopus Agile: Free/Negative slots found!</b>", ""]
    for slot in slots:
        price = slot["value_inc_vat"]
        time_str = format_slot_time(slot["valid_from"], slot["valid_to"])
        sign = "−" if price < 0 else " "
        lines.append(f"• {time_str}  {sign}{abs(price):.2f}p/kWh")
    lines.append("")
    total_minutes = len(slots) * 30
    lines.append(f"{len(slots)} slot(s) | {total_minutes} minutes total")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print message to stdout instead of sending to Telegram")
    parser.add_argument("--config", metavar="PATH", help="Path to config file")
    args = parser.parse_args()

    try:
        config_path = find_config(args.config)
        log.info("Using config: %s", config_path)
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1

    api_key = cfg.get("octopus", "api_key")
    account_number = cfg.get("octopus", "account_number")
    bot_token = cfg.get("telegram", "bot_token")
    chat_id = cfg.get("telegram", "chat_id")
    threshold = cfg.getfloat("settings", "price_threshold_p", fallback=0.0)

    try:
        tariff_code = get_active_tariff_code(api_key, account_number)
    except Exception as exc:
        log.error("Failed to fetch tariff from Octopus API: %s", exc)
        return 1

    if "AGILE" not in tariff_code.upper():
        log.warning("Active tariff '%s' does not appear to be an Agile tariff.", tariff_code)

    product_code = tariff_code_to_product_code(tariff_code)
    log.info("Product code: %s", product_code)

    now = datetime.now(timezone.utc)

    try:
        rates = fetch_unit_rates(api_key, product_code, tariff_code, now)
    except Exception as exc:
        log.error("Failed to fetch unit rates: %s", exc)
        return 1

    log.info("Fetched %d rate slot(s)", len(rates))

    qualifying = [r for r in rates if r.get("value_inc_vat", 999) <= threshold]
    log.info("%d slot(s) at or below %.2fp/kWh", len(qualifying), threshold)

    if not qualifying:
        log.info("No qualifying slots — suppressing notification.")
        return 0

    message = build_message(qualifying)

    if args.dry_run:
        print(message)
        return 0

    try:
        send_telegram(bot_token, chat_id, message)
        log.info("Telegram message sent successfully.")
    except Exception as exc:
        log.error("Failed to send Telegram message: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
