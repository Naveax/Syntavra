from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO, Callable, Mapping, Sequence


@dataclass(frozen=True)
class BoundedProcessResult:
    exit_code: int
    timed_out: bool
    output_limit_exceeded: bool
    stdout: bytes
    stderr: bytes
    stdout_bytes_seen: int
    stderr_bytes_seen: int
    duration_ms: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.output_limit_exceeded


class _BoundedCollector:
    def __init__(self, limit: int, exceeded: threading.Event) -> None:
        if limit < 0:
            raise ValueError("process output limit cannot be negative")
        self.limit = limit
        self.exceeded = exceeded
        self.buffer = bytearray()
        self.bytes_seen = 0
        self.error: BaseException | None = None

    def drain(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                self.bytes_seen += len(chunk)
                remaining = self.limit - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if self.bytes_seen > self.limit:
                    self.exceeded.set()
        except BaseException as error:  # reader errors must fail the parent operation
            self.error = error
            self.exceeded.set()
        finally:
            try:
                stream.close()
            except OSError:
                pass


class _InputWriter:
    def __init__(self, data: bytes | None) -> None:
        self.data = data
        self.error: BaseException | None = None

    def write(self, stream: BinaryIO | None) -> None:
        if stream is None:
            return
        try:
            if self.data:
                stream.write(self.data)
                stream.flush()
        except (BrokenPipeError, OSError) as error:
            self.error = error
        finally:
            try:
                stream.close()
            except OSError:
                pass


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: str,
    environment: Mapping[str, str],
    input_bytes: bytes | None,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    creationflags: int = 0,
    start_new_session: bool = False,
    preexec_fn: Callable[[], None] | None = None,
) -> BoundedProcessResult:
    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise ValueError("bounded process requires a safe argv sequence")
    if timeout_seconds <= 0:
        raise ValueError("bounded process timeout must be positive")
    if stdout_limit < 0 or stderr_limit < 0:
        raise ValueError("bounded process output limits cannot be negative")

    started = time.monotonic()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=start_new_session,
        creationflags=creationflags,
        preexec_fn=preexec_fn,
    )
    if process.stdout is None or process.stderr is None:
        terminate_process_tree(process)
        raise RuntimeError("bounded process pipes were not created")

    exceeded = threading.Event()
    stdout = _BoundedCollector(stdout_limit, exceeded)
    stderr = _BoundedCollector(stderr_limit, exceeded)
    writer = _InputWriter(input_bytes)
    threads = [
        threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True, name="syntavra-stdout-drain"),
        threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True, name="syntavra-stderr-drain"),
        threading.Thread(target=writer.write, args=(process.stdin,), daemon=True, name="syntavra-stdin-writer"),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    output_limit_exceeded = False
    deadline = started + timeout_seconds
    while process.poll() is None:
        if exceeded.is_set():
            output_limit_exceeded = True
            terminate_process_tree(process)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            terminate_process_tree(process)
            break
        time.sleep(min(0.02, remaining))

    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        terminate_process_tree(process)
        raise RuntimeError("bounded process transport thread did not terminate")
    if stdout.error is not None:
        raise RuntimeError("stdout transport failed") from stdout.error
    if stderr.error is not None:
        raise RuntimeError("stderr transport failed") from stderr.error

    return BoundedProcessResult(
        exit_code=int(process.returncode if process.returncode is not None else -1),
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded or stdout.bytes_seen > stdout_limit or stderr.bytes_seen > stderr_limit,
        stdout=bytes(stdout.buffer),
        stderr=bytes(stderr.buffer),
        stdout_bytes_seen=stdout.bytes_seen,
        stderr_bytes_seen=stderr.bytes_seen,
        duration_ms=round((time.monotonic() - started) * 1000.0, 3),
    )


__all__ = ["BoundedProcessResult", "run_bounded_process", "terminate_process_tree"]
