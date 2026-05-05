"""Model routing system for intelligent model selection.

Routes queries to appropriate models based on task requirements
and configured model capabilities.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModelRouter:
    """Intelligent model routing based on query analysis.

    Routes different types of queries to appropriate models
    for optimal performance and cost efficiency.
    """

    def __init__(self, config):
        """Initialize model router.

        Args:
            config: Configuration manager
        """
        self.config = config

        # Model configurations
        self.models = {
            "fast": config.get(
                "kollabor.llm.models.fast",
                {
                    "name": "qwen/qwen3-0.6b",
                    "api_url": "http://localhost:1234",
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            ),
            "reasoning": config.get(
                "kollabor.llm.models.reasoning",
                {
                    "name": "qwen/qwen3-4b",
                    "api_url": "http://localhost:1234",
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
            ),
            "coding": config.get(
                "kollabor.llm.models.coding",
                {
                    "name": "qwen/qwen3-4b",
                    "api_url": "http://localhost:1234",
                    "temperature": 0.5,
                    "max_tokens": 3000,
                },
            ),
            "documentation": config.get(
                "kollabor.llm.models.documentation",
                {
                    "name": "qwen/qwen3-4b",
                    "api_url": "http://localhost:1234",
                    "temperature": 0.6,
                    "max_tokens": 2000,
                },
            ),
        }

        # Default model
        self.default_model = "reasoning"

        logger.info(f"Model router initialized with {len(self.models)} model types")

    def analyze_query(self, query: str) -> str:
        """Analyze query to determine appropriate model type.

        Args:
            query: User query to analyze

        Returns:
            Model type to use
        """
        query_lower = query.lower()

        # Quick responses
        if len(query) < 50 and "?" in query:
            if any(word in query_lower for word in ["what", "when", "where", "who"]):
                return "fast"

        # Coding tasks
        if any(
            word in query_lower
            for word in ["code", "function", "class", "debug", "implement", "fix"]
        ):
            return "coding"

        # Documentation tasks
        if any(
            word in query_lower
            for word in ["document", "explain", "describe", "readme", "comment"]
        ):
            return "documentation"

        # Complex reasoning
        if any(
            word in query_lower
            for word in ["analyze", "compare", "evaluate", "design", "architect"]
        ):
            return "reasoning"

        # Short greetings or simple responses
        if len(query) < 20:
            return "fast"

        # Default to reasoning for everything else
        return str(self.default_model)

    def get_model_config(self, model_type: Optional[str] = None) -> Dict[str, Any]:
        """Get configuration for a specific model type.

        Args:
            model_type: Type of model (fast, reasoning, coding, documentation)

        Returns:
            Model configuration dictionary
        """
        if model_type is None:
            model_type = self.default_model

        if model_type not in self.models:
            logger.warning(f"Unknown model type: {model_type}, using default")
            model_type = self.default_model

        return dict(self.models[model_type])

    def route_query(
        self, query: str, force_model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Route a query to the appropriate model.

        Args:
            query: User query to route
            force_model: Optional model type to force

        Returns:
            Routing decision with model configuration
        """
        # Allow forcing a specific model
        if force_model and force_model in self.models:
            model_type = force_model
            reason = "forced"
        else:
            model_type = self.analyze_query(query)
            reason = "analyzed"

        model_config = self.get_model_config(model_type)

        routing_decision = {
            "model_type": model_type,
            "model_config": model_config,
            "reason": reason,
            "query_length": len(query),
            "query_complexity": self._assess_complexity(query),
        }

        logger.info(f"Routed query to {model_type} model ({reason})")
        return routing_decision

    def _assess_complexity(self, query: str) -> str:
        """Assess query complexity.

        Args:
            query: Query to assess

        Returns:
            Complexity level (simple, moderate, complex)
        """
        # Simple heuristics for complexity
        word_count = len(query.split())
        has_code = "```" in query
        has_multiple_questions = query.count("?") > 1

        if word_count < 10 and not has_code:
            return "simple"
        elif word_count > 50 or has_code or has_multiple_questions:
            return "complex"
        else:
            return "moderate"

    def update_model_config(self, model_type: str, config: Dict[str, Any]) -> bool:
        """Update configuration for a model type.

        Args:
            model_type: Type of model to update
            config: New configuration

        Returns:
            True if update successful
        """
        if model_type not in self.models:
            logger.warning(f"Creating new model type: {model_type}")

        self.models[model_type] = config
        logger.info(f"Updated configuration for {model_type} model")
        return True

    def list_available_models(self) -> List[Dict[str, Any]]:
        """List all available model configurations.

        Returns:
            List of model configurations
        """
        models = []
        for model_type, config in self.models.items():
            models.append(
                {
                    "type": model_type,
                    "name": config.get("name", "unknown"),
                    "api_url": config.get("api_url", "unknown"),
                    "is_default": model_type == self.default_model,
                }
            )
        return models

    def set_default_model(self, model_type: str) -> bool:
        """Set the default model type.

        Args:
            model_type: Model type to set as default

        Returns:
            True if successful
        """
        if model_type not in self.models:
            logger.error(f"Cannot set unknown model type as default: {model_type}")
            return False

        self.default_model = model_type
        logger.info(f"Set default model to: {model_type}")
        return True
