"""Data Analyst Agent - Data analysis and visualization specialist for Python pandas, matplotlib, and SQL.

This agent specializes in:
  - Data manipulation and analysis with pandas
  - SQL query optimization and database operations
  - Data visualization with matplotlib and seaborn
  - Exploratory data analysis (EDA)
  - Statistical analysis and hypothesis testing
"""

from pathlib import Path

AGENT_DIR = Path(__file__).parent

# Available skills for this agent
SKILLS = [
    "pandas-data-manipulation.md",
    "sql-query-optimization.md",
    "data-visualization.md",
    "exploratory-data-analysis.md",
    "statistical-analysis.md",
]


def get_skills():
    """Return list of available skill files."""
    return SKILLS


def get_agent_description():
    """Return agent description."""
    return "Data analysis and visualization specialist for Python pandas, matplotlib, and SQL"
