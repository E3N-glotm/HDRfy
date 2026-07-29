import threading
import time
from pathlib import Path

import run_hdrfy
from hdrfy.config import ConversionConfig
from hdrfy.pipeline import ConversionResult
from hdrfy.script_runner import BatchItem


def _result(item: BatchItem) -> ConversionResult:
    return ConversionResult(
        input_path=item.source,
        output_path=item.output,
        width=8,
        height=8,
        padded_width=8,
        padded_height=8,
        peak_nits=1000.0,
        max_content_boost=1000.0 / 203.0,
        preset="natural",
        probe_output="valid",
        total_seconds=0.01,
    )


def test_threaded_batch_runner_executes_independent_items_concurrently(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def fake_process(
        index: int,
        item: BatchItem,
        _config: ConversionConfig,
    ) -> run_hdrfy.BatchOutcome:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return run_hdrfy.BatchOutcome(index=index, item=item, result=_result(item))

    monkeypatch.setattr(run_hdrfy, "process_item", fake_process)
    monkeypatch.setattr(run_hdrfy, "SHOW_TIMINGS", False)
    items = [
        BatchItem(
            source=tmp_path / f"input-{index}.png",
            output=tmp_path / f"output-{index}.jpg",
            intermediates=None,
        )
        for index in range(4)
    ]

    succeeded, failures = run_hdrfy.run_pending_items(
        list(enumerate(items, start=1)),
        total=len(items),
        config=ConversionConfig(),
        worker_count=2,
    )

    assert succeeded == 4
    assert not failures
    assert maximum_active == 2


def test_single_input_forces_one_batch_worker(monkeypatch) -> None:
    monkeypatch.setattr(run_hdrfy, "BATCH_WORKERS", 8)
    assert run_hdrfy.resolve_worker_count(1) == 1
