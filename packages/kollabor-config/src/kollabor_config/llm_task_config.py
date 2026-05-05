"""LLM Service Task Management Configuration.

Configuration settings for background task management in the LLM service.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BackgroundTasksConfig:
    """Configuration for background task management."""

    max_concurrent: int = 10000
    default_timeout: float = 0
    cleanup_interval: int = 60  # seconds
    enable_monitoring: bool = True
    log_task_events: bool = True
    log_task_errors: bool = True
    enable_metrics: bool = True

    # Advanced settings
    task_retry_attempts: int = 0
    task_retry_delay: float = 1.0
    enable_task_circuit_breaker: bool = False
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0


@dataclass
class QueueConfig:
    """Configuration for message queue management."""

    max_size: int = 1000
    overflow_strategy: str = "drop_oldest"  # drop_oldest, drop_newest, block
    block_timeout: Optional[float] = 1.0
    enable_queue_metrics: bool = True
    log_queue_events: bool = True


@dataclass
class LLMTaskConfig:
    """Complete LLM service task configuration."""

    background_tasks: BackgroundTasksConfig
    queue: QueueConfig

    @classmethod
    def from_dict(cls, config_dict: dict) -> "LLMTaskConfig":
        """Create configuration from dictionary."""
        # Background tasks config
        bg_tasks_config = BackgroundTasksConfig(
            max_concurrent=config_dict.get("background_tasks", {}).get(
                "max_concurrent", 50
            ),
            default_timeout=config_dict.get("background_tasks", {}).get(
                "default_timeout", 30.0
            ),
            cleanup_interval=config_dict.get("background_tasks", {}).get(
                "cleanup_interval", 60
            ),
            enable_monitoring=config_dict.get("background_tasks", {}).get(
                "enable_monitoring", True
            ),
            log_task_events=config_dict.get("background_tasks", {}).get(
                "log_task_events", True
            ),
            log_task_errors=config_dict.get("background_tasks", {}).get(
                "log_task_errors", True
            ),
            enable_metrics=config_dict.get("background_tasks", {}).get(
                "enable_metrics", True
            ),
            task_retry_attempts=config_dict.get("background_tasks", {}).get(
                "task_retry_attempts", 0
            ),
            task_retry_delay=config_dict.get("background_tasks", {}).get(
                "task_retry_delay", 1.0
            ),
            enable_task_circuit_breaker=config_dict.get("background_tasks", {}).get(
                "enable_task_circuit_breaker", False
            ),
            circuit_breaker_threshold=config_dict.get("background_tasks", {}).get(
                "circuit_breaker_threshold", 5
            ),
            circuit_breaker_timeout=config_dict.get("background_tasks", {}).get(
                "circuit_breaker_timeout", 60.0
            ),
        )

        # Queue config
        queue_config = QueueConfig(
            max_size=config_dict.get("queue", {}).get("max_size", 1000),
            overflow_strategy=config_dict.get("queue", {}).get(
                "overflow_strategy", "drop_oldest"
            ),
            block_timeout=config_dict.get("queue", {}).get("block_timeout", 1.0),
            enable_queue_metrics=config_dict.get("queue", {}).get(
                "enable_queue_metrics", True
            ),
            log_queue_events=config_dict.get("queue", {}).get("log_queue_events", True),
        )

        return cls(background_tasks=bg_tasks_config, queue=queue_config)

    @classmethod
    def default(cls) -> "LLMTaskConfig":
        """Get default configuration."""
        return cls(background_tasks=BackgroundTasksConfig(), queue=QueueConfig())


# Default configuration template
DEFAULT_TASK_CONFIG = {
    "background_tasks": {
        "max_concurrent": 10000,
        "default_timeout": 0,
        "cleanup_interval": 60,
        "enable_monitoring": True,
        "log_task_events": True,
        "log_task_errors": True,
        "enable_metrics": True,
        "task_retry_attempts": 0,
        "task_retry_delay": 1.0,
        "enable_task_circuit_breaker": False,
        "circuit_breaker_threshold": 5,
        "circuit_breaker_timeout": 60.0,
    },
    "queue": {
        "max_size": 1000,
        "overflow_strategy": "drop_oldest",
        "block_timeout": 1.0,
        "enable_queue_metrics": True,
        "log_queue_events": True,
    },
}
