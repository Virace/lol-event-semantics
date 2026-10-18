# raw

This directory stores cumulative raw event-name text snapshots.

- Long-term layout: `raw/events/champions/*.txt` and `raw/events/maps/*.txt`
- State file: `raw/update-state.json`
- Input source: one update round of extracted event-name `.txt` files
- Check script: `scripts/check_raw_update.py`
- Update script: `scripts/update_raw_events.py`
- Live collection script: `scripts/collect_raw_events.py`, run directly on a local or cloud computer; explicit WAD/BIN requests remain supported by `scripts/extract_raw_events_from_game.py`.
- Disabled workflow template: `.github/workflows/update-raw-events.yml.disabled` preserves the former update definition. All Actions are disabled; re-enabling requires a successful full local validation and a formal release, followed by an explicit decision.
- Legacy import script: `scripts/export_raw_events.py` still writes `raw/<version>/events` and should be treated as a compatibility input for migration/update work.
- Output rule: one `.txt` file per entity file under `champions/` or `maps/`
- File content: one event name per line
- Scope: Teamfight Tactics / TFTM event names are excluded at extraction/export boundaries because this repository only models events that remain in the League of Legends client.
- Version metadata belongs in `raw/update-state.json`, not in committed directory names.
