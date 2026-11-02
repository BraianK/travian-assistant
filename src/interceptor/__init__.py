"""
Chrome DevTools Protocol (CDP) based HTTP interceptor module.

This package provides browser network interception capabilities using the
Chrome DevTools Protocol to capture and filter Travian Kingdoms game data.
"""

from .browser_cdp import ChromeCDPInterceptor
from .request_filter import RequestFilter

__all__ = ["ChromeCDPInterceptor", "RequestFilter"]
