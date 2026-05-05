# Interactive Status Widgets - Comprehensive Test Plan

**Version:** 1.0
**Date:** 2026-01-19
**Status:** Testing in Progress

## Overview

This document provides comprehensive test cases for the Interactive Status Widgets feature based on the spec at `docs/specs/interactive-status-widgets-spec.md`.

---

## Test Categories

### 1. Navigation Mode Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| NAV-001 | Tab enters STATUS_FOCUS mode | 1. Start app in INPUT mode 2. Press Tab | Shows "STATUS" indicator, status area highlighted | [ ] |
| NAV-002 | Esc exits STATUS_FOCUS to INPUT | 1. Enter STATUS_FOCUS 2. Press Esc | Returns to INPUT mode, indicator gone | [ ] |
| NAV-003 | 'e' enters EDIT mode from STATUS_FOCUS | 1. Enter STATUS_FOCUS 2. Press 'e' | Shows "EDIT" indicator, slots visible (+) | [ ] |
| NAV-004 | Esc exits EDIT to STATUS_FOCUS | 1. Enter EDIT mode 2. Press Esc | Returns to STATUS_FOCUS mode | [ ] |
| NAV-005 | Double Esc exits EDIT to INPUT | 1. Enter EDIT mode 2. Press Esc twice | Returns to INPUT mode | [ ] |
| NAV-006 | Arrow keys navigate widgets | 1. Enter STATUS_FOCUS 2. Press arrows | Selection moves between widgets/rows | [ ] |
| NAV-007 | Home jumps to first widget | 1. Navigate to middle 2. Press Home | Selection at first widget | [ ] |
| NAV-008 | End jumps to last widget | 1. Navigate to middle 2. Press End | Selection at last widget | [ ] |

### 2. Edit Mode - Add Widget Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| ADD-001 | Add widget to row 1 at slot 0 | 1. Enter EDIT mode 2. Go to row 1 slot 0 3. Press Enter 4. Select widget | Widget added at beginning of row 1 | [ ] |
| ADD-002 | Add widget to row 1 at slot 1 | 1. Enter EDIT mode 2. Go to row 1 slot 1 3. Press Enter 4. Select widget | Widget inserted between widgets | [ ] |
| ADD-003 | Add widget to row 2 | 1. Enter EDIT mode 2. Navigate to row 2 3. Press Enter on slot 4. Select widget | Widget added to row 2 | [ ] |
| ADD-004 | Add widget to row 3 (initially hidden) | 1. Enter EDIT mode 2. Navigate to row 3 3. Press Enter on slot 4. Select widget | Widget added to row 3, row becomes visible | [ ] |
| ADD-005 | Add widget to row 4 (empty) | 1. Enter EDIT mode 2. Navigate to row 4 3. Press Enter on slot 4. Select widget | Widget added to row 4 | [ ] |
| ADD-006 | Add widget to row 5 | 1. First add row 5 via 'A' 2. Navigate to row 5 3. Add widget | Widget added to row 5 | [ ] |
| ADD-007 | Add widget to row 6 | 1. First add row 6 via 'A' 2. Navigate to row 6 3. Add widget | Widget added to row 6 | [ ] |
| ADD-008 | Widget picker modal opens | 1. Press Enter on slot | Modal shows "Add Widget to Row X" | [ ] |
| ADD-009 | Widget picker Down arrow | 1. Open picker 2. Press Down | Selection moves down in list | [ ] |
| ADD-010 | Widget picker Up arrow | 1. Open picker 2. Press Up | Selection moves up in list (wraps) | [ ] |
| ADD-011 | Widget picker j/k vim keys | 1. Open picker 2. Press j/k | Selection moves like Down/Up | [ ] |
| ADD-012 | Widget picker Tab changes row | 1. Open picker 2. Press Tab | Target row cycles | [ ] |
| ADD-013 | Widget picker Esc cancels | 1. Open picker 2. Press Esc | Modal closes, no widget added | [ ] |
| ADD-014 | Widget picker Enter selects | 1. Open picker 2. Navigate 3. Press Enter | Selected widget added | [ ] |
| ADD-015 | Widget picker number keys | 1. Open picker 2. Press 1-9 | Quick selects widget N | [ ] |

### 3. Edit Mode - Delete Widget Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| DEL-001 | Delete widget from row 1 | 1. Enter EDIT mode 2. Navigate to widget on row 1 3. Press 'd' | Widget removed from row 1 | [ ] |
| DEL-002 | Delete widget from row 2 | 1. Enter EDIT mode 2. Navigate to widget on row 2 3. Press 'd' | Widget removed from row 2 | [ ] |
| DEL-003 | Delete all widgets from row | 1. Delete all widgets from a row | Row becomes empty but remains | [ ] |
| DEL-004 | Cannot delete from slot | 1. Select a slot (not widget) 2. Press 'd' | Nothing happens or error message | [ ] |

### 4. Edit Mode - Widget Color Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| CLR-001 | Toggle widget color | 1. Navigate to widget 2. Press 'c' | Color cycles: default->primary->accent->dark | [ ] |
| CLR-002 | Color persists after toggle | 1. Change color 2. Exit edit mode 3. Re-enter | Color preserved | [ ] |

