# Self-heal setup in case load function didn't run in this world.
function zdash_tracker:ensure

# Start run on first live dragon tick.
execute if entity @e[type=minecraft:ender_dragon,limit=1] unless score #active zdi matches 1 run function zdash_tracker:start_run

# Sample only while run is active and dragon is loaded/alive.
execute if score #active zdi matches 1 if entity @e[type=minecraft:ender_dragon,limit=1] run function zdash_tracker:sample

# Keep run deltas fresh every tick while active.
execute if score #active zdi matches 1 run function zdash_tracker:update_stats

# Capture End-entry column height once per run.
execute if score #active zdi matches 1 if score #end_entry_logged zdi matches 0 in minecraft:the_end if entity @a run function zdash_tracker:record_end_entry

# Mark dragon death from HP. This works even if death animation is skipped.
execute if entity @e[type=minecraft:ender_dragon,limit=1] store result score #dragon_hp zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Health 100
execute if score #dragon_hp zdi matches ..0 if score #dragon_died zdi matches 0 run function zdash_tracker:mark_dragon_died
execute if entity @e[type=minecraft:ender_dragon,nbt={DragonPhase:9},limit=1] if score #dragon_died zdi matches 0 run function zdash_tracker:mark_dragon_died

# Detect flyaway only while dragon is alive and loaded.
execute if score #active zdi matches 1 if score #dragon_died zdi matches 0 if entity @e[type=minecraft:ender_dragon,limit=1] run function zdash_tracker:flyaway_update

# Track dragon absence to avoid ending too early before stats settle.
execute if score #active zdi matches 1 if entity @e[type=minecraft:ender_dragon,limit=1] run scoreboard players set #missing zdi 0
execute if score #active zdi matches 1 unless entity @e[type=minecraft:ender_dragon,limit=1] run scoreboard players add #missing zdi 1

# End run after short grace period with no dragon.
execute if score #active zdi matches 1 if score #missing zdi matches 40.. run function zdash_tracker:end_run
