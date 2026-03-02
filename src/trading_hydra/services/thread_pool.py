"""
Thread Pool Manager for Trading Hydra
======================================

Provides a priority-based thread pool with "fast lane" for urgent tasks
and worker pool for normal/slow operations.

Architecture:
- Fast Lane (1 dedicated thread): Exit checks, stop-losses, urgent orders
- Worker Pool (3 threads): Quote fetches, bar history, chain lookups
- Priority Queue: URGENT > NORMAL > SLOW

Usage:
    pool = get_thread_pool()
    
    # Urgent task (fast lane)
    future = pool.submit_urgent(exit_check_func, symbol="AAPL")
    
    # Normal task (worker pool)
    future = pool.submit(fetch_quotes_func, symbols=["SPY", "QQQ"])
    
    # Slow task (worker pool, lower priority)
    future = pool.submit_slow(options_chain_lookup, symbol="SPY")
    
    # Fire-and-forget (no result needed)
    pool.fire_and_forget(log_to_hub, data={"event": "trade"})
"""

from typing import Callable, Any, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from queue import PriorityQueue
import threading
import time
import traceback

from ..core.logging import get_logger


class TaskPriority(IntEnum):
    """Task priority levels. Lower number = higher priority."""
    URGENT = 0   # Exit checks, stop-losses
    NORMAL = 5   # Quote fetches, standard operations
    SLOW = 10    # Chain lookups, heavy API calls


@dataclass(order=True)
class PrioritizedTask:
    """Task wrapper with priority for queue ordering."""
    priority: int
    timestamp: float = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    task_id: str = field(compare=False, default="")
    task_type: str = field(compare=False, default="unknown")


@dataclass
class PoolStats:
    """Thread pool statistics."""
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    urgent_tasks: int = 0
    normal_tasks: int = 0
    slow_tasks: int = 0
    avg_wait_time_ms: float = 0.0
    avg_exec_time_ms: float = 0.0
    last_reset: datetime = field(default_factory=datetime.now)


