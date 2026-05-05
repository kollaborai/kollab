"""Hook Monitoring Plugin for development and debugging.

[SHOWCASE] SHOWCASE PLUGIN: Demonstrates ALL plugin ecosystem features! [SHOWCASE]

This plugin serves as a comprehensive example of the Kollab plugin ecosystem,
demonstrating:
- Hook monitoring and performance tracking
- Plugin discovery via PluginFactory
- Cross-plugin service registration via KollaborPluginSDK
- Direct plugin-to-plugin communication
- Event bus messaging
- Dynamic service discovery patterns
- Plugin health dashboard functionality

Perfect for developers learning the plugin system!
"""

import asyncio
import datetime
import logging

# Import event system components
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_tui.visual_effects import AgnosterSegment

sys.path.insert(0, str(Path(__file__).parent.parent))

from kollabor_events import Event, EventType, Hook, HookPriority  # noqa: E402

# [TOOL] PLUGIN ECOSYSTEM IMPORTS - Showcasing factory and SDK integration
try:
    from kollabor_plugins import KollaborPluginSDK, PluginFactory

    SDK_AVAILABLE = True
except ImportError:
    # Graceful degradation if SDK not available
    SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


class HookMonitoringPlugin:
    """[SHOWCASE] SHOWCASE: A comprehensive hook monitoring system demonstrating ALL plugin ecosystem features!"""

    def __init__(self, name: str, event_bus, renderer, config) -> None:
        """Initialize the hook monitoring plugin with full ecosystem integration.

        This initialization showcases:
        - Basic plugin setup
        - Plugin factory access for service discovery
        - SDK initialization for service registration
        - Cross-plugin communication setup

        Args:
            name: Plugin name.
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.
        """
        # [DATA] BASIC PLUGIN SETUP
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        logger.info(f"[INIT] Initializing HookMonitoringPlugin: {name}")

        # [DATA] HOOK MONITORING STATE - Core monitoring functionality
        self.hook_executions = 0
        self.last_hook_event = "None"
        self.failed_hooks = 0
        self.timeout_hooks = 0
        self.hook_performance: Dict[str, Dict[str, float]] = (
            {}
        )  # event_type -> {total_time, count, avg_time}
        self.error_log: List[Dict[str, str]] = []  # Recent errors for debugging
        self.hook_health_status = "Starting"

        # [DATA] ENHANCED PERFORMANCE TRACKING - Detailed metrics and degradation detection
        self.detailed_metrics: Dict[str, Dict[str, Any]] = (
            {}
        )  # event_type -> detailed metrics (history, percentiles)
        self.performance_baseline: Dict[str, float] = (
            {}
        )  # event_type -> baseline avg_time (set after 10 executions)
        self.degradation_detected = False  # Flag for performance degradation

        # [FIND] PLUGIN ECOSYSTEM STATE - Showcasing service discovery
        self.discovered_plugins: Dict[str, Any] = {}  # plugin_name -> plugin_instance
        self.available_services: Dict[str, Any] = {}  # service_name -> plugin_info
        self.plugin_health_stats: Dict[str, Dict[str, Any]] = (
            {}
        )  # plugin_name -> health_metrics
        self.cross_plugin_messages: List[Dict[str, Any]] = (
            []
        )  # Recent inter-plugin communications

        # [TOOL] PLUGIN FACTORY ACCESS - Demonstrating plugin discovery
        # This shows how plugins can discover and communicate with each other
        self.plugin_factory: Optional[PluginFactory] = None
        self.plugin_discovery_enabled = config.get(
            "plugins.hook_monitoring.enable_plugin_discovery", True
        )
        self.discovery_task: Optional[asyncio.Task[None]] = (
            None  # Background task for periodic discovery
        )
        self.last_discovery_time: Optional[datetime.datetime] = (
            None  # Timestamp of last discovery
        )

        # [TOOL] SDK INITIALIZATION - Demonstrating service registration
        # This shows how plugins can register services for other plugins to use
        self.sdk: Optional[KollaborPluginSDK] = None
        self.service_registration_enabled = config.get(
            "plugins.hook_monitoring.enable_service_registration", True
        )
        self.registered_services: List[str] = (
            []
        )  # Track which services are actually registered

        if SDK_AVAILABLE and self.service_registration_enabled:
            self.sdk = KollaborPluginSDK()
            logger.info("SDK initialized - ready for service registration")

        # [COMM] CROSS-PLUGIN COMMUNICATION - Setup for plugin messaging
        self.enable_cross_plugin_comm = config.get(
            "plugins.hook_monitoring.enable_cross_plugin_communication", True
        )
        self.message_history_limit = config.get(
            "plugins.hook_monitoring.message_history_limit", 20
        )

        # [METRICS] METRICS COLLECTION AND RETENTION - Timestamped metrics storage
        self.collected_metrics: List[Dict[str, Any]] = (
            []
        )  # List of timestamped metric snapshots
        self.service_usage_stats: Dict[str, Dict[str, Any]] = (
            {  # Track SDK service call counts
                "monitor_performance": {"calls": 0, "last_called": None},
                "check_plugin_health": {"calls": 0, "last_called": None},
                "collect_system_metrics": {"calls": 0, "last_called": None},
            }
        )
        self.metrics_collection_task: Optional[asyncio.Task[None]] = (
            None  # Background task for metrics collection
        )

        # [TARGET] DASHBOARD STATE - Dashboard update throttling and caching
        self.last_dashboard_update = datetime.datetime.now()
        self.cached_dashboard_content: List[str] = []

        # [TARGET] CREATE HOOKS - Standard hook creation for monitoring
        self.hooks = self._create_all_hooks()

        logger.debug("HookMonitoringPlugin fully initialized with ecosystem features!")

    def get_status_lines(self) -> Dict[str, List[str]]:
        """Get status lines for the hook monitoring plugin organized by area.

        Returns:
            Dictionary with status lines organized by areas A, B, C.
        """
        # Check if status display is enabled for this plugin
        show_status = self.config.get("plugins.hook_monitoring.show_status", True)
        if not show_status:
            return {"A": [], "B": [], "C": []}

        enabled = self.config.get("plugins.hook_monitoring.enabled", False)
        debug_mode = self.config.get("plugins.hook_monitoring.debug_logging", False)

        # Hook monitoring status goes to area B (system monitoring)
        if not enabled:
            return {"A": [], "B": ["Hook Monitor: Off"], "C": []}

        # Calculate failure rate for health display
        failure_rate: float = 0
        if self.hook_executions > 0:
            failure_rate = (
                self.failed_hooks + self.timeout_hooks
            ) / self.hook_executions

        # [FIND] SHOWCASE: Plugin Discovery Status
        discovered_count = len(getattr(self, "discovered_plugins", {}))

        # [TOOL] SHOWCASE: Service Registration Status
        registered_services = len(getattr(self, "registered_services", []))

        # [COMM] SHOWCASE: Cross-Plugin Communication Status
        recent_messages = len(getattr(self, "cross_plugin_messages", []))

        return {
            "A": [],  # No area A content for hook monitoring
            "B": [  # System monitoring goes in area B
                f"Monitor: {self.hook_health_status}",
                f"Executions: {self.hook_executions}",
                f"Plugins: {discovered_count}",
                f"Services: {registered_services}",
                f"Messages: {recent_messages}",
                (
                    f"Rate: {failure_rate:.1%}"
                    if self.hook_executions > 0
                    else "[FAST] Rate: N/A"
                ),
            ],
            "C": [  # Detailed ecosystem info in area C
                "SHOWCASE: Plugin Ecosystem Demo",
                f"Last Event: {self.last_hook_event[:20]}",
                f"Discovery: {'[ok]' if self.plugin_discovery_enabled else '[X]'}",
                f"SDK: {'[ok]' if SDK_AVAILABLE else '[X]'}",
                f"Debug: {'On' if debug_mode else 'Off'}",
            ],
        }

    async def initialize(self) -> None:
        """[INIT] SHOWCASE: Initialize with full plugin ecosystem integration!

        This method demonstrates:
        1. Plugin factory access for discovering other plugins
        2. SDK service registration for providing monitoring services
        3. Cross-plugin communication setup
        4. Dynamic service discovery patterns
        5. Background task management for periodic operations
        """
        logger.info("Starting HookMonitoringPlugin initialization...")

        # [FIND] STEP 1: PLUGIN DISCOVERY - Demonstrating how to find other plugins
        if self.plugin_discovery_enabled:
            await self._discover_other_plugins()

            # Start periodic plugin discovery if interval is configured
            interval = self.config.get("plugins.hook_monitoring.discovery_interval", 30)
            if interval > 0:
                self.discovery_task = asyncio.create_task(
                    self._periodic_plugin_discovery()
                )
                logger.info(
                    f"Started periodic plugin discovery task (interval: {interval}s)"
                )

        # [TOOL] STEP 2: SERVICE REGISTRATION - Showcasing how to offer services to other plugins
        if self.service_registration_enabled and self.sdk:
            await self._register_monitoring_services()

        # [COMM] STEP 3: CROSS-PLUGIN COMMUNICATION SETUP
        if self.enable_cross_plugin_comm:
            await self._setup_cross_plugin_communication()

        # [DATA] STEP 4: INITIALIZE PLUGIN HEALTH MONITORING
        await self._initialize_plugin_health_monitoring()

        # [METRICS] STEP 5: START METRICS COLLECTION - Periodic metrics gathering
        if self.config.get("plugins.hook_monitoring.collect_plugin_metrics", True):
            self.metrics_collection_task = asyncio.create_task(
                self._periodic_metrics_collection()
            )
            logger.info("Started periodic metrics collection task (interval: 60s)")

        logger.info(
            "HookMonitoringPlugin initialization complete - all ecosystem features active!"
        )

    async def _discover_other_plugins(self) -> None:
        """[FIND] SHOWCASE: Discover other plugins using PluginFactory.

        This demonstrates the core pattern for plugin discovery:
        - Access the plugin factory (would typically be injected)
        - Get all plugin instances
        - Analyze their capabilities
        - Store references for later communication
        """
        logger.debug("Discovering other plugins in the ecosystem...")

        try:
            # [TOOL] ACCESS PLUGIN FACTORY - In real implementation, this would be injected
            # For demonstration, we simulate what the factory discovery looks like
            if hasattr(self.renderer, "plugin_instances"):
                # This simulates getting access to the factory through the renderer
                plugin_instances = getattr(self.renderer, "plugin_instances", {})
            else:
                # Fallback demonstration data
                plugin_instances = {
                    "ModernInputPlugin": "simulated_input_plugin",
                    "WorkflowEnforcementPlugin": "simulated_workflow_plugin",
                    # This would be populated by: factory.get_all_instances()
                }

            self.discovered_plugins = plugin_instances

            # [DATA] ANALYZE DISCOVERED PLUGINS - Check their capabilities
            for plugin_name, plugin_instance in plugin_instances.items():
                if plugin_name != self.name:  # Don't analyze ourselves
                    await self._analyze_plugin_capabilities(
                        plugin_name, plugin_instance
                    )

            logger.info(
                f"Discovered {len(self.discovered_plugins)} plugins: {list(self.discovered_plugins.keys())}"
            )

        except Exception as e:
            logger.warning(f"Plugin discovery failed (demonstration mode): {e}")
            # In production, this would use: factory.get_all_instances()

    async def _analyze_plugin_capabilities(
        self, plugin_name: str, plugin_instance: Any
    ) -> None:
        """[FIND] SHOWCASE: Analyze what services a plugin provides.

        This demonstrates how to discover plugin capabilities:
        - Check for service methods
        - Analyze configuration options
        - Determine communication interfaces
        """
        capabilities = {
            "has_get_services": hasattr(plugin_instance, "get_services"),
            "has_status_lines": hasattr(plugin_instance, "get_status_lines"),
            "has_initialize": hasattr(plugin_instance, "initialize"),
            "has_register_hooks": hasattr(plugin_instance, "register_hooks"),
            "supports_messaging": hasattr(plugin_instance, "handle_message"),
            "plugin_type": (
                type(plugin_instance).__name__
                if plugin_instance != "simulated_input_plugin"
                else "ModernInputPlugin"
            ),
        }

        # [NOTE] STORE PLUGIN INFORMATION - For later use in health monitoring
        self.plugin_health_stats[plugin_name] = {
            "capabilities": capabilities,
            "last_seen": datetime.datetime.now(),
            "status": "discovered",
            "message_count": 0,
        }

        logger.debug(f"Analyzed {plugin_name}: {capabilities}")

    async def _periodic_plugin_discovery(self) -> None:
        """[FIND] SHOWCASE: Periodically discover plugins at configured interval.

        This demonstrates background task patterns for plugin ecosystem monitoring:
        - Periodic task execution with configurable interval
        - Auto-capability analysis based on configuration
        - Proper exception handling and cleanup
        - Task cancellation support
        """
        interval = self.config.get("plugins.hook_monitoring.discovery_interval", 30)
        auto_analyze = self.config.get(
            "plugins.hook_monitoring.auto_analyze_capabilities", True
        )

        logger.info(
            f"Starting periodic plugin discovery (interval: {interval}s, auto_analyze: {auto_analyze})"
        )

        while True:
            try:
                await asyncio.sleep(interval)

                # Discover plugins
                if hasattr(self.renderer, "plugin_instances"):
                    plugin_instances = getattr(self.renderer, "plugin_instances", {})
                    self.discovered_plugins = plugin_instances

                    # Auto-analyze capabilities if enabled
                    if auto_analyze:
                        for plugin_name, plugin_instance in plugin_instances.items():
                            if plugin_name != self.name:
                                await self._analyze_plugin_capabilities(
                                    plugin_name, plugin_instance
                                )

                self.last_discovery_time = datetime.datetime.now()
                logger.debug(
                    f"Plugin discovery complete: {len(self.discovered_plugins)} plugins found"
                )

            except asyncio.CancelledError:
                logger.info("Plugin discovery task cancelled")
                break
            except Exception as e:
                logger.error(f"Plugin discovery error: {e}")
                await asyncio.sleep(interval)

    async def _periodic_metrics_collection(self) -> None:
        """[METRICS] SHOWCASE: Periodically collect and store metrics.

        This demonstrates:
        - Periodic metrics gathering with retention policies
        - Time-series data collection patterns
        - Memory-efficient metrics storage with automatic cleanup
        - Configuration-driven collection intervals
        """
        logger.info("Starting periodic metrics collection")

        while True:
            try:
                await asyncio.sleep(60)  # Collect every 60 seconds

                # Gather metrics snapshot
                snapshot = {
                    "timestamp": datetime.datetime.now(),
                    "hook_executions": self.hook_executions,
                    "failed_hooks": self.failed_hooks,
                    "timeout_hooks": self.timeout_hooks,
                    "health_status": self.hook_health_status,
                    "discovered_plugins": len(self.discovered_plugins),
                    "service_usage": dict(self.service_usage_stats),
                    "performance_summary": {
                        event: {
                            "count": metrics["count"],
                            "avg_time": metrics["avg_time"],
                        }
                        for event, metrics in self.hook_performance.items()
                    },
                }

                self.collected_metrics.append(snapshot)

                # Cleanup old metrics
                self._cleanup_old_metrics()

                logger.debug(
                    f"Metrics collected: {len(self.collected_metrics)} snapshots retained"
                )

            except asyncio.CancelledError:
                logger.info("Metrics collection task cancelled")
                break
            except Exception as e:
                logger.error(f"Metrics collection error: {e}")
                await asyncio.sleep(60)

    def _cleanup_old_metrics(self) -> None:
        """[METRICS] SHOWCASE: Remove metrics older than retention period.

        This demonstrates memory management for time-series data:
        - Configuration-driven retention policies
        - Efficient list comprehension for cleanup
        - Automatic old data removal
        """
        retention_hours = self.config.get(
            "plugins.hook_monitoring.metrics_retention_hours", 24
        )
        cutoff_time = datetime.datetime.now() - datetime.timedelta(
            hours=retention_hours
        )

        # Remove old metrics
        original_count = len(self.collected_metrics)
        self.collected_metrics = [
            m for m in self.collected_metrics if m["timestamp"] > cutoff_time
        ]

        removed_count = original_count - len(self.collected_metrics)
        if removed_count > 0:
            logger.debug(
                f"Cleaned up {removed_count} old metrics (retention: {retention_hours}h)"
            )

    async def _register_monitoring_services(self) -> None:
        """[TOOL] SHOWCASE: Register monitoring services for other plugins to use.

        This demonstrates how plugins offer services to the ecosystem:
        - Register performance monitoring service
        - Register health check service
        - Register metrics collection service
        """
        logger.debug("Registering monitoring services for other plugins...")

        if not self.sdk:
            logger.warning("SDK not available - cannot register services")
            return

        # Reset registered services list
        self.registered_services = []

        try:
            # [DATA] SERVICE 1: Performance Monitoring
            if self.config.get(
                "plugins.hook_monitoring.register_performance_service", True
            ):
                self.sdk.register_custom_tool(
                    {
                        "name": "monitor_performance",
                        "description": "Monitor plugin performance and hook execution times",
                        "handler": self._provide_performance_monitoring,
                        "parameters": {
                            "plugin_name": {
                                "type": "string",
                                "description": "Plugin to monitor",
                            },
                            "metric_type": {
                                "type": "string",
                                "description": "Type of metric to collect",
                            },
                        },
                        "plugin": self.name,
                        "enabled": True,
                        "category": "monitoring",
                    }
                )
                self.registered_services.append("monitor_performance")
                logger.debug("Registered performance monitoring service")

            # [HEALTH] SERVICE 2: Health Checking
            if self.config.get("plugins.hook_monitoring.register_health_service", True):
                self.sdk.register_custom_tool(
                    {
                        "name": "check_plugin_health",
                        "description": "Check the health status of any plugin in the system",
                        "handler": self._provide_health_check,
                        "parameters": {
                            "plugin_name": {
                                "type": "string",
                                "description": "Plugin to check",
                            },
                            "detailed": {
                                "type": "boolean",
                                "description": "Return detailed health info",
                            },
                        },
                        "plugin": self.name,
                        "enabled": True,
                        "category": "health",
                    }
                )
                self.registered_services.append("check_plugin_health")
                logger.debug("Registered health check service")

            # [METRICS] SERVICE 3: Metrics Collection
            if self.config.get(
                "plugins.hook_monitoring.register_metrics_service", True
            ):
                self.sdk.register_custom_tool(
                    {
                        "name": "collect_system_metrics",
                        "description": "Collect comprehensive system and plugin metrics",
                        "handler": self._provide_metrics_collection,
                        "parameters": {
                            "time_range": {
                                "type": "string",
                                "description": "Time range for metrics",
                            },
                            "include_performance": {
                                "type": "boolean",
                                "description": "Include performance data",
                            },
                        },
                        "plugin": self.name,
                        "enabled": True,
                        "category": "analytics",
                    }
                )
                self.registered_services.append("collect_system_metrics")
                logger.debug("Registered metrics collection service")

            logger.info(
                f"Registered {len(self.registered_services)} monitoring services: {', '.join(self.registered_services)}"
            )

        except Exception as e:
            logger.error(f"Service registration failed: {e}")

    async def _setup_cross_plugin_communication(self) -> None:
        """[COMM] SHOWCASE: Setup cross-plugin communication channels.

        This demonstrates how to:
        - Set up message handlers for plugin-to-plugin communication
        - Register for event bus notifications
        - Create communication protocols
        """
        logger.debug("Setting up cross-plugin communication...")

        # [COMM] REGISTER MESSAGE HANDLER - For receiving messages from other plugins
        # In a real implementation, this would integrate with the event bus
        self.message_handlers = {
            "health_check_request": self._handle_health_check_request,
            "performance_data_request": self._handle_performance_data_request,
            "plugin_status_update": self._handle_plugin_status_update,
        }

        logger.debug("Cross-plugin communication setup complete!")

    async def _initialize_plugin_health_monitoring(self) -> None:
        """[DATA] SHOWCASE: Initialize comprehensive plugin health monitoring.

        This sets up monitoring for:
        - Plugin performance metrics
        - Communication patterns
        - Service usage statistics
        - System health indicators
        """
        logger.debug("Initializing plugin health monitoring...")

        # [TARGET] HEALTH MONITORING CONFIGURATION
        self.health_monitoring = {
            "enabled": True,
            "check_interval": self.config.get(
                "plugins.hook_monitoring.health_check_interval", 30
            ),
            "performance_threshold": self.config.get(
                "plugins.hook_monitoring.performance_threshold_ms", 100
            ),
            "memory_threshold": self.config.get(
                "plugins.hook_monitoring.memory_threshold_mb", 50
            ),
        }

        # [METRICS] METRICS COLLECTION SETUP
        self.metrics_collection: Dict[str, Any] = {
            "hook_performance": {},
            "plugin_communications": [],
            "service_usage": {},
            "error_patterns": [],
        }

        logger.debug(
            "Plugin health monitoring initialized - comprehensive monitoring active!"
        )

    async def register_hooks(self) -> None:
        """Register hook monitoring plugin hooks for all event types."""
        if not self.config.get("plugins.hook_monitoring.enabled", True):
            logger.info("Hook monitoring plugin disabled, not registering hooks")
            return

        # Register SYSTEM_READY hook to contribute stats
        await self.event_bus.register_hook(
            Hook(
                name="hook_monitoring_ready_stats",
                plugin_name=self.name,
                event_type=EventType.SYSTEM_READY,
                priority=HookPriority.SYSTEM.value,
                callback=self._add_ready_stats,
            )
        )

        logger.info(
            f"Hook Monitor: Registering {len(self.hooks)} monitoring hooks for system health tracking"
        )

        for hook in self.hooks:
            try:
                await self.event_bus.register_hook(hook)
                logger.debug(
                    f"Hook Monitor: Registered {hook.name} for "
                    f"{hook.event_type.value if isinstance(hook.event_type, EventType) else hook.event_type}"
                )
            except Exception as e:
                logger.error(f"Hook Monitor: Failed to register {hook.name}: {e}")

        logger.info("Hook Monitor: Registration completed - monitoring system active")

    def _get_developer_metrics_content(self) -> List[str]:
        """[TARGET] SHOWCASE: Get developer metrics with conditional display and throttling.

        This demonstrates:
        - Configuration-driven dashboard visibility
        - Update throttling for performance optimization
        - Multi-line dashboard with plugin interactions and service usage
        - Content caching with configurable refresh intervals
        """
        try:
            # Check if dashboard is enabled
            dashboard_enabled = self.config.get(
                "plugins.hook_monitoring.enable_health_dashboard", True
            )
            if not dashboard_enabled:
                return []  # Don't show anything when disabled

            # Check if plugin is enabled
            enabled = self.config.get("plugins.hook_monitoring.enabled", False)
            if not enabled:
                seg = AgnosterSegment()
                seg.add_neutral("Hooks: Off", "dark")
                return [seg.render()]

            # Check dashboard update interval (throttling)
            update_interval = self.config.get(
                "plugins.hook_monitoring.dashboard_update_interval", 10
            )
            now = datetime.datetime.now()
            seconds_since_update = (now - self.last_dashboard_update).total_seconds()

            if seconds_since_update < update_interval and self.cached_dashboard_content:
                return self.cached_dashboard_content  # Return cached content

            # Regenerate dashboard content
            self.last_dashboard_update = now

            # Build main metrics line
            seg = AgnosterSegment()

            # Calculate failure rate
            failure_rate: float = 0
            if self.hook_executions > 0:
                failure_rate = (
                    self.failed_hooks + self.timeout_hooks
                ) / self.hook_executions

            # Plugin discovery
            discovered_count = len(getattr(self, "discovered_plugins", {}))

            # Services
            registered_services = len(getattr(self, "registered_services", []))

            # Build agnoster bar
            seg.add_lime(f"Hooks: {self.hook_health_status}", "dark")
            seg.add_cyan(f"Exec: {self.hook_executions}", "dark")
            seg.add_lime(f"Plugins: {discovered_count}")

            if self.hook_executions > 0:
                seg.add_cyan(f"Fail: {failure_rate:.1%}")
            else:
                seg.add_cyan("Fail: N/A")

            seg.add_neutral(f"Services: {registered_services}", "mid")

            result = [seg.render()]

            # Add plugin interactions line if enabled
            show_interactions = self.config.get(
                "plugins.hook_monitoring.show_plugin_interactions", True
            )
            if show_interactions and hasattr(self, "cross_plugin_messages"):
                recent_messages = len(self.cross_plugin_messages)
                if recent_messages > 0:
                    int_seg = AgnosterSegment()
                    int_seg.add_neutral(f"Interactions: {recent_messages}", "dark")

                    # Show top communicators
                    senders: Dict[str, int] = {}
                    for msg in self.cross_plugin_messages:
                        sender = msg.get("sender", "unknown")
                        senders[sender] = senders.get(sender, 0) + 1

                    for sender, count in list(senders.items())[:3]:  # Top 3
                        int_seg.add_cyan(f"{sender}: {count}")

                    result.append(int_seg.render())

            # Add service usage line if enabled
            show_service_usage = self.config.get(
                "plugins.hook_monitoring.show_service_usage", True
            )
            if show_service_usage and hasattr(self, "service_usage_stats"):
                svc_seg = AgnosterSegment()

                total_calls = sum(s["calls"] for s in self.service_usage_stats.values())
                svc_seg.add_neutral(f"Service Calls: {total_calls}", "dark")

                for service_name, stats in self.service_usage_stats.items():
                    if stats["calls"] > 0:
                        # Shorten service names for display
                        short_name = (
                            service_name.replace("monitor_", "")
                            .replace("check_plugin_", "")
                            .replace("collect_system_", "")
                        )
                        svc_seg.add_lime(f"{short_name}: {stats['calls']}")

                if total_calls > 0:  # Only show if there are service calls
                    result.append(svc_seg.render())

            # Cache the result
            self.cached_dashboard_content = result
            return result

        except Exception as e:
            logger.error(f"Error getting developer metrics content: {e}")
            seg = AgnosterSegment()
            seg.add_neutral("Hooks: Error", "dark")
            return [seg.render()]

    async def _add_ready_stats(self, data: dict, event: Event) -> dict:
        """Add hook monitoring stats to ready message collector.

        Args:
            data: Event data dict containing collector
            event: SYSTEM_READY event

        Returns:
            Unmodified data dict
        """
        collector = data.get("collector")
        if not collector:
            return data

        try:
            # Add discovered plugins count
            discovered_count = len(self.discovered_plugins)
            if discovered_count > 0:
                collector.add(
                    category="plugins",
                    count=discovered_count,
                    label="discovered",
                    priority=650,
                    source=self.name,
                )

            # Add registered services count
            registered_services = len(self.registered_services)
            if registered_services > 0:
                collector.add(
                    category="services",
                    count=registered_services,
                    label="registered",
                    priority=640,
                    source=self.name,
                )

            logger.debug(
                f"Hook monitoring contributed {discovered_count} plugins, "
                f"{registered_services} services to ready message"
            )

        except Exception as e:
            logger.warning(f"Failed to add hook monitoring ready stats: {e}")

        return data

    async def shutdown(self) -> None:
        """Shutdown the hook monitoring plugin with proper task cleanup."""
        logger.info(
            f"Hook Monitor: Shutting down - processed {self.hook_executions} events, {self.failed_hooks} failures"
        )

        # Cancel discovery task if running
        if self.discovery_task and not self.discovery_task.done():
            self.discovery_task.cancel()
            try:
                await asyncio.wait_for(self.discovery_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug("Discovery task cancelled during shutdown")
            except Exception as e:
                logger.warning(f"Error cancelling discovery task: {e}")

        # Cancel metrics collection task if running
        if self.metrics_collection_task and not self.metrics_collection_task.done():
            self.metrics_collection_task.cancel()
            try:
                await asyncio.wait_for(self.metrics_collection_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug("Metrics collection task cancelled during shutdown")
            except Exception as e:
                logger.warning(f"Error cancelling metrics collection task: {e}")

        self._log_final_report()
        # Only show shutdown message if not in pipe mode
        # if not getattr(self.renderer, 'pipe_mode', False):
        #     print("\rHook Monitor shut down")

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """[TOOL] SHOWCASE: Comprehensive configuration for hook monitoring plugin.

        This configuration demonstrates all available plugin ecosystem features:
        - Basic monitoring settings
        - Plugin discovery configuration
        - Service registration settings
        - Cross-plugin communication options
        - Health monitoring parameters
        """
        return {
            "plugins": {
                "hook_monitoring": {
                    # [DATA] BASIC MONITORING CONFIGURATION
                    "enabled": True,
                    "debug_logging": True,
                    "show_status": True,
                    "hook_timeout": 5,
                    "log_all_events": True,
                    "log_event_data": False,
                    "log_performance": True,
                    "log_failures_only": False,
                    "performance_threshold_ms": 100,
                    "max_error_log_size": 50,
                    # [FIND] PLUGIN DISCOVERY CONFIGURATION - Showcase feature
                    "enable_plugin_discovery": True,
                    "discovery_interval": 30,  # seconds
                    "auto_analyze_capabilities": True,
                    # [TOOL] SERVICE REGISTRATION CONFIGURATION - Showcase feature
                    "enable_service_registration": True,
                    "register_performance_service": True,
                    "register_health_service": True,
                    "register_metrics_service": True,
                    # [COMM] CROSS-PLUGIN COMMUNICATION - Showcase feature
                    "enable_cross_plugin_communication": True,
                    "message_history_limit": 20,
                    "auto_respond_to_health_checks": True,
                    # [HEALTH] HEALTH MONITORING CONFIGURATION - Showcase feature
                    "health_check_interval": 30,  # seconds
                    "memory_threshold_mb": 50,
                    "performance_degradation_threshold": 0.15,
                    # [METRICS] METRICS COLLECTION - Showcase feature
                    "collect_plugin_metrics": True,
                    "metrics_retention_hours": 24,
                    "detailed_performance_tracking": True,
                    # [TARGET] DASHBOARD FEATURES - Showcase feature
                    "enable_health_dashboard": True,
                    "dashboard_update_interval": 10,  # seconds
                    "show_plugin_interactions": True,
                    "show_service_usage": True,
                }
            },
            "performance": {
                "failure_rate_warning": 0.05,
                "failure_rate_critical": 0.15,
            }
        }

    @staticmethod
    def get_startup_info(config) -> List[str]:
        """[INIT] SHOWCASE: Startup information displaying all ecosystem features.

        Args:
            config: Configuration manager instance.

        Returns:
            List of strings to display during startup.
        """
        return [
            "[SHOWCASE] HOOK MONITORING SHOWCASE PLUGIN",
            f"Monitor: {config.get('plugins.hook_monitoring.enabled')}",
            f"Plugin Discovery: {config.get('plugins.hook_monitoring.enable_plugin_discovery')}",
            f"Service Registration: {config.get('plugins.hook_monitoring.enable_service_registration')}",
            f"Cross-Plugin Comm: {config.get('plugins.hook_monitoring.enable_cross_plugin_communication')}",
            f"Health Dashboard: {config.get('plugins.hook_monitoring.enable_health_dashboard')}",
            f"Performance Threshold: {config.get('plugins.hook_monitoring.performance_threshold_ms')}ms",
            "[TARGET] Demonstrates ALL plugin ecosystem features!",
        ]

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        """Get configuration widgets for this plugin.

        Returns:
            Widget section definition for the config modal.
        """
        return {
            "title": "Hook Monitoring Plugin",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Debug Logging",
                    "config_path": "plugins.hook_monitoring.debug_logging",
                    "help": "Enable detailed debug logging for hooks",
                },
                {
                    "type": "checkbox",
                    "label": "Show Status",
                    "config_path": "plugins.hook_monitoring.show_status",
                    "help": "Display hook monitoring status",
                },
                {
                    "type": "slider",
                    "label": "Hook Timeout",
                    "config_path": "plugins.hook_monitoring.hook_timeout",
                    "min_value": 1,
                    "max_value": 30,
                    "step": 1,
                    "help": "Timeout for hook execution in seconds",
                },
                {
                    "type": "checkbox",
                    "label": "Log All Events",
                    "config_path": "plugins.hook_monitoring.log_all_events",
                    "help": "Log every hook event",
                },
                {
                    "type": "checkbox",
                    "label": "Log Event Data",
                    "config_path": "plugins.hook_monitoring.log_event_data",
                    "help": "Include event data in logs",
                },
                {
                    "type": "checkbox",
                    "label": "Log Performance",
                    "config_path": "plugins.hook_monitoring.log_performance",
                    "help": "Log performance metrics for hooks",
                },
                {
                    "type": "checkbox",
                    "label": "Log Failures Only",
                    "config_path": "plugins.hook_monitoring.log_failures_only",
                    "help": "Only log failed hook executions",
                },
                {
                    "type": "slider",
                    "label": "Performance Threshold",
                    "config_path": "plugins.hook_monitoring.performance_threshold_ms",
                    "min_value": 10,
                    "max_value": 1000,
                    "step": 10,
                    "help": "Performance warning threshold in milliseconds",
                },
                {
                    "type": "slider",
                    "label": "Max Error Log Size",
                    "config_path": "plugins.hook_monitoring.max_error_log_size",
                    "min_value": 10,
                    "max_value": 500,
                    "step": 10,
                    "help": "Maximum error log entries to keep",
                },
                {
                    "type": "checkbox",
                    "label": "Enable Plugin Discovery",
                    "config_path": "plugins.hook_monitoring.enable_plugin_discovery",
                    "help": "Automatically discover new plugins",
                },
                {
                    "type": "slider",
                    "label": "Discovery Interval",
                    "config_path": "plugins.hook_monitoring.discovery_interval",
                    "min_value": 5,
                    "max_value": 300,
                    "step": 5,
                    "help": "Plugin discovery interval in seconds",
                },
                {
                    "type": "checkbox",
                    "label": "Auto Analyze Capabilities",
                    "config_path": "plugins.hook_monitoring.auto_analyze_capabilities",
                    "help": "Automatically analyze plugin capabilities",
                },
                {
                    "type": "checkbox",
                    "label": "Enable Service Registration",
                    "config_path": "plugins.hook_monitoring.enable_service_registration",
                    "help": "Register monitoring services with event bus",
                },
                {
                    "type": "checkbox",
                    "label": "Register Performance Service",
                    "config_path": "plugins.hook_monitoring.register_performance_service",
                    "help": "Register performance monitoring service",
                },
                {
                    "type": "checkbox",
                    "label": "Register Health Service",
                    "config_path": "plugins.hook_monitoring.register_health_service",
                    "help": "Register health monitoring service",
                },
                {
                    "type": "checkbox",
                    "label": "Register Metrics Service",
                    "config_path": "plugins.hook_monitoring.register_metrics_service",
                    "help": "Register metrics collection service",
                },
                {
                    "type": "checkbox",
                    "label": "Enable Cross Plugin Communication",
                    "config_path": "plugins.hook_monitoring.enable_cross_plugin_communication",
                    "help": "Allow plugins to communicate with each other",
                },
                {
                    "type": "slider",
                    "label": "Message History Limit",
                    "config_path": "plugins.hook_monitoring.message_history_limit",
                    "min_value": 5,
                    "max_value": 100,
                    "step": 5,
                    "help": "Maximum message history entries",
                },
                {
                    "type": "checkbox",
                    "label": "Auto Respond to Health Checks",
                    "config_path": "plugins.hook_monitoring.auto_respond_to_health_checks",
                    "help": "Automatically respond to health check requests",
                },
                {
                    "type": "slider",
                    "label": "Health Check Interval",
                    "config_path": "plugins.hook_monitoring.health_check_interval",
                    "min_value": 10,
                    "max_value": 300,
                    "step": 10,
                    "help": "Health check interval in seconds",
                },
                {
                    "type": "slider",
                    "label": "Memory Threshold MB",
                    "config_path": "plugins.hook_monitoring.memory_threshold_mb",
                    "min_value": 10,
                    "max_value": 500,
                    "step": 10,
                    "help": "Memory usage warning threshold in MB",
                },
                {
                    "type": "slider",
                    "label": "Performance Degradation Threshold",
                    "config_path": "plugins.hook_monitoring.performance_degradation_threshold",
                    "min_value": 0.05,
                    "max_value": 0.5,
                    "step": 0.05,
                    "help": "Performance degradation warning threshold",
                },
                {
                    "type": "checkbox",
                    "label": "Collect Plugin Metrics",
                    "config_path": "plugins.hook_monitoring.collect_plugin_metrics",
                    "help": "Collect detailed metrics for all plugins",
                },
                {
                    "type": "slider",
                    "label": "Metrics Retention Hours",
                    "config_path": "plugins.hook_monitoring.metrics_retention_hours",
                    "min_value": 1,
                    "max_value": 168,
                    "step": 1,
                    "help": "How long to retain metrics data (hours)",
                },
                {
                    "type": "checkbox",
                    "label": "Detailed Performance Tracking",
                    "config_path": "plugins.hook_monitoring.detailed_performance_tracking",
                    "help": "Enable detailed performance tracking",
                },
                {
                    "type": "checkbox",
                    "label": "Enable Health Dashboard",
                    "config_path": "plugins.hook_monitoring.enable_health_dashboard",
                    "help": "Show health monitoring dashboard",
                },
                {
                    "type": "slider",
                    "label": "Dashboard Update Interval",
                    "config_path": "plugins.hook_monitoring.dashboard_update_interval",
                    "min_value": 1,
                    "max_value": 60,
                    "step": 1,
                    "help": "Dashboard refresh interval in seconds",
                },
                {
                    "type": "checkbox",
                    "label": "Show Plugin Interactions",
                    "config_path": "plugins.hook_monitoring.show_plugin_interactions",
                    "help": "Display plugin interaction information",
                },
                {
                    "type": "checkbox",
                    "label": "Show Service Usage",
                    "config_path": "plugins.hook_monitoring.show_service_usage",
                    "help": "Display service usage statistics",
                },
            ],
        }

    def _create_all_hooks(self) -> List[Hook]:
        """[TOOL] SHOWCASE: Create comprehensive hooks for monitoring all event types.

        This demonstrates complete system monitoring by hooking into all
        available event types to provide full visibility into system behavior.

        Returns:
            List of hooks covering all event types for comprehensive monitoring.
        """
        hooks = []
        timeout = self.config.get("plugins.hook_monitoring.hook_timeout", 5)

        # User input events
        hooks.extend(
            [
                Hook(
                    name="monitor_user_input_pre",
                    plugin_name=self.name,
                    event_type=EventType.USER_INPUT_PRE,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="monitor_user_input",
                    plugin_name=self.name,
                    event_type=EventType.USER_INPUT,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="monitor_user_input_post",
                    plugin_name=self.name,
                    event_type=EventType.USER_INPUT_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # Key press events
        hooks.extend(
            [
                Hook(
                    name="test_key_press_pre",
                    plugin_name=self.name,
                    event_type=EventType.KEY_PRESS_PRE,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_key_press",
                    plugin_name=self.name,
                    event_type=EventType.KEY_PRESS,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_key_press_post",
                    plugin_name=self.name,
                    event_type=EventType.KEY_PRESS_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # Paste events
        hooks.append(
            Hook(
                name="test_paste_detected",
                plugin_name=self.name,
                event_type=EventType.PASTE_DETECTED,
                priority=HookPriority.PREPROCESSING.value,
                callback=self._log_hook_execution,
                timeout=timeout,
            )
        )

        # LLM events
        hooks.extend(
            [
                Hook(
                    name="test_llm_request_pre",
                    plugin_name=self.name,
                    event_type=EventType.LLM_REQUEST_PRE,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_request",
                    plugin_name=self.name,
                    event_type=EventType.LLM_REQUEST,
                    priority=HookPriority.LLM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_request_post",
                    plugin_name=self.name,
                    event_type=EventType.LLM_REQUEST_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_response_pre",
                    plugin_name=self.name,
                    event_type=EventType.LLM_RESPONSE_PRE,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_response",
                    plugin_name=self.name,
                    event_type=EventType.LLM_RESPONSE,
                    priority=HookPriority.LLM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_response_post",
                    plugin_name=self.name,
                    event_type=EventType.LLM_RESPONSE_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_llm_thinking",
                    plugin_name=self.name,
                    event_type=EventType.LLM_THINKING,
                    priority=HookPriority.LLM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_cancel_request",
                    plugin_name=self.name,
                    event_type=EventType.CANCEL_REQUEST,
                    priority=HookPriority.LLM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # Tool events
        hooks.extend(
            [
                Hook(
                    name="test_tool_call_pre",
                    plugin_name=self.name,
                    event_type=EventType.TOOL_CALL_PRE,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_tool_call",
                    plugin_name=self.name,
                    event_type=EventType.TOOL_CALL,
                    priority=HookPriority.LLM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_tool_call_post",
                    plugin_name=self.name,
                    event_type=EventType.TOOL_CALL_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # System events
        hooks.extend(
            [
                Hook(
                    name="test_system_startup",
                    plugin_name=self.name,
                    event_type=EventType.SYSTEM_STARTUP,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_system_shutdown",
                    plugin_name=self.name,
                    event_type=EventType.SYSTEM_SHUTDOWN,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_render_frame",
                    plugin_name=self.name,
                    event_type=EventType.RENDER_FRAME,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # Input rendering events
        hooks.extend(
            [
                Hook(
                    name="test_input_render_pre",
                    plugin_name=self.name,
                    event_type=EventType.INPUT_RENDER_PRE,
                    priority=HookPriority.PREPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_input_render",
                    plugin_name=self.name,
                    event_type=EventType.INPUT_RENDER,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_input_render_post",
                    plugin_name=self.name,
                    event_type=EventType.INPUT_RENDER_POST,
                    priority=HookPriority.POSTPROCESSING.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        # Command menu events
        hooks.extend(
            [
                Hook(
                    name="test_command_menu_show",
                    plugin_name=self.name,
                    event_type=EventType.COMMAND_MENU_SHOW,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_command_menu_navigate",
                    plugin_name=self.name,
                    event_type=EventType.COMMAND_MENU_NAVIGATE,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_command_menu_select",
                    plugin_name=self.name,
                    event_type=EventType.COMMAND_MENU_SELECT,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
                Hook(
                    name="test_command_menu_hide",
                    plugin_name=self.name,
                    event_type=EventType.COMMAND_MENU_HIDE,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._log_hook_execution,
                    timeout=timeout,
                ),
            ]
        )

        logger.info(
            f"Hook Monitor: Created {len(hooks)} monitoring hooks for system health tracking"
        )
        return hooks

    # [TOOL] SERVICE IMPLEMENTATION METHODS - These are called by other plugins via SDK

    async def _provide_performance_monitoring(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """[TOOL] SHOWCASE: Performance monitoring service for other plugins.

        This service can be called by other plugins like:
        result = await sdk.execute_custom_tool("monitor_performance", {
            "plugin_name": "ModernInputPlugin",
            "metric_type": "execution_time"
        })
        """
        # Track service usage
        self.service_usage_stats["monitor_performance"]["calls"] += 1
        self.service_usage_stats["monitor_performance"][
            "last_called"
        ] = datetime.datetime.now()

        plugin_name = params.get("plugin_name", "all")
        metric_type = params.get("metric_type", "summary")

        logger.info(
            f"[DATA] Performance monitoring requested for {plugin_name}, metric: {metric_type}"
        )

        if plugin_name == "all":
            # Return performance data for all plugins
            return {
                "status": "success",
                "data": {
                    "total_executions": self.hook_executions,
                    "performance_metrics": self.hook_performance,
                    "health_status": self.hook_health_status,
                    "timestamp": datetime.datetime.now().isoformat(),
                },
            }
        else:
            # Return specific plugin performance
            plugin_perf = self.plugin_health_stats.get(plugin_name, {})
            return {
                "status": "success",
                "data": {
                    "plugin_name": plugin_name,
                    "performance": plugin_perf,
                    "timestamp": datetime.datetime.now().isoformat(),
                },
            }

    async def _provide_health_check(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """[HEALTH] SHOWCASE: Health check service for other plugins.

        Other plugins can call this to check system health:
        health = await sdk.execute_custom_tool("check_plugin_health", {
            "plugin_name": "system",
            "detailed": True
        })
        """
        # Track service usage
        self.service_usage_stats["check_plugin_health"]["calls"] += 1
        self.service_usage_stats["check_plugin_health"][
            "last_called"
        ] = datetime.datetime.now()

        plugin_name = params.get("plugin_name", "system")
        detailed = params.get("detailed", False)

        logger.info(
            f"[HEALTH] Health check requested for {plugin_name}, detailed: {detailed}"
        )

        if plugin_name == "system":
            # System-wide health check
            failure_rate: float = 0
            if self.hook_executions > 0:
                failure_rate = (
                    self.failed_hooks + self.timeout_hooks
                ) / self.hook_executions

            health_data = {
                "overall_health": self.hook_health_status,
                "failure_rate": failure_rate,
                "total_plugins": len(self.discovered_plugins),
                "active_plugins": len(
                    [
                        p
                        for p in self.plugin_health_stats.values()
                        if p.get("status") == "active"
                    ]
                ),
            }

            if detailed:
                health_data.update(
                    {
                        "plugin_details": self.plugin_health_stats,
                        "recent_errors": self.error_log[-5:] if self.error_log else [],
                        "performance_summary": self.hook_performance,
                    }
                )

            return {"status": "success", "health": health_data}
        else:
            # Specific plugin health check
            plugin_health = self.plugin_health_stats.get(
                plugin_name, {"status": "unknown"}
            )
            return {"status": "success", "health": plugin_health}

    async def _provide_metrics_collection(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """[METRICS] SHOWCASE: Metrics collection service for analytics.

        Other plugins can request comprehensive metrics:
        metrics = await sdk.execute_custom_tool("collect_system_metrics", {
            "time_range": "last_hour",
            "include_performance": True
        })
        """
        # Track service usage
        self.service_usage_stats["collect_system_metrics"]["calls"] += 1
        self.service_usage_stats["collect_system_metrics"][
            "last_called"
        ] = datetime.datetime.now()

        time_range = params.get("time_range", "current")
        include_performance = params.get("include_performance", True)

        logger.info(
            f"[METRICS] Metrics collection requested for {time_range}, performance: {include_performance}"
        )

        metrics = {
            "collection_timestamp": datetime.datetime.now().isoformat(),
            "time_range": time_range,
            "system_metrics": {
                "total_hook_executions": self.hook_executions,
                "failed_hooks": self.failed_hooks,
                "timeout_hooks": self.timeout_hooks,
                "health_status": self.hook_health_status,
                "discovered_plugins": len(self.discovered_plugins),
                "active_services": len(self.available_services),
            },
        }

        if include_performance:
            metrics["performance_metrics"] = self.hook_performance

        return {"status": "success", "metrics": metrics}

    # [COMM] CROSS-PLUGIN COMMUNICATION HANDLERS

    async def _handle_health_check_request(
        self, message: Dict[str, Any], sender: str
    ) -> Dict[str, Any]:
        """[COMM] SHOWCASE: Handle health check requests from other plugins with optional auto-response.

        This demonstrates:
        - Direct plugin-to-plugin communication
        - Automatic service response based on configuration
        - Message tracking and statistics
        """
        logger.info(f"[COMM] Health check request received from {sender}")

        # Record the communication
        self.cross_plugin_messages.append(
            {
                "timestamp": datetime.datetime.now(),
                "sender": sender,
                "message_type": "health_check_request",
                "data": message,
            }
        )

        # Keep message history limited
        if len(self.cross_plugin_messages) > self.message_history_limit:
            self.cross_plugin_messages = self.cross_plugin_messages[
                -self.message_history_limit :
            ]

        # Update sender's plugin stats
        if sender in self.plugin_health_stats:
            self.plugin_health_stats[sender]["message_count"] += 1
            self.plugin_health_stats[sender]["last_seen"] = datetime.datetime.now()

        # Auto-respond if configured
        auto_respond = self.config.get(
            "plugins.hook_monitoring.auto_respond_to_health_checks", True
        )
        if auto_respond:
            response = await self._provide_health_check(
                {
                    "plugin_name": message.get("plugin_name", "system"),
                    "detailed": message.get("detailed", False),
                }
            )
            logger.debug(f"[COMM] Auto-responded to {sender} with health check")
            return response

        return {"status": "acknowledged", "auto_respond": False}

    async def _handle_performance_data_request(
        self, message: Dict[str, Any], sender: str
    ) -> Dict[str, Any]:
        """[COMM] SHOWCASE: Handle performance data requests from other plugins."""
        logger.info(f"[DATA] Performance data request from {sender}")

        # This would typically send data back via event bus
        performance_data = {
            "hook_executions": self.hook_executions,
            "performance_metrics": self.hook_performance,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        return performance_data

    async def _handle_plugin_status_update(
        self, message: Dict[str, Any], sender: str
    ) -> None:
        """[COMM] SHOWCASE: Handle status updates from other plugins."""
        logger.info(f"[CLIP] Status update received from {sender}")

        # Update our records about the sender plugin
        if sender not in self.plugin_health_stats:
            self.plugin_health_stats[sender] = {}

        self.plugin_health_stats[sender].update(
            {
                "last_status_update": datetime.datetime.now(),
                "reported_status": message.get("status", "unknown"),
                "additional_data": message.get("data", {}),
            }
        )

    async def _log_hook_execution(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Log hook execution details.

        Args:
            data: Event data.
            event: Event object.

        Returns:
            Hook result with logging information.
        """
        self.hook_executions += 1
        self.last_hook_event = (
            event.type.value if isinstance(event.type, EventType) else event.type
        )

        start_time = time.time()

        # Update performance tracking
        event_type_val = (
            event.type.value if isinstance(event.type, EventType) else event.type
        )
        self._update_performance_metrics(event_type_val, start_time)

        # Determine if this should be logged based on configuration
        log_all = self.config.get("plugins.hook_monitoring.log_all_events", True)
        log_failures_only = self.config.get(
            "plugins.hook_monitoring.log_failures_only", False
        )

        if log_all and not log_failures_only:
            # Filter out excessive render and key press events to reduce log spam
            event_type_str = (
                event.type.value if isinstance(event.type, EventType) else event.type
            )
            if event_type_str not in [
                "input_render",
                "render",
                "key_press",
                "key_press_pre",
                "key_press_post",
            ]:
                logger.debug(
                    f"HOOK MONITOR [{self.hook_executions}]: "
                    f"{event.type.value if isinstance(event.type, EventType) else event.type} "
                    f"from {event.source}"
                )

        # Log event data if configured
        if self.config.get("plugins.hook_monitoring.log_event_data", False):
            data_keys = (
                list(data.keys()) if isinstance(data, dict) else type(data).__name__
            )
            logger.debug(f"Hook Monitor - Event data keys: {data_keys}")

        # Check for performance issues
        execution_time_ms = (time.time() - start_time) * 1000
        threshold_ms = self.config.get(
            "plugins.hook_monitoring.performance_threshold_ms", 100
        )

        if execution_time_ms > threshold_ms:
            logger.warning(
                f"Hook Monitor - SLOW HOOK: "
                f"{event.type.value if isinstance(event.type, EventType) else event.type} took "
                f"{execution_time_ms:.1f}ms (threshold: {threshold_ms}ms)"
            )

        # Update health status
        self._update_health_status()

        return {
            "status": "monitored",
            "execution_count": self.hook_executions,
            "event_type": (
                event.type.value if isinstance(event.type, EventType) else event.type
            ),
            "plugin_name": self.name,
            "execution_time_ms": execution_time_ms,
            "health_status": self.hook_health_status,
        }

    def _update_performance_metrics(self, event_type: str, start_time: float) -> None:
        """Update performance metrics with optional detailed tracking.

        Args:
            event_type: The event type being monitored.
            start_time: Start time of the hook execution.
        """
        execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds

        # Standard metrics (always tracked)
        if event_type not in self.hook_performance:
            self.hook_performance[event_type] = {
                "total_time": 0,
                "count": 0,
                "avg_time": 0,
                "max_time": 0,
                "min_time": float("inf"),
            }

        metrics = self.hook_performance[event_type]
        metrics["total_time"] += execution_time
        metrics["count"] += 1
        metrics["avg_time"] = metrics["total_time"] / metrics["count"]
        metrics["max_time"] = max(metrics["max_time"], execution_time)
        metrics["min_time"] = min(metrics["min_time"], execution_time)

        # Set baseline after 10 executions
        if metrics["count"] == 10 and event_type not in self.performance_baseline:
            self.performance_baseline[event_type] = metrics["avg_time"]
            logger.debug(
                f"Set performance baseline for {event_type}: {metrics['avg_time']:.2f}ms"
            )

        # Detailed tracking (optional)
        detailed_tracking = self.config.get(
            "plugins.hook_monitoring.detailed_performance_tracking", True
        )
        if detailed_tracking:
            if event_type not in self.detailed_metrics:
                self.detailed_metrics[event_type] = {
                    "execution_history": [],  # Last 100 executions
                    "percentile_95": 0,
                    "percentile_99": 0,
                }

            detail = self.detailed_metrics[event_type]
            detail["execution_history"].append(execution_time)

            # Keep only last 100 executions
            if len(detail["execution_history"]) > 100:
                detail["execution_history"] = detail["execution_history"][-100:]

            # Calculate percentiles (require at least 20 samples)
            if len(detail["execution_history"]) >= 20:
                sorted_times = sorted(detail["execution_history"])
                p95_idx = int(len(sorted_times) * 0.95)
                p99_idx = int(len(sorted_times) * 0.99)
                detail["percentile_95"] = sorted_times[p95_idx]
                detail["percentile_99"] = sorted_times[p99_idx]

    def _check_performance_degradation(self) -> bool:
        """[DATA] SHOWCASE: Check if performance has degraded beyond threshold.

        This demonstrates:
        - Performance baseline comparison
        - Configuration-driven degradation thresholds
        - Automatic performance degradation detection
        - Warning logging for degraded performance

        Returns:
            True if degradation detected, False otherwise
        """
        threshold = self.config.get(
            "plugins.hook_monitoring.performance_degradation_threshold", 0.15
        )
        degraded_events = []

        for event_type, baseline_time in self.performance_baseline.items():
            if event_type in self.hook_performance:
                current_time = self.hook_performance[event_type]["avg_time"]
                # Only check degradation if baseline is meaningful (> 0.1ms)
                # This avoids false positives from sub-millisecond execution times
                if baseline_time > 0.1:
                    degradation = (current_time - baseline_time) / baseline_time

                    if degradation > threshold:
                        degraded_events.append(
                            {
                                "event_type": event_type,
                                "baseline_ms": baseline_time,
                                "current_ms": current_time,
                                "degradation_pct": degradation * 100,
                            }
                        )

        if degraded_events:
            self.degradation_detected = True
            for event in degraded_events:
                logger.warning(
                    f"Performance degradation detected: {event['event_type']} "
                    f"baseline={event['baseline_ms']:.1f}ms current={event['current_ms']:.1f}ms "
                    f"({event['degradation_pct']:.1f}% slower)"
                )
            return True

        self.degradation_detected = False
        return False

    def _update_health_status(self) -> None:
        """Update health status including performance degradation check."""
        total_hooks = self.hook_executions
        if total_hooks == 0:
            self.hook_health_status = "Starting"
            return

        failure_rate = (self.failed_hooks + self.timeout_hooks) / total_hooks

        # Check performance degradation
        degraded = self._check_performance_degradation()

        # Determine health status (degradation affects status)
        if failure_rate == 0 and not degraded:
            self.hook_health_status = "Healthy"
        elif (
            failure_rate < self.config.get("performance.failure_rate_warning", 0.05)
            and not degraded
        ):
            self.hook_health_status = "Good"
        elif (
            failure_rate < self.config.get("performance.failure_rate_critical", 0.15)
            or degraded
        ):
            self.hook_health_status = "Warning"
        else:
            self.hook_health_status = "Critical"

    def _log_error(self, error_msg: str, event_type: str) -> None:
        """Log an error with rotation to prevent memory bloat.

        Args:
            error_msg: Error message to log.
            event_type: Event type where error occurred.
        """
        error_entry = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "event_type": event_type,
            "message": error_msg,
        }

        self.error_log.append(error_entry)

        # Rotate error log to prevent memory bloat
        max_size = self.config.get("plugins.hook_monitoring.max_error_log_size", 50)
        if len(self.error_log) > max_size:
            self.error_log = self.error_log[-max_size:]

    def _log_final_report(self) -> None:
        """Log a comprehensive final report of hook monitoring."""
        if not self.config.get("plugins.hook_monitoring.log_performance", True):
            return

        logger.info("=== HOOK MONITOR FINAL REPORT ===")
        logger.info(f"Total Hook Executions: {self.hook_executions}")
        logger.info(f"Failed Hooks: {self.failed_hooks}")
        logger.info(f"Timeout Hooks: {self.timeout_hooks}")
        logger.info(f"Final Health Status: {self.hook_health_status}")

        if self.hook_performance:
            logger.info("=== PERFORMANCE METRICS ===")
            for event_type, metrics in self.hook_performance.items():
                if metrics["count"] > 0:
                    logger.info(
                        f"{event_type}: {metrics['count']} executions, "
                        f"avg: {metrics['avg_time']:.1f}ms, "
                        f"max: {metrics['max_time']:.1f}ms, "
                        f"min: {metrics['min_time']:.1f}ms"
                    )

        if self.error_log:
            logger.info("=== RECENT ERRORS ===")
            for error in self.error_log[-10:]:  # Show last 10 errors
                logger.info(
                    f"[{error['timestamp']}] {error['event_type']}: {error['message']}"
                )

        logger.info("=== END HOOK MONITOR REPORT ===")

    def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get current monitoring statistics.

        Returns:
            Dictionary with comprehensive monitoring statistics.
        """
        return {
            "total_executions": self.hook_executions,
            "failed_hooks": self.failed_hooks,
            "timeout_hooks": self.timeout_hooks,
            "health_status": self.hook_health_status,
            "last_event": self.last_hook_event,
            "performance_metrics": self.hook_performance,
            "recent_errors": self.error_log[-5:] if self.error_log else [],
            "failure_rate": (self.failed_hooks + self.timeout_hooks)
            / max(1, self.hook_executions),
        }

    # [TARGET] PUBLIC API METHODS - For other plugins to use

    def get_plugin_ecosystem_dashboard(self) -> Dict[str, Any]:
        """[TARGET] SHOWCASE: Public API - Plugin Ecosystem Dashboard.

        This method can be called by other plugins to get a comprehensive
        view of the entire plugin ecosystem. Perfect demonstration of how
        plugins can provide services to each other!

        Example usage from another plugin:
        ```python
        # Get the hook monitoring plugin instance
        factory = self.get_factory()
        monitor = factory.get_instance("HookMonitoringPlugin")

        # Get ecosystem dashboard
        dashboard = monitor.get_plugin_ecosystem_dashboard()
        print(f"Total plugins: {dashboard['summary']['total_plugins']}")
        ```
        """
        # Calculate comprehensive ecosystem metrics
        total_plugins = len(getattr(self, "discovered_plugins", {}))
        active_plugins = len(
            [
                p
                for p in getattr(self, "plugin_health_stats", {}).values()
                if p.get("status") != "error"
            ]
        )

        failure_rate: float = 0
        if self.hook_executions > 0:
            failure_rate = (
                self.failed_hooks + self.timeout_hooks
            ) / self.hook_executions

        dashboard = {
            "[TARGET] PLUGIN ECOSYSTEM DASHBOARD": "Live Status",
            "summary": {
                "total_plugins": total_plugins,
                "active_plugins": active_plugins,
                "registered_services": len(getattr(self, "registered_services", [])),
                "system_health": self.hook_health_status,
                "overall_failure_rate": failure_rate,
                "last_updated": datetime.datetime.now().isoformat(),
            },
            "hook_monitoring": {
                "total_executions": self.hook_executions,
                "failed_hooks": self.failed_hooks,
                "timeout_hooks": self.timeout_hooks,
                "performance_metrics": dict(
                    list(self.hook_performance.items())[:5]
                ),  # Top 5
            },
            "plugin_discovery": {
                "discovery_enabled": self.plugin_discovery_enabled,
                "discovered_plugins": list(
                    getattr(self, "discovered_plugins", {}).keys()
                ),
                "plugin_capabilities": getattr(self, "plugin_health_stats", {}),
            },
            "service_registration": {
                "sdk_available": SDK_AVAILABLE,
                "services_enabled": self.service_registration_enabled,
                "available_services": getattr(self, "registered_services", []),
            },
            "cross_plugin_communication": {
                "communication_enabled": self.enable_cross_plugin_comm,
                "recent_messages": len(getattr(self, "cross_plugin_messages", [])),
                "message_history": getattr(self, "cross_plugin_messages", [])[
                    -3:
                ],  # Last 3
                "message_handlers": list(getattr(self, "message_handlers", {}).keys()),
            },
            "showcase_features": {
                "[FIND] Plugin Discovery": (
                    "[ok] Active" if self.plugin_discovery_enabled else "Disabled"
                ),
                "[TOOL] Service Registration": (
                    "[ok] Active"
                    if (self.service_registration_enabled and SDK_AVAILABLE)
                    else "Disabled"
                ),
                "[COMM] Cross-Plugin Comm": (
                    "[ok] Active" if self.enable_cross_plugin_comm else "Disabled"
                ),
                "[DATA] Performance Monitoring": "[ok] Active",
                "[HEALTH] Health Monitoring": "[ok] Active",
                "[METRICS] Metrics Collection": "[ok] Active",
            },
        }

        return dashboard

    async def send_demo_message_to_plugin(
        self, target_plugin: str, message_type: str = "demo"
    ) -> Dict[str, Any]:
        """[COMM] SHOWCASE: Send a demonstration message to another plugin.

        This showcases cross-plugin communication patterns.

        Args:
            target_plugin: Name of the plugin to send message to
            message_type: Type of message to send

        Returns:
            Result of the message sending attempt
        """
        logger.info(f"[COMM] DEMO: Sending {message_type} message to {target_plugin}")

        demo_message = {
            "sender": self.name,
            "message_type": message_type,
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {
                "demo": True,
                "system_health": self.hook_health_status,
                "hook_executions": self.hook_executions,
                "message": f"Hello from {self.name}! This demonstrates cross-plugin communication.",
            },
        }

        # Record the outgoing message
        if hasattr(self, "cross_plugin_messages"):
            self.cross_plugin_messages.append(
                {
                    "timestamp": datetime.datetime.now(),
                    "direction": "outgoing",
                    "target": target_plugin,
                    "message_type": message_type,
                    "data": demo_message,
                }
            )

        # In a real implementation, this would use the event bus:
        # await self.event_bus.send_message(target_plugin=target_plugin, data=demo_message)

        return {
            "status": "demo_sent",
            "target": target_plugin,
            "message": demo_message,
            "note": "In production, this would use the event bus for actual delivery",
        }
