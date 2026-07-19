# raw

This directory stores cumulative raw event-name text snapshots.

- Long-term layout: `raw/events/champions/*.txt` and `raw/events/maps/*.txt`
- State file: `raw/update-state.json`
- Input source: one update round of extracted event-name `.txt` files
- Check script: `scripts/check_raw_update.py`
- Update script: `scripts/update_raw_events.py`
- Round extraction script: `scripts/extract_raw_events_from_game.py`
- Workflow: `.github/workflows/update-raw-events.yml` consumes a raw round from an artifact or GAME manifest and writes only cumulative repository data.
- Legacy import script: `scripts/export_raw_events.py` still writes `raw/<version>/events` and should be treated as a compatibility input for migration/update work.
- Output rule: one `.txt` file per entity file under `champions/` or `maps/`
- File content: one event name per line
- Scope: Teamfight Tactics / TFTM event names are excluded at extraction/export boundaries because this repository only models events that remain in the League of Legends client.
- Version metadata belongs in `raw/update-state.json`, not in committed directory names.
