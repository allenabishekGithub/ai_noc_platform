# File: main.py
"""
Entry point for the Agentic Module.

Boots the AgentCore (which wires up the LangChain agent, MCP client, A2A
interface, FAISS RAG store, OpenWebUI-compatible API and CLI) and keeps
the process alive until a shutdown signal is received.
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import logging
import signal
import sys
import time

from agent.autonomous_manager import AutonomousManager
from utils.event_timing import log_event, timed_event

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agentic_module.log")
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

agent: AutonomousManager = None  # type: ignore
shutdown_requested = False


def signal_handler(sig, frame):
    global shutdown_requested
    shutdown_requested = True
    log_event(logger, "shutdown_signal", signal=sig)
    raise KeyboardInterrupt


def parse_args():
    parser = argparse.ArgumentParser(
        description="Agentic Module - LangChain agent with MCP, A2A, FAISS RAG"
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Run an interactive CLI prompt instead of (only) starting the server."
    )
    parser.add_argument(
        "--query", "-q", type=str, default=None,
        help="Run a single query and exit."
    )
    parser.add_argument(
        "--no-server", action="store_true",
        help="Do not start the A2A / OpenWebUI HTTP server."
    )
    return parser.parse_args()


if __name__ == "__main__":
    with timed_event(logger, "parse_args"):
        args = parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    with timed_event(logger, "autonomous_manager_init"):
        agent = AutonomousManager()

    if not args.no_server:
        with timed_event(logger, "server_start"):
            agent.start()

    # One-shot query mode
    if args.query:
        with timed_event(logger, "one_shot_query", query_len=len(args.query)):
            answer = agent.ask(args.query)
        print(answer)
        with timed_event(logger, "agent_stop_after_query"):
            agent.stop()
        sys.exit(0)

    # Interactive CLI mode
    if args.cli:
        from cli.cli_interface import run_cli
        try:
            with timed_event(logger, "cli_session"):
                run_cli(agent)
        finally:
            with timed_event(logger, "agent_stop_after_cli"):
                agent.stop()
        sys.exit(0)

    log_event(logger, "agent_running_idle_loop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_event(logger, "keyboard_interrupt")
    finally:
        with timed_event(logger, "agent_stop_on_shutdown"):
            agent.stop()
        log_event(logger, "shutdown_complete")
