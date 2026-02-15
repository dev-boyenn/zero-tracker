# Approximate source attribution using health delta and observed explosive block disappearance.
scoreboard players set #hp_now zdi 0
execute if entity @e[type=minecraft:ender_dragon,limit=1] store result score #hp_now zdi run data get entity @e[type=minecraft:ender_dragon,limit=1] Health 1

scoreboard players operation #hp_diff zdi = #hp_last zdi
scoreboard players operation #hp_diff zdi -= #hp_now zdi
execute if score #hp_diff zdi matches ..-1 run scoreboard players set #hp_diff zdi 0
scoreboard players operation #hp_last zdi = #hp_now zdi

# Snapshot nearby bed/anchor blocks around player to detect disappearance.
function zdash_tracker:scan_nearby_blocks

scoreboard players operation #near_bed_drop zdi = #near_bed_prev zdi
scoreboard players operation #near_bed_drop zdi -= #near_bed_now zdi
scoreboard players operation #near_anch_drop zdi = #near_anch_prev zdi
scoreboard players operation #near_anch_drop zdi -= #near_anch_now zdi
execute if score #near_bed_drop zdi matches ..-1 run scoreboard players set #near_bed_drop zdi 0
execute if score #near_anch_drop zdi matches ..-1 run scoreboard players set #near_anch_drop zdi 0

# Convert block drops to explosion counts.
# Beds are usually 2 blocks, so convert dropped bed blocks to bed explosions with ceil(drop / 2).
scoreboard players set #explode_beds zdi 0
execute if score #near_bed_drop zdi matches 1.. run scoreboard players operation #explode_beds zdi = #near_bed_drop zdi
execute if score #near_bed_drop zdi matches 1.. run scoreboard players add #explode_beds zdi 1
execute if score #near_bed_drop zdi matches 1.. run scoreboard players operation #explode_beds zdi /= #two zdi

scoreboard players set #explode_anchors zdi 0
execute if score #near_anch_drop zdi matches 1.. run scoreboard players operation #explode_anchors zdi = #near_anch_drop zdi
execute if score #near_anch_drop zdi matches 1.. run scoreboard players add #explode_anchors zdi 1
execute if score #near_anch_drop zdi matches 1.. run scoreboard players operation #explode_anchors zdi /= #two zdi

scoreboard players operation #explode_total zdi = #explode_beds zdi
scoreboard players operation #explode_total zdi += #explode_anchors zdi

# Advance previous snapshot.
scoreboard players operation #near_bed_prev zdi = #near_bed_now zdi
scoreboard players operation #near_anch_prev zdi = #near_anch_now zdi

# Track explosion totals from drops only.
scoreboard players operation #explode_beds_total zdi += #explode_beds zdi
scoreboard players operation #explode_anchors_total zdi += #explode_anchors zdi
execute store result storage zdash:tracker run.deltas.beds_exploded int 1 run scoreboard players get #explode_beds_total zdi
execute store result storage zdash:tracker run.deltas.anchors_interactions int 1 run scoreboard players get #explode_anchors_total zdi
execute store result storage zdash:tracker run.deltas.anchors_exploded_est int 1 run scoreboard players get #explode_anchors_total zdi

execute if score #explode_total zdi matches 1.. run data modify storage zdash:tracker run.explode_tmp set value {gt:0L,explode_beds:0,explode_anchors:0}
execute if score #explode_total zdi matches 1.. store result storage zdash:tracker run.explode_tmp.gt long 1 run time query gametime
execute if score #explode_total zdi matches 1.. store result storage zdash:tracker run.explode_tmp.explode_beds int 1 run scoreboard players get #explode_beds zdi
execute if score #explode_total zdi matches 1.. store result storage zdash:tracker run.explode_tmp.explode_anchors int 1 run scoreboard players get #explode_anchors zdi
execute if score #explode_total zdi matches 1.. run data modify storage zdash:tracker run.explode_events append from storage zdash:tracker run.explode_tmp
execute if score #explode_total zdi matches 1.. unless data storage zdash:tracker run.explosive_stand{logged:1b} as @a[limit=1] store result storage zdash:tracker run.explosive_stand.y int 1 run data get entity @s Pos[1] 1
execute if score #explode_total zdi matches 1.. unless data storage zdash:tracker run.explosive_stand{logged:1b} run data modify storage zdash:tracker run.explosive_stand.logged set value 1b

