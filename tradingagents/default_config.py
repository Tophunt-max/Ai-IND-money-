"""Settings of the market-data layer (``tradingagents.dataflows``): data vendors, the
yfinance symbol suffix, news limits and the cache directory. ``TRADINGAGENTS_CACHE_DIR``
moves the cache."""

import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")


def build_default_config() -> dict:
    return {
        "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR")
        or os.path.join(_TRADINGAGENTS_HOME, "cache"),
        "news_article_limit": 20,             # max articles per ticker (ticker-news)
        "global_news_article_limit": 10,      # max articles for global/macro news
        "global_news_lookback_days": 7,       # macro news lookback window
        "global_news_queries": [
            "Reserve Bank of India repo rate inflation",
            "NIFTY earnings GDP economic outlook India",
            "geopolitical risk trade war sanctions",
            "Federal Reserve interest rates",
            "oil commodities supply chain energy",
        ],
        # The exact vendor chain per category ("a,b" = ordered fallback)
        "data_vendors": {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "sec_edgar,yfinance",
            "news_data": "yfinance",
            "macro_data": "fred",
            "prediction_markets": "polymarket",
        },
        # Tool-level overrides (take precedence over the category)
        "tool_vendors": {},
        # Suffix route_to_vendor appends to bare symbols sent to yfinance (".NS" for NSE)
        "yfinance_symbol_suffix": "",
    }


DEFAULT_CONFIG = build_default_config()
