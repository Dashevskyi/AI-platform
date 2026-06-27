"""In-process pipeline metrics (Prometheus text exposition, no extra deps)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class _Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests_total = 0
        self.tier0_hits = 0
        self.errors = 0
        self.stage_ms_sum: dict[str, float] = defaultdict(float)
        self.stage_ms_count: dict[str, int] = defaultdict(int)
        self.ttft_ms_sum = 0.0
        self.ttft_count = 0

    def record_request(self, *, tier0: bool = False, error: bool = False) -> None:
        with self._lock:
            self.requests_total += 1
            if tier0:
                self.tier0_hits += 1
            if error:
                self.errors += 1

    def record_stages(self, timings_ms: dict[str, int] | None) -> None:
        if not timings_ms:
            return
        with self._lock:
            for k, v in timings_ms.items():
                self.stage_ms_sum[k] += float(v)
                self.stage_ms_count[k] += 1

    def record_ttft(self, ms: int) -> None:
        with self._lock:
            self.ttft_ms_sum += float(ms)
            self.ttft_count += 1

    def snapshot(self) -> dict:
        with self._lock:
            stages = {
                k: {
                    "count": self.stage_ms_count[k],
                    "avg_ms": round(self.stage_ms_sum[k] / max(self.stage_ms_count[k], 1), 1),
                }
                for k in self.stage_ms_sum
            }
            return {
                "requests_total": self.requests_total,
                "tier0_hits": self.tier0_hits,
                "errors": self.errors,
                "ttft_avg_ms": round(self.ttft_ms_sum / max(self.ttft_count, 1), 1),
                "ttft_samples": self.ttft_count,
                "stages": stages,
                "updated_at": time.time(),
            }

    def prometheus_text(self) -> str:
        s = self.snapshot()
        lines = [
            "# HELP ai_platform_pipeline_requests_total Chat completion requests",
            "# TYPE ai_platform_pipeline_requests_total counter",
            f"ai_platform_pipeline_requests_total {s['requests_total']}",
            "# HELP ai_platform_pipeline_tier0_hits_total Tier0 short-circuit hits",
            "# TYPE ai_platform_pipeline_tier0_hits_total counter",
            f"ai_platform_pipeline_tier0_hits_total {s['tier0_hits']}",
            "# HELP ai_platform_pipeline_errors_total Pipeline errors",
            "# TYPE ai_platform_pipeline_errors_total counter",
            f"ai_platform_pipeline_errors_total {s['errors']}",
            "# HELP ai_platform_pipeline_ttft_ms_avg Average time to first token (ms)",
            "# TYPE ai_platform_pipeline_ttft_ms_avg gauge",
            f"ai_platform_pipeline_ttft_ms_avg {s['ttft_avg_ms']}",
        ]
        for stage, data in s.get("stages", {}).items():
            safe = stage.replace("-", "_").replace(".", "_")
            lines.append(f"# TYPE ai_platform_pipeline_stage_ms_avg_{safe} gauge")
            lines.append(f"ai_platform_pipeline_stage_ms_avg_{safe} {data['avg_ms']}")
        return "\n".join(lines) + "\n"


pipeline_metrics = _Metrics()
