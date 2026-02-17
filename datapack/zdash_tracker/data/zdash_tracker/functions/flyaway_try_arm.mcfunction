scoreboard players set #fly_ref_x zdi -30
scoreboard players set #fly_ref_z zdi 27
scoreboard players set #fly_cand_code zdi 1
function zdash_tracker:flyaway_check_dist
execute if score #fly_dist2 zdi matches ..100 if score #fly_mon zdi matches 0 run function zdash_tracker:flyaway_arm_candidate

scoreboard players set #fly_ref_x zdi 29
scoreboard players set #fly_ref_z zdi -29
scoreboard players set #fly_cand_code zdi 2
function zdash_tracker:flyaway_check_dist
execute if score #fly_dist2 zdi matches ..100 if score #fly_mon zdi matches 0 run function zdash_tracker:flyaway_arm_candidate

scoreboard players set #fly_ref_x zdi -21
scoreboard players set #fly_ref_z zdi 0
scoreboard players set #fly_cand_code zdi 3
function zdash_tracker:flyaway_check_dist
execute if score #fly_dist2 zdi matches ..100 if score #fly_mon zdi matches 0 run function zdash_tracker:flyaway_arm_candidate

scoreboard players set #fly_ref_x zdi 20
scoreboard players set #fly_ref_z zdi 0
scoreboard players set #fly_cand_code zdi 4
function zdash_tracker:flyaway_check_dist
execute if score #fly_dist2 zdi matches ..100 if score #fly_mon zdi matches 0 run function zdash_tracker:flyaway_arm_candidate