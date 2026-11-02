"""Main entry point for Travian Kingdoms Assistant."""

import sys
import logging

try:
    from src.app import TravelAssistantApp
    from src.config.paths import get_data_dir
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from app import TravelAssistantApp
    from config.paths import get_data_dir


def setup_logging():
    """Configure logging for the application."""
    log_path = get_data_dir() / "travian.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(log_path)),
        ],
    )


def main():
    """Main application entry point."""
    setup_logging()
    
    app = TravelAssistantApp()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
