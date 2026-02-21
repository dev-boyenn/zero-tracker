# Scan a compact volume around player feet for stronghold marker blocks.
# This enables teleport-into-stronghold workflows (no Eye Spy throw needed).
function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~ ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~ ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~ ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~ ~-1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~ ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~ ~-1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~ ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~ ~-1 run function zdash_tracker:stronghold/check_marker_here

execute positioned ~ ~-1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~-1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~-1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~-1 ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~-1 ~-1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~-1 ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~-1 ~-1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~-1 ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~-1 ~-1 run function zdash_tracker:stronghold/check_marker_here

execute positioned ~ ~1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~1 ~1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~-1 ~1 ~ run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~1 ~1 run function zdash_tracker:stronghold/check_marker_here
execute positioned ~ ~1 ~-1 run function zdash_tracker:stronghold/check_marker_here
