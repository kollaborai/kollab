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
