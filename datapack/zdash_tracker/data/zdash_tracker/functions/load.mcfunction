scoreboard objectives add zdi dummy
scoreboard objectives add zubow minecraft.used:minecraft.bow
scoreboard objectives add zuxbow minecraft.used:minecraft.crossbow
scoreboard objectives add zukill minecraft.killed:minecraft.ender_dragon

scoreboard players set #active zdi 0
scoreboard players set #dragon_died zdi 0
scoreboard players set #bow_start zdi 0
scoreboard players set #xbow_start zdi 0
scoreboard players set #kill_start zdi 0
scoreboard players set #missing zdi 0
scoreboard players set #dragon_hp zdi 0
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
scoreboard players set #two zdi 2
scoreboard players set #fly_mon zdi 0
scoreboard players set #fly_hit zdi 0
scoreboard players set #fly_node_x zdi 0
scoreboard players set #fly_node_z zdi 0
scoreboard players set #fly_node_code zdi 0
scoreboard players set #fly_cand_code zdi 0
scoreboard players set #fly_ref_x zdi 0
scoreboard players set #fly_ref_z zdi 0
scoreboard players set #fly_dx zdi 0
scoreboard players set #fly_dz zdi 0
scoreboard players set #fly_dx2 zdi 0
scoreboard players set #fly_dz2 zdi 0
scoreboard players set #fly_dist2 zdi 0
scoreboard players set #drx zdi 0
scoreboard players set #dry zdi 0
scoreboard players set #drz zdi 0
scoreboard players set #fly_now_gt zdi 0
scoreboard players set #fly_entry_gt zdi 0
scoreboard players set #fly_since_end zdi 0
scoreboard players set #fly_crystals_alive zdi 0
scoreboard players set #fly_within2 zdi 100
scoreboard players set #fly_away2 zdi 600

# Storage layout.
data modify storage zdash:tracker meta set value {scale:1000}
data modify storage zdash:tracker meta.version set value "v2026-02-17-flyaway3"
data modify storage zdash:tracker run set value {active:0b,start_gt:0L,end_gt:0L,dragon_died:0b,dragon_died_gt:0L,flyaway:{armed:0b,detected:0b,node:"",node_code:0,detected_gt:0L,dragon_x:0,dragon_y:0,dragon_z:0,detected_dist2:0,crystals_alive:-1},deltas:{beds_exploded:0,anchors_interactions:0,anchors_exploded_est:0,bows_shot:0,crossbows_shot:0},end_entry:{logged:0b,gt:0L,player_y:0,top_y:-1,top_is_endstone:0b},explosive_stand:{logged:0b,y:0},damage_by_source:{beds_scaled:0,anchors_scaled:0,other_scaled:0},damage_events:[],explode_events:[]}
data modify storage zdash:tracker cur set value {x:0,y:0,z:0,yaw:0,pitch:0,gt:0L}
execute unless data storage zdash:tracker samples run data modify storage zdash:tracker samples set value []
