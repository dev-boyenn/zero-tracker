# ZDash Tracker Datapack (1.16.1)

Minimal always-on dragon tracker.

## Behavior
- Runs every tick.
- If an `ender_dragon` is loaded/alive, it appends one sample to storage.
- No chat logging.
- No enable/disable toggle.
- No cap.

## Install
1. Copy `datapack/zdash_tracker` into your world:
   `...\\.minecraft\\saves\\<world>\\datapacks\\zdash_tracker`
2. Run `/reload`.

## Storage Output
Storage key: `zdash:tracker`

Fields:
- `samples`: list of `{x,y,z,gt}`
  - `x,y,z` are scaled by 1000 (e.g. `-56000` => `-56.000`)
  - `gt` is `time query gametime`
- `cur`: latest sample object
- `meta.scale`: `1000`
