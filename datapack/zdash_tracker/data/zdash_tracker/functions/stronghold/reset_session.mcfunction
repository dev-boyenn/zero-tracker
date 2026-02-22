# Reset stronghold tracker state for a fresh world/session.
scoreboard players set #sh_active zdi 0
scoreboard players set #sh_tick zdi 0
scoreboard players set #sh_dim zdi 0

data modify storage zdash:tracker stronghold.active set value 0b
data modify storage zdash:tracker stronghold.eye_spy set value {logged:0b,gt:0L,x:0,y:0,z:0,dim:0}
data modify storage zdash:tracker stronghold.end_enter set value {logged:0b,gt:0L,x:0,y:0,z:0,dim:0}
data modify storage zdash:tracker stronghold.spectator set value {detected:0b,gt:0L,x:0,y:0,z:0,dim:0}
data modify storage zdash:tracker stronghold.samples set value []
