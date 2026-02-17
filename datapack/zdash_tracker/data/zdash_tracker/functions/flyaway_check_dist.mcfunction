scoreboard players operation #fly_dx zdi = #drx zdi
scoreboard players operation #fly_dx zdi -= #fly_ref_x zdi
scoreboard players operation #fly_dx2 zdi = #fly_dx zdi
scoreboard players operation #fly_dx2 zdi *= #fly_dx zdi

scoreboard players operation #fly_dz zdi = #drz zdi
scoreboard players operation #fly_dz zdi -= #fly_ref_z zdi
scoreboard players operation #fly_dz2 zdi = #fly_dz zdi
scoreboard players operation #fly_dz2 zdi *= #fly_dz zdi

scoreboard players operation #fly_dist2 zdi = #fly_dx2 zdi
scoreboard players operation #fly_dist2 zdi += #fly_dz2 zdi