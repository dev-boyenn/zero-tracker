# Keep storage mirror of runtime state for easier parser diagnostics.
execute if score #sh_active zdi matches 1 run data modify storage zdash:tracker stronghold.active set value 1b
execute unless score #sh_active zdi matches 1 run data modify storage zdash:tracker stronghold.active set value 0b

# Detect world change (gametime wrapped to a lower value) and reset stronghold session state.
execute store result score #sh_now_gt zdi run time query gametime
execute if score #sh_now_gt zdi < #sh_prev_gt zdi run function zdash_tracker:stronghold/reset_session
scoreboard players operation #sh_prev_gt zdi = #sh_now_gt zdi

# Fallback Eye Spy detector by used-item stat (robust even when advancement reward callbacks fail).
scoreboard players set #eye_now zdi 0
execute if entity @a run execute store result score #eye_now zdi run scoreboard players get @a[limit=1] zueye
scoreboard players operation #eye_delta zdi = #eye_now zdi
scoreboard players operation #eye_delta zdi -= #eye_start zdi

# Primary trigger: vanilla follow_ender_eye advancement.
# Overworld-only to avoid false arming in The End on worlds where the advancement is already complete.
execute unless score #sh_active zdi matches 1 unless data storage zdash:tracker stronghold.eye_spy{logged:1b} as @a[limit=1,advancements={minecraft:story/follow_ender_eye=true}] if predicate zdash_tracker:in_overworld run function zdash_tracker:stronghold/on_eye_spy

# Backup triggers.
execute unless score #sh_active zdi matches 1 if score #eye_delta zdi matches 1.. unless data storage zdash:tracker stronghold.eye_spy{logged:1b} as @a[limit=1] if predicate zdash_tracker:in_overworld run function zdash_tracker:stronghold/on_eye_use_stat
execute unless score #sh_active zdi matches 1 unless data storage zdash:tracker stronghold.eye_spy{logged:1b} as @a[limit=1] if predicate zdash_tracker:in_overworld at @s run function zdash_tracker:stronghold/detect_entry_blocks

# If player enters spectator during active SH split, mark it as failed.
execute if score #sh_active zdi matches 1 unless data storage zdash:tracker stronghold.spectator{detected:1b} as @a[limit=1,gamemode=spectator] run function zdash_tracker:stronghold/mark_spectator_fail

# Stop the stronghold tracking window at first End entry.
execute if score #sh_active zdi matches 1 as @a[limit=1] if predicate zdash_tracker:in_end run function zdash_tracker:stronghold/on_end_enter

# Sample at fixed cadence while tracking is active.
execute if score #sh_active zdi matches 1 run scoreboard players add #sh_tick zdi 1
execute if score #sh_active zdi matches 1 if score #sh_tick zdi matches 5.. as @a[limit=1] run function zdash_tracker:stronghold/sample_player
execute if score #sh_active zdi matches 1 if score #sh_tick zdi matches 5.. run scoreboard players set #sh_tick zdi 0
