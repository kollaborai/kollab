# Merge flow

Ready PRs keep their heads and land together in one merge commit, instead of each PR folding main and
rerunning CI and review after every merge.

    python scripts/merge_flow.py                   # plan for every ready PR
    python scripts/merge_flow.py --prs 1450,1458   # plan for these, in this order
    python scripts/merge_flow.py --json plan.json  # also write the plan

A PR is ready when it is not a draft, its review decision is approved, and `ci/tests`,
`ci/standards-check` and `ci/security-scan` pass on its head.

What the plan does, all as local git objects (nothing is pushed or posted):

1. Checks every PR against main, and every pair of PRs that shares a file, with `git merge-tree`.
2. Marks the later PR of each clash for a fix; a PR that clashes with one needing a fix waits a round.
3. Pairs the remaining PRs by shared files (most similar first) into one combined commit.
4. Merges that commit onto main as the window commit and reports what lands now.

On the 78 PRs merged Nov 20 to 27, rebuilt on one base, the plan lands 50 in one merge and marks 28 for
fixes in 12 rounds, about 108 minutes in all; landing them one at a time took 13.6 hours of CI in the same
replay. Landing the window for real (#1473) is the next step.

## Landing a window (#1473)

    python scripts/merge_flow.py --open        # push the window commit, open its PR
    GH_TOKEN=<merging account> python scripts/merge_flow.py --land 1480

`--open` pushes the planned window commit to `merge-flow/window-<sha>` and opens a PR whose body records
the base and every landed PR's head. The window PR adds no change of its own; its required checks run once
on the combined result.

`--land` refuses to merge unless all of these hold: main has not moved since the plan, every listed PR
still has the head that was approved and is still ready, the window's required checks are green, and
rebuilding the window from those heads gives the same files. Then it approves and merges the window as one
merge commit, run as the merging account. A PR marked for a fix stays out and goes to its fixer as today.