class ThreadPoolManager:
    """
    Priority-based thread pool manager.
    
    Fast Lane: Dedicated thread for urgent tasks (exits, stops)
    Worker Pool: General purpose workers for normal operations
    """
    
    _instance: Optional['ThreadPoolManager'] = None
    _lock = threading.Lock()
    
    def __init__(
        self,
        fast_lane_size: int = 1,
        worker_pool_size: int = 3,
        max_queue_size: int = 100
    ):
        self._logger = get_logger()
        self._shutdown = False
        self._shutdown_event = threading.Event()
        
        # Fast lane for urgent tasks
        self._fast_lane = ThreadPoolExecutor(
            max_workers=fast_lane_size,
            thread_name_prefix="FastLane"
        )
        
        # Worker pool for normal/slow tasks
        self._worker_pool = ThreadPoolExecutor(
            max_workers=worker_pool_size,
            thread_name_prefix="Worker"
        )
        
        # Priority queue for task ordering
        self._task_queue: PriorityQueue = PriorityQueue(maxsize=max_queue_size)
        
        # Statistics
        self._stats = PoolStats()
        self._stats_lock = threading.Lock()
        
        # Timing tracking
        self._pending_tasks: Dict[str, float] = {}  # task_id -> submit_time
        self._timing_lock = threading.Lock()
        
        # Task counter for unique IDs
        self._task_counter = 0
        self._counter_lock = threading.Lock()
        
        self._logger.log("thread_pool_initialized", {
            "fast_lane_size": fast_lane_size,
            "worker_pool_size": worker_pool_size,
            "max_queue_size": max_queue_size
        })
    
    def _generate_task_id(self) -> str:
        """Generate unique task ID."""
        with self._counter_lock:
            self._task_counter += 1
            return f"task_{self._task_counter}_{int(time.time() * 1000) % 100000}"
    
    def _wrap_task(
        self,
        func: Callable,
        task_id: str,
        task_type: str,
        *args,
        **kwargs
    ) -> Callable:
        """Wrap task with timing and error handling."""
        def wrapped():
            start_time = time.time()
            
            # Calculate wait time
            with self._timing_lock:
                submit_time = self._pending_tasks.pop(task_id, start_time)
            wait_time_ms = (start_time - submit_time) * 1000
            
            try:
                result = func(*args, **kwargs)
                exec_time_ms = (time.time() - start_time) * 1000
                
                # Update stats
                with self._stats_lock:
                    self._stats.tasks_completed += 1
                    # Rolling average
                    n = self._stats.tasks_completed
                    self._stats.avg_wait_time_ms = (
                        (self._stats.avg_wait_time_ms * (n - 1) + wait_time_ms) / n
                    )
                    self._stats.avg_exec_time_ms = (
                        (self._stats.avg_exec_time_ms * (n - 1) + exec_time_ms) / n
                    )
                
                # Log slow tasks
                if exec_time_ms > 1000:
                    self._logger.log("thread_pool_slow_task", {
                        "task_id": task_id,
                        "task_type": task_type,
                        "exec_time_ms": round(exec_time_ms, 1),
                        "wait_time_ms": round(wait_time_ms, 1)
                    })
                
                return result
                
            except Exception as e:
                exec_time_ms = (time.time() - start_time) * 1000
                
                with self._stats_lock:
                    self._stats.tasks_failed += 1
                
                self._logger.log("thread_pool_task_error", {
                    "task_id": task_id,
                    "task_type": task_type,
                    "error": str(e),
                    "exec_time_ms": round(exec_time_ms, 1),
                    "traceback": traceback.format_exc()[:500]
                })
                raise
        
        return wrapped
    
    def submit_urgent(
        self,
        func: Callable,
        *args,
        task_type: str = "urgent",
        **kwargs
    ) -> Future:
        """
        Submit urgent task to fast lane.
        Use for: exit checks, stop-loss triggers, urgent orders.
        """
        if self._shutdown:
            raise RuntimeError("Thread pool is shutting down")
        
        task_id = self._generate_task_id()
        
        with self._timing_lock:
            self._pending_tasks[task_id] = time.time()
        
        with self._stats_lock:
            self._stats.tasks_submitted += 1
            self._stats.urgent_tasks += 1
        
        wrapped = self._wrap_task(func, task_id, task_type, *args, **kwargs)
        return self._fast_lane.submit(wrapped)
    
    def submit(
        self,
        func: Callable,
        *args,
        task_type: str = "normal",
        **kwargs
    ) -> Future:
        """
        Submit normal task to worker pool.
        Use for: quote fetches, bar history, standard operations.
        """
        if self._shutdown:
            raise RuntimeError("Thread pool is shutting down")
        
        task_id = self._generate_task_id()
        
        with self._timing_lock:
            self._pending_tasks[task_id] = time.time()
        
        with self._stats_lock:
            self._stats.tasks_submitted += 1
            self._stats.normal_tasks += 1
        
        wrapped = self._wrap_task(func, task_id, task_type, *args, **kwargs)
        return self._worker_pool.submit(wrapped)
    
    def submit_slow(
        self,
        func: Callable,
        *args,
        task_type: str = "slow",
        **kwargs
    ) -> Future:
        """
        Submit slow task to worker pool (same pool, tracked separately).
        Use for: options chain lookups, heavy API calls.
        """
        if self._shutdown:
            raise RuntimeError("Thread pool is shutting down")
        
        task_id = self._generate_task_id()
        
        with self._timing_lock:
            self._pending_tasks[task_id] = time.time()
        
        with self._stats_lock:
            self._stats.tasks_submitted += 1
            self._stats.slow_tasks += 1
        
        wrapped = self._wrap_task(func, task_id, task_type, *args, **kwargs)
        return self._worker_pool.submit(wrapped)
    
    def fire_and_forget(
        self,
        func: Callable,
        *args,
        task_type: str = "fire_forget",
        **kwargs
    ) -> None:
        """
        Submit task without waiting for result.
        Use for: logging, hub sync, non-critical operations.
        """
        if self._shutdown:
            return  # Silently ignore during shutdown
        
        task_id = self._generate_task_id()
        
        with self._timing_lock:
            self._pending_tasks[task_id] = time.time()
        
        with self._stats_lock:
            self._stats.tasks_submitted += 1
            self._stats.normal_tasks += 1
        
        wrapped = self._wrap_task(func, task_id, task_type, *args, **kwargs)
        self._worker_pool.submit(wrapped)
    
    def submit_batch(
        self,
        tasks: List[tuple],
        task_type: str = "batch"
    ) -> List[Future]:
        """
        Submit multiple tasks at once.
        
        Args:
            tasks: List of (func, args, kwargs) tuples
            task_type: Type label for logging
            
        Returns:
            List of futures
        """
        futures = []
        for func, args, kwargs in tasks:
            future = self.submit(func, *args, task_type=task_type, **kwargs)
            futures.append(future)
        return futures
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current pool statistics."""
        with self._stats_lock:
            return {
                "tasks_submitted": self._stats.tasks_submitted,
                "tasks_completed": self._stats.tasks_completed,
                "tasks_failed": self._stats.tasks_failed,
                "urgent_tasks": self._stats.urgent_tasks,
                "normal_tasks": self._stats.normal_tasks,
                "slow_tasks": self._stats.slow_tasks,
                "avg_wait_time_ms": round(self._stats.avg_wait_time_ms, 2),
                "avg_exec_time_ms": round(self._stats.avg_exec_time_ms, 2),
                "pending_tasks": len(self._pending_tasks),
                "uptime_seconds": (datetime.now() - self._stats.last_reset).total_seconds()
            }
    
    def reset_stats(self) -> None:
        """Reset pool statistics."""
        with self._stats_lock:
            self._stats = PoolStats()
    
    def log_stats(self) -> None:
        """Log current pool statistics."""
        stats = self.get_stats()
        self._logger.log("thread_pool_stats", stats)
    
    def is_healthy(self) -> bool:
        """Check if pool is healthy (not shutdown, not overwhelmed)."""
        if self._shutdown:
            return False
        
        with self._timing_lock:
            pending = len(self._pending_tasks)
        
        # Unhealthy if too many pending tasks
        return pending < 50
    
    def shutdown(self, wait: bool = True, timeout: float = 10.0) -> None:
        """
        Gracefully shutdown the thread pool.
        
        Args:
            wait: Wait for pending tasks to complete
            timeout: Maximum seconds to wait
        """
        self._shutdown = True
        self._shutdown_event.set()
        
        self._logger.log("thread_pool_shutdown_start", {
            "wait": wait,
            "timeout": timeout,
            "pending_tasks": len(self._pending_tasks)
        })
        
        # Log final stats
        self.log_stats()
        
        # Shutdown executors
        self._fast_lane.shutdown(wait=wait)
        self._worker_pool.shutdown(wait=wait)
        
        self._logger.log("thread_pool_shutdown_complete", {})
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False


# Singleton instance
_thread_pool: Optional[ThreadPoolManager] = None
_pool_lock = threading.Lock()


def get_thread_pool(
    fast_lane_size: int = 1,
    worker_pool_size: int = 3
) -> ThreadPoolManager:
    """
    Get or create the global thread pool instance.
    
    Args:
        fast_lane_size: Number of fast lane threads (default 1)
        worker_pool_size: Number of worker threads (default 3)
    
    Returns:
        ThreadPoolManager singleton
    """
    global _thread_pool
    
    if _thread_pool is None:
        with _pool_lock:
            if _thread_pool is None:
                _thread_pool = ThreadPoolManager(
                    fast_lane_size=fast_lane_size,
                    worker_pool_size=worker_pool_size
                )
    
    return _thread_pool


def shutdown_thread_pool(wait: bool = True) -> None:
    """Shutdown the global thread pool."""
    global _thread_pool
    
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=wait)
        _thread_pool = None
