# Ready Message Stats Test

## Purpose

This tmux test verifies that the ready message system correctly displays statistics from core components and plugins during application startup.

## What It Tests

The test validates:

1. **Ready Message Display** - Confirms "Ready!" message appears
2. **Stats Format** - Verifies stats are shown in parentheses
3. **System Prompt Modules** - Checks for modular system prompt count
4. **Hooks Count** - Verifies active hooks are counted
5. **Plugins Count** - Confirms loaded plugins are counted
6. **Status Views Count** - Checks for status view registration count
7. **Numeric Values** - Ensures counts are numeric (not errors)

## Running the Test

```bash
# From project root
./tests/tmux/test_ready_message_stats.sh
```

## Expected Output

```
========================================
Ready Message Stats Display Test
========================================

Test: Verify ready message displays stats from core and plugins
Session: test_ready_message_12345

[1/6] Creating tmux session...
[2/6] Starting Kollab...
[3/6] Waiting for initialization (5 seconds)...
[4/6] Capturing terminal output...

--- Captured Output ---
[kollab console header]
Ready! (24 system prompt modules, 85 hooks active, 15 plugins loaded, 8 status views available) Type your message and press Enter.
--- End Output ---

[5/6] Running verification tests...

Test 1: Verify 'Ready!' message appears
✓ PASS - Ready message found
Test 2: Verify stats are displayed
✓ PASS - Stats parentheses found
Test 3: Verify 'system prompt' stat present
✓ PASS - System prompt modules stat found
Test 4: Verify 'hooks' stat present
✓ PASS - Hooks stat found
Test 5: Verify 'plugins' stat present
✓ PASS - Plugins stat found
Test 6: Verify 'status views' stat present
✓ PASS - Status views stat found
Test 7: Verify numeric counts in stats
✓ PASS - Numeric counts found in ready message

[6/6] Sending quit command...

========================================
Test Summary
========================================
Tests passed: 7/7

✓ ALL TESTS PASSED

The ready message system is working correctly!
Stats are being collected and displayed from core and plugins.
```

## What Can Fail

### Complete Failure (0-2 tests pass)

If most tests fail, the ready message system is broken:

**Possible causes:**
- App crashes during startup
- LLM configuration error (blocks initialization)
- Missing ReadyMessageCollector import
- Event emission broken

**Debug:**
```bash
# Check application logs
tail -100 ~/.kollab/projects/*/logs/kollab.log

# Look for errors during initialization
grep -i "error\|fail" ~/.kollab/projects/*/logs/kollab.log | tail -20
```

### Partial Success (3-6 tests pass)

If "Ready!" appears but stats are incomplete:

**Possible causes:**
- Timing issue (tmux captured too early)
- Some stats collectors failing silently
- Plugins not fully initialized

**Debug:**
```bash
# Re-run with longer wait time
# Edit test_ready_message_stats.sh line 66:
sleep 10  # instead of sleep 5

# Check for specific stat collection errors
grep "ready stats" ~/.kollab/projects/*/logs/kollab.log
```

### Missing Specific Stats

**System prompt modules missing:**
- Check `~/.kollab/agents/default/system_prompt.md` exists
- Verify `<trender type="include">` tags are present
- See: `kollabor/utils/prompt_renderer.py`

**Hooks count missing:**
- Verify hooks are being registered
- Check `event_bus.hook_registry.get_all_hooks()` works
- See: `kollabor/events/registry.py`

**Plugins count missing:**
- Check plugins are discovered
- Verify `plugin_registry.list_plugins()` returns data
- See: `kollabor/plugins/discovery.py`

**Status views missing:**
- Check status renderer initialized
- Verify `status_renderer.view_registry.views` populated
- See: `kollabor/io/status/renderer.py`

## Integration with CI/CD

This test can be integrated into automated testing:

```bash
# In CI script
if ! ./tests/tmux/test_ready_message_stats.sh; then
    echo "Ready message test failed - check initialization"
    exit 1
fi
```

## Maintenance

When the ready message system changes:

1. **Add new stats** - Update test to check for new stat categories
2. **Change format** - Update grep patterns to match new format
3. **Change timing** - Adjust `sleep 5` if initialization takes longer

## Related Files

- **Implementation:**
  - `kollabor/events/ready_message.py` - ReadyMessageCollector
  - `kollabor/application.py` - _display_ready_message()
  - `kollabor/events/models.py` - EventType.SYSTEM_READY

- **Documentation:**
  - `docs/features/ready-message-system.md` - Full system docs
  - `READY_MESSAGE_SYSTEM.md` - Implementation summary

- **Plugin Example:**
  - `plugins/hook_monitoring_plugin.py` - _add_ready_stats()

## Test Output Files

Temporary files created during test:

- `/tmp/kollabor_ready_test_<pid>.txt` - Captured terminal output
- Cleaned up automatically on test exit

## Manual Testing

To manually verify the ready message:

```bash
# Start Kollab normally
python main.py

# Look for ready message with stats after the startup header
# Should see: Ready! (24 system prompt modules, ...) Type your message and press Enter.
```

## Troubleshooting

**Test hangs:**
- Kill session: `tmux kill-session -t test_ready_message_*`
- Check for zombie processes: `ps aux | grep python | grep main.py`

**No output captured:**
- Increase wait time in script
- Check if app is actually starting: `tmux attach -t test_ready_message_*`

**Stats show as errors:**
- Check logs for exceptions during `_add_core_ready_stats()`
- Verify all required components are initialized

## Success Criteria

For the test to pass completely (7/7):

1. Application starts without errors
2. All core components initialize successfully
3. Plugins register and initialize
4. SYSTEM_READY event is emitted and processed
5. ReadyMessageCollector receives stats from core and plugins
6. Stats are formatted and displayed in ready message
7. Ready message appears in terminal output

## Regression Prevention

This test prevents regressions in:

- Ready message display timing
- Stats collection from core components
- Plugin contribution to ready message
- Event system (SYSTEM_READY event)
- Initialization order (must complete before ready message)

Run this test after changes to:
- Application startup sequence
- Plugin initialization
- Event system
- Ready message collection/formatting
