"""생성 작업 큐 테스트.

LLM까지 켜면 30~40초가 걸린다. 화면에 반응이 없으면 사용자가 멈춘 줄 알고
다시 누르고, 그러면 **LLM 호출과 요금이 두 배**가 된다. 이 파일이 지키는 건
진행 상황이 실제로 흘러나오는지와, 실패해도 작업이 매달려 있지 않은지다.
"""

from __future__ import annotations

import threading
import time

from arc.web.jobs import Job, JobStore


def _wait(job: Job, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not job.done and time.monotonic() < deadline:
        time.sleep(0.01)


class TestJobLifecycle:
    def test_runs_and_stores_result(self):
        store = JobStore()
        job = store.start(lambda j: "결과")
        _wait(job)
        assert job.done
        assert job.result == "결과"
        assert not job.error

    def test_progress_events_recorded_in_order(self):
        store = JobStore()

        def work(j):
            j.emit("a", "재무제표 수집")
            j.emit("b", "LLM 서술 생성")

        job = store.start(work)
        _wait(job)
        assert [k for k, _ in job.events] == ["a", "b"]
        assert job.events[1][1] == "LLM 서술 생성"

    def test_failure_is_captured_not_raised(self):
        """작업이 죽어도 스레드가 조용히 사라지면 안 된다 — 화면이 영원히 기다린다."""
        store = JobStore()
        job = store.start(lambda j: (_ for _ in ()).throw(RuntimeError("DART 응답 없음")))
        _wait(job)
        assert job.done
        assert "RuntimeError" in job.error
        assert any(k == "error" for k, _ in job.events)

    def test_snapshot_returns_only_new_events(self):
        """SSE는 이미 보낸 것을 다시 보내면 안 된다."""
        job = Job(id="x")
        job.emit("a", "하나")
        assert len(job.snapshot(0)) == 1
        job.emit("b", "둘")
        assert [m for _, m in job.snapshot(1)] == ["둘"]
        assert job.snapshot(2) == []

    def test_unknown_job_is_none(self):
        assert JobStore().get("없는아이디") is None


class TestEviction:
    def test_completed_jobs_expire(self):
        """안 지우면 리포트 결과가 메모리에 계속 쌓인다."""
        store = JobStore(ttl=0.0)
        old = store.start(lambda j: None)
        _wait(old)
        store.start(lambda j: None)  # _evict를 태운다
        assert store.get(old.id) is None

    def test_cap_drops_oldest(self):
        store = JobStore(ttl=10_000, max_jobs=3)
        jobs = []
        for _ in range(5):
            j = store.start(lambda j: None)
            _wait(j)
            jobs.append(j)
            time.sleep(0.005)  # created_at이 겹치지 않게
        assert store.get(jobs[0].id) is None
        assert store.get(jobs[-1].id) is not None

    def test_running_job_not_evicted_by_ttl(self):
        """진행 중인 작업을 지우면 사용자가 결과를 못 받는다."""
        store = JobStore(ttl=0.0)
        gate = threading.Event()
        running = store.start(lambda j: gate.wait(2.0))
        store.start(lambda j: None)
        assert store.get(running.id) is not None
        gate.set()
        _wait(running)


class TestThreadSafety:
    def test_concurrent_emits_all_land(self):
        job = Job(id="x")
        threads = [
            threading.Thread(target=lambda i=i: [job.emit(str(i), f"m{i}-{n}") for n in range(20)])
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert job.event_count == 80
