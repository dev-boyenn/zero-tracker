scoreboard players set #active zdi 1
scoreboard players set #dragon_died zdi 0

execute store result storage zdash:tracker run.start_gt long 1 run time query gametime
data modify storage zdash:tracker run.active set value 1b
data modify storage zdash:tracker run.end_gt set value 0L
data modify storage zdash:tracker run.dragon_died set value 0b
data modify storage zdash:tracker run.dragon_died_gt set value 0L
data modify storage zdash:tracker run.deltas set value {beds_exploded:0,anchors_interactions:0,anchors_exploded_est:0,bows_shot:0,crossbows_shot:0}
data modify storage zdash:tracker run.end_entry set value {logged:0b,gt:0L,player_y:0,top_y:-1,top_is_endstone:0b}
data modify storage zdash:tracker run.explosive_stand set value {logged:0b,y:0}
data modify storage zdash:tracker run.damage_by_source set value {beds_scaled:0,anchors_scaled:0,other_scaled:0}
data modify storage zdash:tracker run.damage_events set value []
data modify storage zdash:tracker run.explode_events set value []
data modify storage zdash:tracker samples set value []

scoreboard players set #missing zdi 0
scoreboard players set #hp_last zdi 0
scoreboard players set #hp_now zdi 0
scoreboard players set #hp_diff zdi 0
scoreboard players set #bow_now zdi 0
scoreboard players set #xbow_now zdi 0
scoreboard players set #kill_now zdi 0
scoreboard players set #bow_delta zdi 0
scoreboard players set #xbow_delta zdi 0
scoreboard players set #near_bed_now zdi 0
scoreboard players set #near_bed_prev zdi 0
scoreboard players set #near_bed_drop zdi 0
scoreboard players set #near_anch_now zdi 0
scoreboard players set #near_anch_prev zdi 0
scoreboard players set #near_anch_drop zdi 0
scoreboard players set #near_total zdi 0
scoreboard players set #end_entry_logged zdi 0
scoreboard players set #entry_player_y zdi 0
scoreboard players set #entry_top_y zdi -1
scoreboard players set #entry_top_is_endstone zdi 0
scoreboard players set #explode_beds zdi 0
scoreboard players set #explode_anchors zdi 0
scoreboard players set #explode_total zdi 0
scoreboard players set #explode_beds_total zdi 0
scoreboard players set #explode_anchors_total zdi 0
scoreboard players set #dmg_bed zdi 0
scoreboard players set #dmg_anch zdi 0
scoreboard players set #dmg_other zdi 0
scoreboard players set #bed_dmg_total zdi 0
scoreboard players set #anch_dmg_total zdi 0
scoreboard players set #other_dmg_total zdi 0

execute if entity @a run execute store result score #bow_start zdi run scoreboard players get @a[limit=1] zubow
execute if entity @a run execute store result score #xbow_start zdi run scoreboard players get @a[limit=1] zuxbow
execute if entity @a run execute store result score #kill_start zdi run scoreboard players get @a[limit=1] zukill

execute if entity @e[type=minecraft:ender_dragon,limit=1] store result score #hp_last zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Health 1
