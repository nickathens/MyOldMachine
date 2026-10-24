extends Node
# Probe for the GDRE proof (Godot 3): known names, literals and control flow,
# compiled by Godot 3.6 and decompiled back. run_all() returns one line per
# check, so the original and the decompiled script can be run side by side.

signal scored(points, who)

enum Mode { IDLE, RUNNING = 5, DONE }
enum { FLAG_A = 1, FLAG_B = 2, FLAG_C = 4 }

const MARKER = "RE_GDRE_MARKER_33"
const LIMITS = [3, 7, 11]
const TABLE = {"a": 1, "b": [2, 3], 4: "four"}
const HALF = 0.5
const THIRD = 0.333333333333333314829616256247
const TINY = 1e-7
const HUGE = 1.5e300
const WHOLE_FLOAT = 3.0
const NEG_ZERO = -0.0
const BIG_INT = 9223372036854775807
const HEX = 0xFF
const BIN = 0b1011
const UNDERSCORED = 1_000_000
const ESCAPES = "tab\there \"quoted\" back\\slash\nnewline"
const UNICODE = "Ελληνικά é ü 日本"
const VEC = Vector2(1.5, -2.0)
const COL = Color(0.1, 0.2, 0.3, 1.0)

export var speed = 2.5
export(float, 0, 10, 0.5) var level = 1.0
onready var label = $Label
var counter = 0
var health = 100 setget set_health, get_health


class Inner:
	var v = 1

	func twice():
		return v * 2


func set_health(value):
	health = int(clamp(value, 0, 100))


func get_health():
	return health


static func pan(x, width):
	if width <= 0:
		return 0
	var p = (x * 200) / width - 100
	return int(clamp(p, -100, 100))


func classify(n):
	match n:
		0:
			return "zero"
		1, 2, 3:
			return "small"
		_:
			if n < 0:
				return "negative"
			if n > 1000:
				return "huge"
			return "big"


func total(items):
	var t = 0
	for v in items:
		if v % 2 == 0:
			continue
		t += v
	var i = 0
	while true:
		i += 1
		if i > 3:
			break
		t += i
	for k in range(3):
		t -= k
	return t


func bits(a, b):
	return [a & b, a | b, a ^ b, ~a, a << 3, b >> 1, a % 7, -a, posmod(-a, 5)]


func floats(x):
	return [x / 3.0, x * HALF, fmod(x, 1.25), stepify(x, 0.1), floor(x), ceil(x)]


func logic(a, b):
	var parts = []
	parts.append(a and b)
	parts.append(a or b)
	parts.append(not a)
	parts.append("yes" if a != b else "no")
	parts.append(1 in [1, 2] and not (3 in [1, 2]))
	return str(parts)


func strings(s):
	return "%s|%d|%05.2f|%x" % [s.to_upper(), s.length(), PI, 255] + s.substr(1, 2) + str(s.find("b"))


func typed(values: Array, scale: int = 2) -> int:
	var sum := 0
	for v in values:
		sum += v * scale
	return sum


func run_all():
	var out = PoolStringArray()
	for x in [-50, 0, 1, 63, 64, 65, 127, 128, 300]:
		for w in [0, 1, 64, 128]:
			out.append("pan %d %d = %d" % [x, w, pan(x, w)])
	for n in [-5, 0, 1, 2, 3, 4, 999, 1000, 1001, 5000]:
		out.append("classify %d = %s" % [n, classify(n)])
	out.append("total %d" % total(LIMITS))
	out.append("total2 %d" % total([2, 4, 5, 9, 10]))
	for pair in [[12, 10], [0, 1], [-7, 3], [255, 15]]:
		out.append("bits %s" % str(bits(pair[0], pair[1])))
	for f in [0.0, 1.0, 2.5, -3.75, 10.125]:
		out.append("floats %s" % var2bytes(floats(f)).hex_encode())
	for a in [true, false]:
		for b in [true, false]:
			out.append("logic %s" % logic(a, b))
	out.append("strings " + strings("abcdef"))
	out.append("typed %d" % typed([1, 2, 3]))
	out.append("typed2 %d" % typed([1, 2, 3], 5))
	var inner = Inner.new()
	inner.v = 21
	out.append("inner %d" % inner.twice())
	self.health = 250
	out.append("health %d" % self.health)
	self.health = -4
	out.append("health %d" % self.health)
	var kinds = []
	for v in [MARKER, LIMITS, TABLE, HALF, VEC, Mode.RUNNING, FLAG_C]:
		kinds.append(typeof(v))
	out.append("types " + str(kinds))
	out.append("whole " + str(WHOLE_FLOAT / 2) + " " + str(typeof(WHOLE_FLOAT)))
	return out


func _ready():
	label.text = MARKER
	connect("scored", self, "_on_scored")
	emit_signal("scored", 3, "probe")
	yield(get_tree(), "idle_frame")
	print(MARKER, " ", run_all().size())


func _on_scored(points, _who):
	counter += points


func _on_timer_timeout():
	queue_free()
