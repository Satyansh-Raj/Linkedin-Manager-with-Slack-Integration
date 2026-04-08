import logging
from dotenv import load_dotenv
import slack_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    load_dotenv()
    log.info("Starting AI LinkedIn Manager V3...")
    log.info("Connecting to Slack Socket Mode...")

    try:
        slack_module.start_socket_listener()
    except KeyboardInterrupt:
        log.info("Bot shut down manually.")
    except Exception as e:
        log.error(f"Fatal error: {e}")


if __name__ == "__main__":
    main()
