# Stat-based Eye Spy hook.
# Called as player context when ender_eye used count increases.
function zdash_tracker:stronghold/on_eye_spy
scoreboard players operation #eye_start zdi = #eye_now zdi
