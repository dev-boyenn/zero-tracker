# Sample dragon position (scaled by 1000) and game tick.
execute store result storage zdash:tracker cur.x int 1000 run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[0] 1
execute store result storage zdash:tracker cur.y int 1000 run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[1] 1
execute store result storage zdash:tracker cur.z int 1000 run data get entity @e[type=minecraft:ender_dragon,limit=1] Pos[2] 1
execute store result storage zdash:tracker cur.yaw int 1000 run data get entity @e[type=minecraft:ender_dragon,limit=1] Rotation[0] 1
execute store result storage zdash:tracker cur.pitch int 1000 run data get entity @e[type=minecraft:ender_dragon,limit=1] Rotation[1] 1
execute store result storage zdash:tracker cur.gt long 1 run time query gametime
data modify storage zdash:tracker samples append from storage zdash:tracker cur
function zdash_tracker:update_stats
