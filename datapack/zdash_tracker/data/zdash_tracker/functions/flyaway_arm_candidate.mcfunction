scoreboard players set #fly_mon zdi 1
scoreboard players operation #fly_node_x zdi = #fly_ref_x zdi
scoreboard players operation #fly_node_z zdi = #fly_ref_z zdi
scoreboard players operation #fly_node_code zdi = #fly_cand_code zdi

data modify storage zdash:tracker run.flyaway.armed set value 1b
execute store result storage zdash:tracker run.flyaway.node_code int 1 run scoreboard players get #fly_node_code zdi
execute if score #fly_node_code zdi matches 1 run data modify storage zdash:tracker run.flyaway.node set value "back_diag"
execute if score #fly_node_code zdi matches 2 run data modify storage zdash:tracker run.flyaway.node set value "front_diag"
execute if score #fly_node_code zdi matches 3 run data modify storage zdash:tracker run.flyaway.node set value "back_straight"
execute if score #fly_node_code zdi matches 4 run data modify storage zdash:tracker run.flyaway.node set value "front_straight"