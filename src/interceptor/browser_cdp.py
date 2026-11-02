"""
Chrome DevTools Protocol (CDP) based HTTP interceptor for Travian Kingdoms.

This module provides a high-level interface to Chrome's DevTools Protocol,
enabling network interception and data capture from the browser running
Travian Kingdoms game sessions.

Uses pychrome library as the CDP client:
    pip install pychrome
"""

import asyncio
import base64
import json
import logging
import time
from collections import deque
from typing import Any, Callable, Optional

try:
    import pychrome
except ImportError:
    pychrome = None

logger = logging.getLogger(__name__)


class NetworkRequest:
    """Represents a network request captured by the CDP interceptor."""

    def __init__(
        self,
        request_id: str,
        url: str,
        method: str,
        headers: dict | None = None,
        post_data: str | bytes | None = None,
        timestamp: float | None = None,
    ):
        self.request_id = request_id
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data
        self.timestamp = timestamp or time.time()
        self.response_data: Optional[dict] = None
        self.response_body: str | bytes | None = None
        self.status_code: int | None = None
        self.response_headers: dict | None = None

    @property
    def is_json_response(self) -> bool:
        """Check if response content-type indicates JSON."""
        if not self.response_headers:
            return False
        content_type = self.response_headers.get("Content-Type", "")
        return "application/json" in content_type or "text/plain" in content_type

    def get_json(self) -> Optional[dict]:
        """Parse response body as JSON if available."""
        if not self.response_body:
            return None
        try:
            return json.loads(self.response_body)
        except (json.JSONDecodeError, TypeError):
            return None


