from __future__ import annotations

import threading
import time

from perlica.kernel.loading import LoadingReporter


class CaptureStream:
    def __init__(self, tty: bool):
        self._tty = tty
        self._chunks = []
        self._lock = threading.Lock()

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str):
        with self._lock:
            self._chunks.append(text)

    def flush(self):
        return None

    @property
    def value(self) -> str:
        with self._lock:
            return "".join(self._chunks)


def test_loading_non_tty_static_line():
    stream = CaptureStream(tty=False)
    reporter = LoadingReporter(stream=stream)

    reporter.start(context_id="ctx", session_id="sess_1", provider_id="codex")
    reporter.update("llm-call-1", detail="call")
    reporter.stop()

    assert "正在运行" in stream.value
    assert "context=ctx" in stream.value
    assert "session=sess_1" in stream.value


def test_loading_tty_spinner_updates():
    stream = CaptureStream(tty=True)
    reporter = LoadingReporter(stream=stream)

    reporter.start(context_id="ctx", session_id="sess_1", provider_id="codex")
    reporter.update("llm-call-1", detail="first")
    time.sleep(0.25)
    reporter.update("finalize", detail="done")
    time.sleep(0.15)
    reporter.stop()

    assert "llm-1" in stream.value or "finalize" in stream.value


def test_loading_truncates_long_ids():
    stream = CaptureStream(tty=True)
    reporter = LoadingReporter(stream=stream)

    reporter.start(
        context_id="default",
        session_id="sess_6a23a74cf49a4cad993f85ac8b298fe6",
        provider_id="claude",
    )
    reporter.update("llm-call-1", detail="very-long-detail-for-tool-dispatch-step")
    time.sleep(0.2)
    reporter.stop()

    value = stream.value
    assert "sess_" in value
    assert ".." in value
