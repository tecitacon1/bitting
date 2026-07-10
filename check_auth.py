#!/usr/bin/env python3
"""Verify Polymarket wallet auth and USDC balance (no orders placed)."""

import sys

from config import (
    POLYMARKET_CHAIN_ID,
    POLYMARKET_CLOB_HOST,
    POLYMARKET_FUNDER_ADDRESS,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_SIGNATURE_TYPE,
)


def main():
    if not POLYMARKET_PRIVATE_KEY:
        print("POLYMARKET_PRIVATE_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

    client = ClobClient(
        POLYMARKET_CLOB_HOST,
        key=POLYMARKET_PRIVATE_KEY,
        chain_id=POLYMARKET_CHAIN_ID,
        signature_type=POLYMARKET_SIGNATURE_TYPE,
        funder=POLYMARKET_FUNDER_ADDRESS or None,
    )

    creds = client.derive_api_key()
    client.set_api_creds(creds)
    print("API key derived:", creds.api_key[:8] + "...")

    balance = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    raw = balance.get("balance", "0")
    usdc = float(raw) / 1_000_000
    allowance = balance.get("allowance")
    print("Auth OK")
    print(f"Wallet funder: {POLYMARKET_FUNDER_ADDRESS or '(signer)'}")
    print(f"USDC balance:  ${usdc:.4f}")
    print(f"Allowance:     {allowance}")


if __name__ == "__main__":
    main()
