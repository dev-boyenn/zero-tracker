# Called while tracking is active and the player is detected in The End.
scoreboard players set #sh_active zdi 0
data modify storage zdash:tracker stronghold.active set value 0b

scoreboard players set #sh_dim zdi 1
execute store result storage zdash:tracker stronghold.end_enter.gt long 1 run time query gametime
execute store result storage zdash:tracker stronghold.end_enter.x int 1000 run data get entity @s Pos[0] 1
execute store result storage zdash:tracker stronghold.end_enter.y int 1000 run data get entity @s Pos[1] 1
execute store result storage zdash:tracker stronghold.end_enter.z int 1000 run data get entity @s Pos[2] 1
execute store result storage zdash:tracker stronghold.end_enter.dim int 1 run scoreboard players get #sh_dim zdi
data modify storage zdash:tracker stronghold.end_enter.logged set value 1b
