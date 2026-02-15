scoreboard players set #bow_now zdi 0
scoreboard players set #xbow_now zdi 0
scoreboard players set #kill_now zdi 0

execute if entity @a run execute store result score #bow_now zdi run scoreboard players get @a[limit=1] zubow
execute if entity @a run execute store result score #xbow_now zdi run scoreboard players get @a[limit=1] zuxbow
execute if entity @a run execute store result score #kill_now zdi run scoreboard players get @a[limit=1] zukill

scoreboard players operation #bow_delta zdi = #bow_now zdi
scoreboard players operation #bow_delta zdi -= #bow_start zdi
scoreboard players operation #xbow_delta zdi = #xbow_now zdi
scoreboard players operation #xbow_delta zdi -= #xbow_start zdi

execute store result storage zdash:tracker run.deltas.bows_shot int 1 run scoreboard players get #bow_delta zdi
execute store result storage zdash:tracker run.deltas.crossbows_shot int 1 run scoreboard players get #xbow_delta zdi

function zdash_tracker:damage_attribution

execute if score #kill_now zdi > #kill_start zdi if score #dragon_died zdi matches 0 run function zdash_tracker:mark_dragon_died
