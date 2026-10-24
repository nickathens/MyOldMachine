extends SceneTree
# GDRE proof harness (Godot 3): every script constant as exact bytes, then one
# RESULT line per check from run_all(). See harness4.gd for why constants are
# read from the script's own table.


func _init():
	var script = load(OS.get_environment("RE_PROBE"))
	var table = script.get_script_constant_map()
	var names = table.keys()
	names.sort()
	for name in names:
		var v = table[name]
		var shown
		if typeof(v) == TYPE_OBJECT:
			shown = "object"
		elif typeof(v) == TYPE_NODE_PATH:
			shown = str(v)
		else:
			shown = var2bytes(v).hex_encode()
		print("CONST ", name, " ", typeof(v), " ", shown)
	var probe = script.new()
	for line in probe.run_all():
		print("RESULT ", line)
	probe.free()
	quit()
