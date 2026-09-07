# Planning & Plan.md Execution Standards

The Syte Autonomous Agent follows strict planning and verification standards for every non-trivial task.

## Mandatory `plan.md` Protocol

1. **Root Location**: Always write `plan.md` directly into the project root directory using `syte_create_plan` and `syte_write_file`.
2. **Structure of `plan.md`**:
   - Title & Goal description
   - Architectural Overview & Dependencies
   - Detailed Phase / Step Breakdown with checkable boxes:
     `- [ ] Step 1: Description`
     `- [ ] Step 2: Description`
   - Verification Strategy (automated tests, build command, preview test)
3. **Approval & Countdown Gate**:
   - When `syte_create_plan` is called, an interactive approval card is rendered for the user.
   - The card features:
     - `Accept & Execute` (Immediate start)
     - 10-Second Auto-Start Countdown (Starts automatically unless paused)
     - `Pause / Revise Plan` (Allows user to inspect or modify requirements)
4. **Live Execution Tracking**:
   - As each step commences, call `syte_update_plan_step` with `status: "in_progress"`.
   - Update `plan.md` on disk with `- [x]` as steps complete.
   - Run verification (syntax check, build check) before marking complete.
   - Never leave uncompleted steps without testing.
