"""
Timestamps on every line a long-running script writes, prints and
tracebacks alike, so that its log shows when each thing happened.
"""

import sys
import threading
import time


class Stamped:
    """A text stream that starts each line with the local time."""

    def __init__(self, stream):
        self.stream, self.at_line_start, self.lock = stream, True, threading.Lock()

    def write(self, text: str) -> int:
        with self.lock:
            out = []
            for line in text.splitlines(keepends=True):
                if self.at_line_start:
                    out.append(time.strftime("%Y-%m-%d %H:%M:%S%z "))
                out.append(line)
                self.at_line_start = line.endswith("\n")
            self.stream.write("".join(out))
        return len(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def stamp() -> None:
    sys.stdout, sys.stderr = Stamped(sys.stdout), Stamped(sys.stderr)
