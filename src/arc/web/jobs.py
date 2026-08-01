"""생성 작업 큐 — 진행 상황을 스트리밍하기 위한 최소 장치.

왜 필요한가
-----------
LLM까지 켜면 생성이 30~40초 걸린다. 폼을 POST하고 기다리는 동안 화면에 아무
반응이 없으면 사용자는 **멈춘 줄 알고 다시 누른다.** 그러면 LLM 호출이 두 배가
되고 요금도 두 배가 된다.

설계
----
* 작업을 **백그라운드 스레드**에서 돌린다. `build_report`가 동기 함수이고
  네트워크 대기가 대부분이라 스레드로 충분하다.
* 진행 단계는 `Job.events`에 쌓이고 SSE로 흘려보낸다.
* 결과는 메모리에 두고 완료 후 페이지가 읽어 간다.

메모리 저장의 전제
------------------
**워커 1개**를 전제한다(Dockerfile 참조 — corpCode 캐시·LLM 예산도 같은 전제).
워커를 늘리면 작업이 다른 프로세스에 생겨 조회가 실패한다. 그때는 Redis 같은
공유 저장소로 옮겨야 한다.

오래된 작업은 지운다. 안 지우면 리포트 결과가 메모리에 계속 쌓인다.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

# 완료 후 이 시간이 지나면 버린다. 사용자가 결과 페이지를 여는 데 필요한
# 시간보다 넉넉하되, 메모리에 오래 남지 않을 만큼 짧게.
JOB_TTL_SECONDS = 30 * 60
MAX_JOBS = 50


@dataclass
class Job:
    """생성 작업 하나."""

    id: str
    created_at: float = field(default_factory=time.monotonic)
    events: list[tuple[str, str]] = field(default_factory=list)
    done: bool = False
    result: object | None = None
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, key: str, message: str) -> None:
        with self._lock:
            self.events.append((key, message))

    def snapshot(self, since: int) -> list[tuple[str, str]]:
        with self._lock:
            return self.events[since:]

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self.events)


class JobStore:
    """작업 보관소. 워커 1개 전제 (모듈 docstring 참조)."""

    def __init__(self, ttl: float = JOB_TTL_SECONDS, max_jobs: int = MAX_JOBS) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.ttl = ttl
        self.max_jobs = max_jobs

    def _evict(self) -> None:
        """TTL 지난 것부터, 그래도 많으면 오래된 순으로 버린다."""
        now = time.monotonic()
        stale = [k for k, j in self._jobs.items() if j.done and now - j.created_at > self.ttl]
        for k in stale:
            self._jobs.pop(k, None)
        if len(self._jobs) > self.max_jobs:
            oldest = sorted(self._jobs.items(), key=lambda kv: kv[1].created_at)
            for k, _ in oldest[: len(self._jobs) - self.max_jobs]:
                self._jobs.pop(k, None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, work: Callable[[Job], object]) -> Job:
        """작업을 만들고 백그라운드에서 실행한다."""
        job = Job(id=uuid.uuid4().hex[:16])
        with self._lock:
            self._evict()
            self._jobs[job.id] = job

        def run() -> None:
            try:
                job.result = work(job)
            except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여주는 게 목적이다
                job.error = f"{type(exc).__name__}: {exc}"
                job.emit("error", job.error)
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True, name=f"arc-job-{job.id}").start()
        return job