class ChromeCDPInterceptor:
    """Chrome CDP interceptor for capturing Travian Kingdoms network traffic.

    This class manages a Chrome browser instance and intercepts network requests
    using Chrome DevTools Protocol. It provides callbacks for processing captured
    data and stores request/response pairs for later parsing.

    Example usage:
        interceptor = ChromeCDPInterceptor(cdp_address="127.0.0.1:9222")
        interceptor.start()
        
        @interceptor.on_request("Network.requestSent", "village_state")
        def handle_village_update(request):
            print(f"Village state updated: {request.url}")
        
        # Later, when done
        interceptor.stop()
    """

    def __init__(
        self,
        cdp_address: str = "127.0.0.1:9222",
        cdp_port: int = 9222,
        max_stored_requests: int = 500,
        auto_refresh_interval: float = 30.0,
    ):
        """Initialize the CDP interceptor.

        Args:
            cdp_address: Chrome DevTools Protocol address (e.g., '127.0.0.1:9222').
            cdp_port: Port for CDP connection.
            max_stored_requests: Maximum number of requests to store in memory.
            auto_refresh_interval: Interval in seconds for automatic data refresh.
        """
        if pychrome is None:
            raise ImportError(
                "pychrome is required for CDP interception. "
                "Install it with: pip install pychrome"
            )

        self.cdp_address = cdp_address
        self.cdp_port = cdp_port
        self.max_stored_requests = max_stored_requests
        self.auto_refresh_interval = auto_refresh_interval

        # Browser and session management
        self.browser: Any = None
        self.tab: Any = None
        self._session: Any = None
        self._is_running: bool = False

        # Request storage (deque with max length)
        self._requests: deque[NetworkRequest] = deque(maxlen=max_stored_requests)

        # Callbacks for different event types
        self._callbacks: dict[str, list[Callable]] = {
            "request_sent": [],
            "response_received": [],
            "data_received": [],
            "error_received": [],
            "custom": [],
        }

        # Request filter instance
        from .request_filter import RequestFilter
        self.request_filter = RequestFilter()

        logger.info(
            "ChromeCDPInterceptor initialized with CDP address: %s:%d",
            cdp_address,
            cdp_port,
        )

    def _setup_event_handlers(self) -> None:
        """Set up CDP event handlers for network monitoring."""

        # Network.requestSent - when a request is initiated
        @self._session.event
        def NetworkRequestSent(params):
            request_id = params.get("requestId", "")
            request_data = params.get("request", {})
            url = request_data.get("url", "")
            method = request_data.get("method", "GET")
            headers = request_data.get("headers", {})
            post_data = request_data.get("postData", None)

            if self.request_filter.is_travian_request(url):
                req = NetworkRequest(
                    request_id=request_id,
                    url=url,
                    method=method,
                    headers=headers,
                    post_data=post_data,
                )
                self._requests.append(req)
                logger.debug("Travian request captured: %s (%s)", url, method)

                # Trigger request_sent callbacks
                for callback in self._callbacks["request_sent"]:
                    try:
                        callback(req)
                    except Exception as e:
                        logger.error("Error in request_sent callback: %s", e)

        # Network.responseReceived - when response headers are received
        @self._session.event
        def NetworkResponseReceived(params):
            request_id = params.get("requestId", "")
            status = params.get("response", {}).get("status", 0)
            headers = params.get("response", {}).get("headers", {})
            encoded_response_id = params.get("encodedResponseLength", 0)

            # Find matching request
            req = self._find_request_by_id(request_id)
            if req:
                req.status_code = status
                req.response_headers = headers

                logger.debug(
                    "Travian response received: %s (status: %d)",
                    req.url,
                    status,
                )

                for callback in self._callbacks["response_received"]:
                    try:
                        callback(req)
                    except Exception:
                        logger.exception("Error in response_received callback")

        # Network.loadingFinished - when response body is complete
        @self._session.event
        def NetworkLoadingFinished(params):
            request_id = params.get("requestId", "")
            encoded_length = params.get("encodedDataLength", 0)

            req = self._find_request_by_id(request_id)
            if req and self.request_filter.should_capture_response(req.url):
                # Fetch response body
                try:
                    body = self._session.call(
                        "Network.getResponseBody",
                        requestId=request_id,
                    )
                    response_body = body.get("body", "")
                    if body.get("base64Encoded"):
                        response_body = base64.b64decode(response_body)
                    req.response_body = response_body
                    logger.debug(
                        "Response body captured: %s (%d bytes)",
                        req.url,
                        len(req.response_body or ""),
                    )
                    for callback in self._callbacks["response_received"]:
                        try:
                            callback(req)
                        except Exception:
                            logger.exception("Error in response_received callback")
                    for callback in self._callbacks["data_received"]:
                        try:
                            callback(req)
                        except Exception:
                            logger.exception("Error in data_received callback")
                except Exception as e:
                    logger.warning("Failed to get response body for %s: %s", req.url, e)

        # Network.loadingFailed - when request fails
        @self._session.event
        def NetworkLoadingFailed(params):
            request_id = params.get("requestId", "")
            error_text = params.get("errorText", "Unknown error")

            req = self._find_request_by_id(request_id)
            if req:
                logger.warning("Travian request failed: %s (%s)", req.url, error_text)

            # Trigger error callbacks
            for callback in self._callbacks["error_received"]:
                try:
                    callback(request_id, error_text)
                except Exception as e:
                    logger.error("Error in error_received callback: %s", e)

    def _find_request_by_id(self, request_id: str) -> Optional[NetworkRequest]:
        """Find a stored request by its CDP request ID."""
        for req in self._requests:
            if req.request_id == request_id:
                return req
        return None

    def start(self, target_url: str | None = None) -> bool:
        """Start the browser and begin network interception.

        Args:
            target_url: Optional URL to navigate to (e.g., Travian game URL).

        Returns:
            True if successfully started.
        """
        try:
            # Connect to Chrome instance
            self.browser = pychrome.Browser(url=f"http://{self.cdp_address}:{self.cdp_port}")
            
            if not self.browser.tab_list():
                # Create new tab if no tabs available
                new_tab = self.browser.new_tab()
                new_tab.open()
                self.tab = new_tab.start()
                logger.info("Created new browser tab")
            else:
                # Use existing tab
                self.tab = self.browser.tab_list()[0]
                self.tab.start()
                logger.info("Using existing browser tab")

            self._session = self.tab.default_session
            self._setup_event_handlers()
            
            # Enable network domain events
            self._session.call("Network.enable")
            
            # Enable network interception (optional, for blocking/modifying requests)
            self._session.call(
                "Network.setBypassServiceWorker",
                bypass=True,
            )

            # Navigate to target URL if provided
            if target_url:
                logger.info("Navigating to: %s", target_url)
                self.tab.navigate(target_url, timeout=30)

            self._is_running = True
            logger.info("CDP interceptor started successfully")
            return True

        except Exception as e:
            logger.error("Failed to start CDP interceptor: %s", e)
            self._is_running = False
            return False

    def stop(self) -> None:
        """Stop the browser and release resources."""
        if self._is_running and self.tab:
            try:
                self.tab.stop()
                logger.info("Browser tab stopped")
            except Exception as e:
                logger.error("Error stopping tab: %s", e)

        if self.browser:
            try:
                self.browser.close()
                logger.info("Browser closed")
            except Exception as e:
                logger.error("Error closing browser: %s", e)

        self._is_running = False
        logger.info("CDP interceptor stopped")

    @property
    def is_running(self) -> bool:
        """Check if the interceptor is currently running."""
        return self._is_running

    def on(
        self, event_type: str, callback: Callable | None = None
    ) -> Callable | None:
        """Register a callback for a specific event type.

        Args:
            event_type: One of 'request_sent', 'response_received', 
                       'data_received', 'error_received', 'custom'.
            callback: Function to call when event occurs. If None, returns
                     the decorator function.

        Returns:
            The callback or a decorator function.
        """
        if event_type not in self._callbacks:
            raise ValueError(
                f"Invalid event type: {event_type}. "
                f"Valid types: {list(self._callbacks.keys())}"
            )

        def decorator(fn):
            self._callbacks[event_type].append(fn)
            logger.debug("Callback registered for event: %s", event_type)
            return fn

        if callback is not None:
            self._callbacks[event_type].append(callback)
            return None
        return decorator

    def get_travian_requests(self, category: str | None = None) -> list[NetworkRequest]:
        """Get stored Travian requests, optionally filtered by category.

        Args:
            category: Request category to filter by (e.g., 'village_state', 'attack').

        Returns:
            List of matching NetworkRequest objects.
        """
        if category is None:
            return list(self._requests)

        return [
            req for req in self._requests
            if self.request_filter.categorize_request(req.url) == category
        ]

    def get_latest_response(self, category: str) -> Optional[dict]:
        """Get the latest parsed JSON response for a request category.

        Args:
            category: Request category (e.g., 'village_state').

        Returns:
            Parsed JSON dict or None if not found.
        """
        requests = self.get_travian_requests(category)
        for req in reversed(requests):
            if req.is_json_response and req.response_body:
                return req.get_json()
        return None

    def refresh_game_data(self, screen_name: str = "village") -> bool:
        """Trigger a game data refresh by switching screens.

        This simulates what the game client does when refreshing village data.

        Args:
            screen_name: The screen to switch to (e.g., 'village', 'overview').

        Returns:
            True if the refresh was triggered successfully.
        """
        if not self._is_running or not self.tab:
            logger.warning("Cannot refresh: interceptor not running")
            return False

        try:
            # Navigate to the screen URL to trigger data refresh
            url = f"https://{self.request_filter.get_world_from_url(self.cdp_address)}/game.php?screen={screen_name}"
            self.tab.navigate(url, timeout=10)
            logger.info("Screen refresh triggered: %s", screen_name)
            return True
        except Exception as e:
            logger.error("Failed to refresh game data: %s", e)
            return False

    def execute_game_action(
        self, action_type: str, params: dict | None = None
    ) -> Optional[dict]:
        """Execute a game action via JavaScript in the browser.

        Args:
            action_type: Type of action (e.g., 'trainUnit', 'upgradeBuilding').
            params: Parameters for the action.

        Returns:
            The response from the game server as parsed JSON, or None.
        """
        if not self._is_running or not self.tab:
            logger.warning("Cannot execute action: interceptor not running")
            return None

        try:
            # Build JavaScript to trigger the game action
            js_parts = [f"Game.Server.Request('{action_type}'"]
            if params:
                js_parts.append(f", {json.dumps(params)}")
            js_parts.append(");")
            
            js_command = "".join(js_parts)
            
            # Execute in browser context
            result = self.tab.execute(js_command)
            logger.debug("Executed game action: %s", action_type)
            
            return result if isinstance(result, dict) else None

        except Exception as e:
            logger.error("Failed to execute game action %s: %s", action_type, e)
            return None

    def clear_stored_requests(self) -> None:
        """Clear all stored network requests."""
        count = len(self._requests)
        self._requests.clear()
        logger.info("Cleared %d stored requests", count)

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