### 5. Edit Mode - Row Management Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| ROW-001 | All 4 default rows shown in EDIT | 1. Enter EDIT mode | Rows 1-4 visible including hidden | [ ] |
| ROW-002 | Add new row with 'A' | 1. Press 'A' in EDIT mode | New row 5 added | [ ] |
| ROW-003 | Cannot exceed 6 rows | 1. Add rows until 6 2. Try to add more | Error or blocked | [ ] |
| ROW-004 | Row visibility toggle | 1. Empty row 2. Toggle visibility | Row hidden/shown | [ ] |

### 6. Widget Activation Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| ACT-001 | Activate profile widget | 1. Navigate to profile widget 2. Press Enter | /profile command executes | [ ] |
| ACT-002 | Activate model widget | 1. Navigate to model widget 2. Press Enter | /model command executes | [ ] |
| ACT-003 | Activate cwd widget | 1. Navigate to cwd widget 2. Press Enter | /cd command executes | [ ] |
| ACT-004 | Toggle widget cycles state | 1. Navigate to toggle widget 2. Press Enter/Space | State cycles (OFF->ON->AUTO) | [ ] |

### 7. Persistence Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| PER-001 | Layout persists after restart | 1. Add widgets 2. Exit app 3. Restart | Same layout restored | [ ] |
| PER-002 | Widget positions preserved | 1. Move widgets 2. Restart | Positions match | [ ] |
| PER-003 | Widget colors preserved | 1. Set colors 2. Restart | Colors match | [ ] |
| PER-004 | Hidden rows preserved | 1. Toggle visibility 2. Restart | Visibility matches | [ ] |

### 8. Visual Feedback Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| VIS-001 | Selection indicator shown | 1. Enter navigation mode | Selected widget/slot highlighted | [ ] |
| VIS-002 | Mode indicator accurate | 1. Change modes | Indicator shows INPUT/STATUS/EDIT | [ ] |
| VIS-003 | Slots shown with + symbol | 1. Enter EDIT mode | + symbols between widgets | [ ] |
| VIS-004 | Row numbers shown in EDIT | 1. Enter EDIT mode | (row N) label visible | [ ] |

### 9. Error Handling Tests

| Test ID | Description | Steps | Expected Result | Status |
|---------|-------------|-------|-----------------|--------|
| ERR-001 | Cannot navigate during LLM streaming | 1. Start LLM request 2. Press Tab | Navigation blocked or deferred | [ ] |
| ERR-002 | Widget picker handles empty list | 1. No widgets available | Graceful handling | [ ] |
| ERR-003 | Invalid row navigation | 1. Try to navigate past bounds | Selection clamped | [ ] |

---

## Test Execution Log

### Session 2026-01-19

| Time | Test ID | Result | Notes |
|------|---------|--------|-------|
| | | | |

---

## Known Issues Discovered

| Issue | Description | Status | Fix |
|-------|-------------|--------|-----|
| FIXED | move_selection() called with 4 args instead of 2 | Fixed | navigation_manager.py |
| FIXED | deactivate_widget() missing from navigation_state.py | Fixed | navigation_state.py |
| FIXED | Widget picker modal timeout too short (10ms) | Fixed | Changed to 100ms (0.1s) |
| FIXED | _clamp_selection_to_bounds loses slot type | Fixed | navigation_manager.py:1759 - now preserves SelectionType |
| FIXED | handle_remove_widget uses wrong row index | Fixed | navigation_manager.py:720 - now uses get_full_selection() |
| FIXED | Hidden row doesn't become visible on add widget | Fixed | layout_manager.py:349-351 - auto-visibility |

## Test Results Summary (2026-01-19)

### Automated Tests Passed
- verify_delete_widget_row_index_fix.sh: PASS - Delete key handled, no row errors
- verify_add_widget_to_hidden_row.sh: PASS - Row 4 becomes visible after add
- verify_persistence.sh: PASS - Layout persists across app restart

### Key Verifications
- Tab -> STATUS_FOCUS mode: PASS
- STATUS_FOCUS + 'e' -> EDIT mode: PASS
- Arrow key navigation: PASS (with fixed clamping)
- Widget picker modal: PASS (arrow keys work with 100ms timeout)
- Delete on slot: PASS (correctly returns "Cannot remove - slot selected")
- Persistence: PASS (widgets persist after restart)

---

## Test Commands

```bash
# Start test session
tmux new-session -s editmode-demo "python main.py"

# Enter navigation modes
Tab        # INPUT -> STATUS_FOCUS
e          # STATUS_FOCUS -> EDIT
Esc        # Exit one level

# Navigation
Up/Down    # Move between rows
Left/Right # Move between widgets/slots

# Widget operations
Enter      # Add widget (on slot) or activate widget
d          # Delete widget
c          # Toggle color

# Row operations
A          # Add new row
R          # Remove empty row

# Widget picker
Up/Down    # Navigate list
j/k        # Vim-style navigation
Enter      # Select widget
Esc        # Cancel
1-9        # Quick select by number
Tab        # Cycle target row
```

---

## Verification Checklist

- [ ] All navigation modes work correctly
- [ ] Add widget works for rows 1-6
- [ ] Delete widget works for all rows
- [ ] Widget picker modal navigates properly
- [ ] Persistence works across restarts
- [ ] Visual feedback is accurate
- [ ] Error handling is robust
- [ ] All spec requirements met
