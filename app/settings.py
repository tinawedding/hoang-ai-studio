"""One bounded schema for the editor and the render API."""
CONTROLS = [
    # id, Vietnamese label, minimum, maximum, step, default, unit, group
    ("pitch", "Cao độ", -12, 12, .1, 0, "st", "voice"),
    ("noise", "Giảm nhiễu nền", 0, 30, 1, 8, "dB", "voice"),
    ("deesser", "Giảm âm xì · De-esser", 0, 100, 1, 0, "%", "voice"),
    ("highpass_hz", "Cắt tiếng ù · High-pass", 20, 300, 5, 80, "Hz", "voice"),
    ("lowpass_hz", "Cắt dải cao · Low-pass", 4000, 20000, 100, 20000, "Hz", "voice"),
    ("bass", "Âm trầm · 140 Hz", -12, 12, .5, 0, "dB", "eq"),
    ("lowmid", "Độ dày · 400 Hz", -12, 12, .5, 0, "dB", "eq"),
    ("mid", "Độ rõ lời · Mid", -12, 12, .5, 0, "dB", "eq"),
    ("mid_hz", "Tần số Mid", 500, 3000, 25, 1200, "Hz", "eq"),
    ("mid_q", "Độ hẹp dải Mid · Q", .3, 4, .1, .8, "Q", "eq"),
    ("presence", "Độ nổi · 3 kHz", -12, 12, .5, 0, "dB", "eq"),
    ("treble", "Độ sáng · 6.5 kHz", -12, 12, .5, 0, "dB", "eq"),
    ("threshold", "Ngưỡng nén · Threshold", -48, 0, 1, -18, "dB", "dynamics"),
    ("ratio", "Tỉ lệ nén · Ratio", 1, 12, .5, 3, ":1", "dynamics"),
    ("attack", "Thời gian bắt · Attack", 1, 100, 1, 15, "ms", "dynamics"),
    ("release", "Thời gian nhả · Release", 40, 1000, 10, 180, "ms", "dynamics"),
    ("makeup", "Bù âm sau nén · Makeup", 0, 12, .5, 3.5, "dB", "dynamics"),
    ("gate_threshold", "Ngưỡng mở Gate", -70, -20, 1, -48, "dB", "dynamics"),
    ("gate_release", "Độ mềm Gate", 50, 800, 10, 180, "ms", "dynamics"),
    ("reverb", "Độ vang phòng", 0, 40, 1, 0, "%", "space"),
    ("room", "Kích thước phòng", .5, 2, .1, 1, "×", "space"),
    ("gain", "Âm lượng đầu ra", -12, 12, .5, 0, "dB", "space"),
    ("ceiling", "Trần chống vỡ · Limiter", -6, -.3, .1, -1.5, "dB", "space"),
    ("fade", "Vào / ra nhẹ · Fade", 0, 3, .1, 0, "s", "space"),
    ("target_lufs", "Độ lớn khi xuất", -24, -12, 1, -16, "LUFS", "master"),
]
DEFAULTS = {row[0]: float(row[5]) for row in CONTROLS}
DEFAULTS.update(highpass=True, compress=True, normalize=True, gate=False)
LIMITS = {row[0]: (row[2], row[3]) for row in CONTROLS}


def editor_schema():
    names = ("id", "label", "min", "max", "step", "default", "unit", "group")
    return [dict(zip(names, row)) for row in CONTROLS]
