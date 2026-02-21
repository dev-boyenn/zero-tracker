# Starts stronghold navigation sampling window (Eye Spy -> End enter).
scoreboard players set #sh_active zdi 1
scoreboard players set #sh_tick zdi 0

data modify storage zdash:tracker stronghold.active set value 1b
data modify storage zdash:tracker stronghold.eye_spy set value {logged:0b,gt:0L,x:0,y:0,z:0,dim:0}
data modify storage zdash:tracker stronghold.end_enter set value {logged:0b,gt:0L,x:0,y:0,z:0,dim:0}
data modify storage zdash:tracker stronghold.samples set value []

scoreboard players set #sh_dim zdi 0

execute store result storage zdash:tracker stronghold.eye_spy.gt long 1 run time query gametime
execute store result storage zdash:tracker stronghold.eye_spy.x int 1000 run data get entity @s Pos[0] 1
execute store result storage zdash:tracker stronghold.eye_spy.y int 1000 run data get entity @s Pos[1] 1
execute store result storage zdash:tracker stronghold.eye_spy.z int 1000 run data get entity @s Pos[2] 1
execute store result storage zdash:tracker stronghold.eye_spy.dim int 1 run scoreboard players get #sh_dim zdi
data modify storage zdash:tracker stronghold.eye_spy.logged set value 1b

function zdash_tracker:stronghold/sample_player
