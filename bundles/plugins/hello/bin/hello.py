"""An example long-running process with graceful shutdown."""
import logging
import signal
import threading

logger = logging.getLogger(__name__)
stop = threading.Event()


def shutdown(signum, frame):
    stop.set()


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logger.info("Hello service started")
    while not stop.wait(30):
        logger.info("Hello from the background")
    logger.info("Hello service stopped")


if __name__ == "__main__":
    main()
