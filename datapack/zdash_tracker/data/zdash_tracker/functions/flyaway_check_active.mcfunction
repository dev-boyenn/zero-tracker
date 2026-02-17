scoreboard players operation #fly_ref_x zdi = #fly_node_x zdi
scoreboard players operation #fly_ref_z zdi = #fly_node_z zdi
function zdash_tracker:flyaway_check_dist
execute if score #fly_dist2 zdi > #fly_away2 zdi if score #fly_hit zdi matches 0 run function zdash_tracker:flyaway_detect
