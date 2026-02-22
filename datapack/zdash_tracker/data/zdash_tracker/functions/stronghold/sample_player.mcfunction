scoreboard players set #sh_dim zdi 0
execute as @s if predicate zdash_tracker:in_nether run scoreboard players set #sh_dim zdi -1
execute as @s if predicate zdash_tracker:in_end run scoreboard players set #sh_dim zdi 1

execute store result storage zdash:tracker stronghold_cur.gt long 1 run time query gametime
execute store result storage zdash:tracker stronghold_cur.x int 1000 run data get entity @s Pos[0] 1
execute store result storage zdash:tracker stronghold_cur.y int 1000 run data get entity @s Pos[1] 1
execute store result storage zdash:tracker stronghold_cur.z int 1000 run data get entity @s Pos[2] 1
execute store result storage zdash:tracker stronghold_cur.dim int 1 run scoreboard players get #sh_dim zdi
data modify storage zdash:tracker stronghold.samples append from storage zdash:tracker stronghold_cur
