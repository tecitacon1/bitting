"""Polymarket CLOB execution helpers for the ladder strategy."""

import logging
from dataclasses import dataclass

from config import (
    LADDER_MAX_BUY_PRICE,
    LADDER_MIN_ORDER_USDC,
    LADDER_ORDER_USDC,
    LIVE_TRADING_ENABLED,
    POLYMARKET_API_KEY,
    POLYMARKET_API_PASSPHRASE,
    POLYMARKET_API_SECRET,
    POLYMARKET_CHAIN_ID,
    POLYMARKET_CLOB_HOST,
    POLYMARKET_FUNDER_ADDRESS,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_SIGNATURE_TYPE,
)

LOGGER = logging.getLogger("execution_engine")


@dataclass
class OrderBookView:
    best_ask: float | None
    best_bid: float | None
    tick_size: str
    neg_risk: bool


def parse_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_order_book(raw_book):
    asks = raw_book.get("asks") or []
    bids = raw_book.get("bids") or []
    best_ask = parse_float(asks[0]["price"]) if asks else None
    best_bid = parse_float(bids[0]["price"]) if bids else None
    return OrderBookView(
        best_ask=best_ask,
        best_bid=best_bid,
        tick_size=str(raw_book.get("tick_size") or "0.01"),
        neg_risk=bool(raw_book.get("neg_risk")),
    )


class PolymarketExecutor:
    def __init__(self):
        self.enabled = LIVE_TRADING_ENABLED
        self.client = None
        self.read_client = None
        self.OrderArgs = None
        self.MarketOrderArgs = None
        self.OrderType = None
        self.PartialCreateOrderOptions = None
        self.ApiCreds = None

        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import (
            ApiCreds,
            MarketOrderArgs,
            OrderArgs,
            OrderType,
            PartialCreateOrderOptions,
        )

        self.read_client = ClobClient(POLYMARKET_CLOB_HOST, chain_id=POLYMARKET_CHAIN_ID)
        self.OrderArgs = OrderArgs
        self.MarketOrderArgs = MarketOrderArgs
        self.OrderType = OrderType
        self.PartialCreateOrderOptions = PartialCreateOrderOptions
        self.ApiCreds = ApiCreds

        if not self.enabled:
            LOGGER.warning("Live trading disabled — orders will be logged only.")
            return

        if not POLYMARKET_PRIVATE_KEY:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY is required when LIVE_TRADING_ENABLED=true")

        creds = None
        if POLYMARKET_API_KEY and POLYMARKET_API_SECRET and POLYMARKET_API_PASSPHRASE:
            creds = ApiCreds(
                api_key=POLYMARKET_API_KEY,
                api_secret=POLYMARKET_API_SECRET,
                api_passphrase=POLYMARKET_API_PASSPHRASE,
            )

        self.client = ClobClient(
            POLYMARKET_CLOB_HOST,
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=POLYMARKET_CHAIN_ID,
            signature_type=POLYMARKET_SIGNATURE_TYPE,
            funder=POLYMARKET_FUNDER_ADDRESS or None,
            creds=creds,
        )
        if creds is None:
            derived = self.client.derive_api_key()
            self.client.set_api_creds(derived)

    def get_balance(self):
        if not self.client:
            return 0.0
        try:
            payload = self.client.get_balance_allowance()
            return parse_float(payload.get("balance"), 0.0) / 1_000_000
        except Exception:
            LOGGER.exception("Failed to fetch CLOB balance")
            return 0.0

    def get_order_book(self, token_id):
        clob = self.client or self.read_client
        if not clob:
            return OrderBookView(None, None, "0.01", False)
        raw = clob.get_order_book(token_id)
        if hasattr(raw, "__dict__"):
            raw = {
                "asks": [{"price": level.price, "size": level.size} for level in (raw.asks or [])],
                "bids": [{"price": level.price, "size": level.size} for level in (raw.bids or [])],
                "tick_size": getattr(raw, "tick_size", "0.01"),
                "neg_risk": getattr(raw, "neg_risk", False),
            }
        return summarize_order_book(raw)

    def market_buy_yes(self, token_id, usdc_amount, max_price=LADDER_MAX_BUY_PRICE):
        book = self.get_order_book(token_id)
        if book.best_ask is None:
            return {"ok": False, "reason": "no_ask_liquidity"}

        if book.best_ask > max_price:
            return {
                "ok": False,
                "reason": "price_too_high",
                "best_ask": book.best_ask,
                "max_price": max_price,
            }

        if usdc_amount < LADDER_MIN_ORDER_USDC:
            return {"ok": False, "reason": "below_min_order"}

        if not self.enabled:
            LOGGER.info(
                "PAPER BUY YES | token=%s | usdc=%.2f | ask=%.3f",
                token_id,
                usdc_amount,
                book.best_ask,
            )
            est_shares = usdc_amount / book.best_ask
            return {
                "ok": True,
                "paper": True,
                "side": "BUY",
                "token_id": token_id,
                "usdc_amount": usdc_amount,
                "price": book.best_ask,
                "shares": round(est_shares, 4),
            }

        order = self.MarketOrderArgs(
            token_id=token_id,
            amount=usdc_amount,
            side="BUY",
            price=book.best_ask,
            order_type=self.OrderType.FOK,
        )
        signed = self.client.create_market_order(order)
        response = self.client.post_order(signed, self.OrderType.FOK)
        shares = usdc_amount / book.best_ask
        return {
            "ok": True,
            "paper": False,
            "side": "BUY",
            "token_id": token_id,
            "usdc_amount": usdc_amount,
            "price": book.best_ask,
            "shares": round(shares, 4),
            "response": response,
        }

    def market_sell_yes(self, token_id, shares):
        book = self.get_order_book(token_id)
        if book.best_bid is None:
            return {"ok": False, "reason": "no_bid_liquidity"}
        if shares <= 0:
            return {"ok": False, "reason": "no_shares"}

        if not self.enabled:
            LOGGER.info(
                "PAPER SELL YES | token=%s | shares=%.4f | bid=%.3f",
                token_id,
                shares,
                book.best_bid,
            )
            return {
                "ok": True,
                "paper": True,
                "side": "SELL",
                "token_id": token_id,
                "shares": shares,
                "price": book.best_bid,
                "usdc_amount": round(shares * book.best_bid, 4),
            }

        order = self.MarketOrderArgs(
            token_id=token_id,
            amount=shares,
            side="SELL",
            price=book.best_bid,
            order_type=self.OrderType.FOK,
        )
        signed = self.client.create_market_order(order)
        response = self.client.post_order(signed, self.OrderType.FOK)
        return {
            "ok": True,
            "paper": False,
            "side": "SELL",
            "token_id": token_id,
            "shares": shares,
            "price": book.best_bid,
            "usdc_amount": round(shares * book.best_bid, 4),
            "response": response,
        }


def default_order_usdc():
    return LADDER_ORDER_USDC
