# File: cli_interface.py
"""
Interactive CLI for the Agentic Module.

Run with `python main.py --cli` or pipe a one-shot query with
`python main.py --query "..."`.

Special commands:
  /exit  /quit       Quit the CLI
  /reset             Reset the session chat history
  /ingest <path>     Ingest a local text file into the FAISS store
  /help              Show available commands
"""
import logging
import os
import sys
from utils.event_timing import log_event, timed_event

LOGGER = logging.getLogger(__name__)

HELP = """\
Available commands:
  /help              Show this help
  /exit, /quit       Quit
  /reset             Clear conversation history
  /ingest <path>     Ingest a text file into the RAG store
  Otherwise, your text is sent to the agent.
"""


def run_cli(agent_core):
    session_id = "cli"
    log_event(LOGGER, "cli_started", session_id=session_id)
    print("Agentic Module CLI - type /help for commands, /exit to quit.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            log_event(LOGGER, "cli_stopped", reason="interrupt")
            return

        if not line:
            continue

        if line in ("/exit", "/quit"):
            log_event(LOGGER, "cli_stopped", reason="user_exit")
            return
        if line == "/help":
            print(HELP)
            continue
        if line == "/reset":
            with timed_event(LOGGER, "cli_reset_session"):
                agent_core.agent._sessions.pop(session_id, None)
            print("(session reset)")
            continue
        if line.startswith("/ingest "):
            path = line[len("/ingest "):].strip()
            if not os.path.isfile(path):
                print(f"file not found: {path}")
                continue
            with timed_event(LOGGER, "cli_ingest_file", path=path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                added = agent_core.ingest_documents([
                    {"text": text, "metadata": {"source": path}}
                ])
            print(f"(ingested {added} chunks from {path})")
            continue

        try:
            with timed_event(LOGGER, "cli_query", query_len=len(line)):
                answer = agent_core.ask(line, session_id=session_id)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            continue

        print(f"agent> {answer}")
