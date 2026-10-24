"""Build and export both P3 variants into out/p3_<P3>/<variant>/ (print-oriented STL, STEP, assembled STEP).
Usage: P3=v1|v2 .venv/bin/python export_p3.py"""
import json
import os
from pathlib import Path
from build123d import export_step, export_stl
P3 = os.environ.get('P3', 'v1')
src = open({'v1': 'enclosure_p3.py', 'v2': 'enclosure_p3_v2.py', 'v3': 'enclosure_p3_v3.py', 'v4': 'enclosure_p3_v4.py', 'v5': 'enclosure_p3_v5.py', 'v5_1': 'enclosure_p3_v5_1.py', 'v5_2': 'enclosure_p3_v5_2.py', 'v5_3': 'enclosure_p3_v5_3.py', 'v5_4': 'enclosure_p3_v5_4.py', 'v5_5': 'enclosure_p3_v5_5.py', 'v5_6': 'enclosure_p3_v5_6.py', 'v5_5L': 'enclosure_p3_v5_5L.py'}[P3]).read().replace('if "show_object" in globals():', 'if False:')
report = {}
for variant in ("fdm", "mjf"):
    out = Path('out/p3_%s' % P3) / variant
    out.mkdir(parents=True, exist_ok=True)
    g = {"VARIANT": variant}
    exec(src, g)
    base, lid, lid_a = g['base'], g['lid'], g['lid_assembled']
    assert len(base.solids()) == 1 and len(lid.solids()) == 1 and base.is_valid and lid.is_valid
    ov = base & lid_a
    assert not ov or ov.volume < 1e-6
    for name, shape in (('base', base), ('lid', lid)):
        export_step(shape, str(out / f'{name}.step'))
        export_stl(shape, str(out / f'{name}.stl'), tolerance=0.01, angular_tolerance=0.1)
    export_step(base + lid_a, str(out / 'assembled.step'))
    bb = (base + lid_a).bounding_box().size
    dens = 1.24 if variant == 'fdm' else 1.01
    report[variant] = dict(size=[round(bb.X, 2), round(bb.Y, 2), round(bb.Z, 2)],
                           base_g=round(base.volume / 1000 * dens, 1), lid_g=round(lid.volume / 1000 * dens, 1),
                           base_cm3=round(base.volume / 1000, 2), lid_cm3=round(lid.volume / 1000, 2))
print(json.dumps(report, indent=1))
