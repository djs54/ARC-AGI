import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import psutil

@dataclass
class BenchmarkConfig:
    name: str
    description: str = ""
    timeout: int = 3600  # Default 1 hour
    memory_limit_gb: float = 8.0
    cpu_limit_percent: float = 80.0
    parameters: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BenchmarkResult:
    benchmark_name: str
    success: bool
    duration: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

class BenchmarkHarness(abc.ABC):
    """
    Base class for all SideQuest benchmarks.
    """

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.resource_stats: List[Dict[str, Any]] = []

    @abc.abstractmethod
    async def setup(self) -> None:
        """Initialize benchmark-specific resources (e.g., model loading)."""
        pass

    @abc.abstractmethod
    async def run(self) -> BenchmarkResult:
        """Execute the core benchmark logic."""
        pass

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Clean up benchmark-specific resources."""
        pass

    async def execute(self) -> BenchmarkResult:
        """Wrapper to handle setup, execution, teardown, and resource monitoring."""
        start_time = time.perf_counter()
        monitor_task = asyncio.create_task(self._monitor_resources())
        
        try:
            await self.setup()
            result = await asyncio.wait_for(self.run(), timeout=self.config.timeout)
            return result
        except asyncio.TimeoutError:
            return BenchmarkResult(
                benchmark_name=self.config.name,
                success=False,
                duration=time.perf_counter() - start_time,
                error=f"Benchmark timed out after {self.config.timeout} seconds"
            )
        except Exception as e:
            return BenchmarkResult(
                benchmark_name=self.config.name,
                success=False,
                duration=time.perf_counter() - start_time,
                error=str(e)
            )
        finally:
            monitor_task.cancel()
            await self.teardown()

    async def _monitor_resources(self) -> None:
        """Periodically log resource usage during benchmark execution."""
        process = psutil.Process()
        try:
            while True:
                mem_info = process.memory_info()
                cpu_percent = process.cpu_percent(interval=None)
                self.resource_stats.append({
                    "timestamp": time.time(),
                    "memory_rss_gb": mem_info.rss / (1024**3),
                    "cpu_percent": cpu_percent
                })
                # Check limits
                if mem_info.rss / (1024**3) > self.config.memory_limit_gb:
                    # In a real scenario, we might want to signal a stop here
                    pass
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
