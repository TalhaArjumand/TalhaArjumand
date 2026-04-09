# Profile Metrics Setup

This repository includes a self-hosted profile metrics pipeline so the README does not depend on public third-party widgets for streak and contribution visuals.

## What It Does

- queries GitHub contribution data with your own authenticated token
- generates local SVG assets under `assets/profile-metrics/`
- updates the profile README using repository-hosted images
- refreshes daily through GitHub Actions

## Required Secret

Add this repository secret in `TalhaArjumand/TalhaArjumand`:

- `PROFILE_METRICS_TOKEN`

Recommended token type:

- classic personal access token with `read:user` and `repo`

Reason:

- `read:user` is needed for contribution data access
- `repo` is the safe choice if private repository contribution activity must be reflected

## After Adding the Secret

1. Open the `Update Profile Metrics` workflow in Actions.
2. Click `Run workflow`.
3. Wait for the workflow to finish.
4. Confirm these files were updated:
   - `assets/profile-metrics/streak.svg`
   - `assets/profile-metrics/summary.svg`
   - `assets/profile-metrics/activity.svg`
   - `assets/profile-metrics/summary.json`

## Notes

- GitHub's native contribution calendar remains the authoritative source.
- The generated assets are designed to match your profile theme and reduce external cache drift.
- If private contributions are enabled on your profile, the workflow-generated assets are much more reliable than public streak widgets, but they still depend on the contribution data exposed to your token.
