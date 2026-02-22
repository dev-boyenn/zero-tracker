# Called from hidden advancement reward when Eye Spy is completed.
# Hard-gated to Overworld using a dimension predicate.
execute if predicate zdash_tracker:in_overworld run function zdash_tracker:stronghold/on_eye_spy_overworld
