extends SceneTree
# GDRE proof harness: load the script named by RE_PROBE and print every script
# constant (exact bytes, so the sign of a zero and the last bit of a float show)
# and one RESULT line per check from its run_all(), so an original and a
# decompiled script can be compared run for run under the same engine.
# Constants are read from the script's own table, not from inside a function:
# the compiler merges equal constants in a function (it hashes -0.0 and 0.0 as
# one), which hid a lost sign the first time this ran. A NodePath is printed as
# text: its byte encoding carries uninitialised padding.


func _init() -> void:
	var script: Script = load(OS.get_environment("RE_PROBE"))
	var table := script.get_script_constant_map()
	var names := table.keys()
	names.sort()
	for name in names:
		var v = table[name]
		var shown: String
		if typeof(v) == TYPE_OBJECT:
			shown = "object"
		elif typeof(v) == TYPE_NODE_PATH:
			shown = str(v)
		else:
			shown = var_to_bytes(v).hex_encode()
		print("CONST ", name, " ", typeof(v), " ", shown)
	var probe = script.new()
	for line in probe.run_all():
		print("RESULT ", line)
	probe.free()
	quit()
