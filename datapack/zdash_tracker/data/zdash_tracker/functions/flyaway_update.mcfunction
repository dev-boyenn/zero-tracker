# Update dragon XYZ for flyaway checks.
execute store result score #drx zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[0] 1
execute store result score #dry zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[1] 1
execute store result score #drz zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[2] 1

# Track time in End from entry marker (record_end_entry).
execute store result score #fly_now_gt zdi run time query gametime
execute store result score #fly_entry_gt zdi run data get storage zdash:tracker run.end_entry.gt 1
scoreboard players operation #fly_since_end zdi = #fly_now_gt zdi
scoreboard players operation #fly_since_end zdi -= #fly_entry_gt zdi

# Arm flyaway monitor once dragon comes within 10 horizontal blocks of a node.
execute if score #fly_mon zdi matches 0 if score #fly_hit zdi matches 0 if score #end_entry_logged zdi matches 1 if score #fly_since_end zdi matches 160.. run function zdash_tracker:flyaway_try_arm

# Once armed, detect flyaway if dragon goes beyond 15 horizontal blocks.
execute if score #fly_mon zdi matches 1 if score #fly_hit zdi matches 0 run function zdash_tracker:flyaway_check_active

# Persist armed flag to storage for parser visibility.
execute if score #fly_mon zdi matches 1 run data modify storage zdash:tracker run.flyaway.armed set value 1b
execute if score #fly_mon zdi matches 0 run data modify storage zdash:tracker run.flyaway.armed set value 0b
