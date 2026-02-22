# Mark this stronghold navigation split as failed because player entered spectator.
scoreboard players set #sh_dim zdi 0
execute as @s if predicate zdash_tracker:in_nether run scoreboard players set #sh_dim zdi -1
execute as @s if predicate zdash_tracker:in_end run scoreboard players set #sh_dim zdi 1

execute store result storage zdash:tracker stronghold.spectator.gt long 1 run time query gametime
execute store result storage zdash:tracker stronghold.spectator.x int 1000 run data get entity @s Pos[0] 1
execute store result storage zdash:tracker stronghold.spectator.y int 1000 run data get entity @s Pos[1] 1
execute store result storage zdash:tracker stronghold.spectator.z int 1000 run data get entity @s Pos[2] 1
execute store result storage zdash:tracker stronghold.spectator.dim int 1 run scoreboard players get #sh_dim zdi
data modify storage zdash:tracker stronghold.spectator.detected set value 1b

# End the SH split immediately after spectator detection.
scoreboard players set #sh_active zdi 0
scoreboard players set #sh_tick zdi 0
data modify storage zdash:tracker stronghold.active set value 0b