scoreboard players set #dmg_bed zdi 0
scoreboard players set #dmg_anch zdi 0
scoreboard players set #dmg_other zdi 0

# Attribute damage using explosion counts from drops only.
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 1.. run scoreboard players operation #dmg_bed zdi = #hp_diff zdi
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 1.. run scoreboard players operation #dmg_bed zdi *= #explode_beds zdi
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 1.. run scoreboard players operation #dmg_bed zdi /= #explode_total zdi
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 1.. run scoreboard players operation #dmg_anch zdi = #hp_diff zdi
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 1.. run scoreboard players operation #dmg_anch zdi -= #dmg_bed zdi
execute if score #hp_diff zdi matches 1.. if score #explode_total zdi matches 0 run scoreboard players operation #dmg_other zdi = #hp_diff zdi

scoreboard players operation #bed_dmg_total zdi += #dmg_bed zdi
scoreboard players operation #anch_dmg_total zdi += #dmg_anch zdi
scoreboard players operation #other_dmg_total zdi += #dmg_other zdi

execute store result storage zdash:tracker run.damage_by_source.beds_scaled int 1 run scoreboard players get #bed_dmg_total zdi
execute store result storage zdash:tracker run.damage_by_source.anchors_scaled int 1 run scoreboard players get #anch_dmg_total zdi
execute store result storage zdash:tracker run.damage_by_source.other_scaled int 1 run scoreboard players get #other_dmg_total zdi

execute if score #hp_diff zdi matches 1.. run data modify storage zdash:tracker run.damage_tmp set value {gt:0L,hp_diff_scaled:0,explode_beds:0,explode_anchors:0,bed_dmg_scaled:0,anchor_dmg_scaled:0,other_dmg_scaled:0}
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.gt long 1 run time query gametime
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.hp_diff_scaled int 1 run scoreboard players get #hp_diff zdi
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.explode_beds int 1 run scoreboard players get #explode_beds zdi
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.explode_anchors int 1 run scoreboard players get #explode_anchors zdi
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.bed_dmg_scaled int 1 run scoreboard players get #dmg_bed zdi
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.anchor_dmg_scaled int 1 run scoreboard players get #dmg_anch zdi
execute if score #hp_diff zdi matches 1.. store result storage zdash:tracker run.damage_tmp.other_dmg_scaled int 1 run scoreboard players get #dmg_other zdi
execute if score #hp_diff zdi matches 1.. run data modify storage zdash:tracker run.damage_events append from storage zdash:tracker run.damage_tmp

execute if score #hp_diff zdi matches 1.. run tellraw @a [{"text":"[zdashdbg] hpDelta=","color":"gold"},{"score":{"name":"#hp_diff","objective":"zdi"},"color":"white"},{"text":" explode b=","color":"dark_aqua"},{"score":{"name":"#explode_beds","objective":"zdi"},"color":"white"},{"text":" a=","color":"dark_aqua"},{"score":{"name":"#explode_anchors","objective":"zdi"},"color":"white"},{"text":" -> bed=","color":"aqua"},{"score":{"name":"#dmg_bed","objective":"zdi"},"color":"white"},{"text":" anchor=","color":"aqua"},{"score":{"name":"#dmg_anch","objective":"zdi"},"color":"white"},{"text":" other=","color":"aqua"},{"score":{"name":"#dmg_other","objective":"zdi"},"color":"white"}]
