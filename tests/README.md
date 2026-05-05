# Test Suite

This directory contains the test suite for Kollab.

## Running Tests

### Run All Tests
```bash
python tests/run_tests.py
```

### Run Individual Test Files
```bash
python -m unittest tests.test_llm_plugin
python -m unittest tests.test_config_manager
python -m unittest tests.test_plugin_registry
```

### Run Specific Test Cases
```bash
python -m unittest tests.test_llm_plugin.TestLLMPlugin.test_thinking_tags_removal
```

## Test Coverage

### LLM Plugin Tests (`test_llm_plugin.py`)
- ✅ `<think>...</think>` tag removal
- ✅ `<final_response>...</final_response>` tag extraction
- ✅ Combined tag processing
- ✅ Responses without special tags

### Config Manager Tests (`test_config_manager.py`)
- ✅ Default configuration creation
- ✅ Dot notation config access (`config.get("section.key")`)
- ✅ Configuration modification and persistence
- ✅ Config file merging with existing files

### Plugin Registry Tests (`test_plugin_registry.py`)
- ✅ Plugin discovery from filesystem
- ✅ Plugin loading and class registration
- ✅ Plugin configuration merging

## Adding New Tests

1. Create a new test file following the pattern `test_*.py`
2. Import the module you want to test
3. Create test classes inheriting from `unittest.TestCase`
4. Write test methods starting with `test_`
5. Run the test suite to verify your tests pass

## Test Structure

```
tests/
├── __init__.py              # Package initialization
├── README.md               # This file
├── run_tests.py            # Test runner
├── test_llm_plugin.py      # LLM plugin functionality tests
├── test_config_manager.py  # Configuration system tests
└── test_plugin_registry.py # Plugin discovery and loading tests
```