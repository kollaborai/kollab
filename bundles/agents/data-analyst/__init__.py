"""Data Analyst Agent - Data analysis and visualization specialist for Python pandas, matplotlib, and SQL.

This agent specializes in:
  - Data manipulation and analysis with pandas
  - SQL query optimization and database operations
  - Data visualization with matplotlib and seaborn
  - Exploratory data analysis (EDA)
  - Statistical analysis and hypothesis testing

Skill names are assigned via ``agent.json`` and resolved from the Agent Skills
library (``bundles/skills/<name>/SKILL.md``). This list mirrors that file.
"""

from pathlib import Path

AGENT_DIR = Path(__file__).parent

# Mirrors ``skills`` in agent.json (bundled Agent Skills directory names).
ASSIGNED_SKILL_NAMES = [
    "data-visualization",
    "exploratory-data-analysis",
    "pandas-data-manipulation",
    "sql-query-optimization",
    "statistical-analysis",
]


def get_skills():
    """Return bundled skill directory names declared for this agent."""
    return list(ASSIGNED_SKILL_NAMES)


def get_agent_description():
    """Return agent description."""
    return "Data analysis and visualization specialist for Python pandas, matplotlib, and SQL"
