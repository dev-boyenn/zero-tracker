function zdash_tracker:update_stats
execute if score #dragon_died zdi matches 0 if block 0 63 0 minecraft:end_portal run function zdash_tracker:mark_dragon_died
execute if score #dragon_died zdi matches 0 if block 0 62 0 minecraft:end_portal run function zdash_tracker:mark_dragon_died
execute if score #dragon_died zdi matches 0 if block 0 64 0 minecraft:end_portal run function zdash_tracker:mark_dragon_died
scoreboard players set #active zdi 0
scoreboard players set #missing zdi 0
data modify storage zdash:tracker run.active set value 0b
execute store result storage zdash:tracker run.end_gt long 1 run time query gametime
tellraw @a [{"text":"[zdash] run ended","color":"yellow"}]
