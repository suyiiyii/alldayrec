"""P3 checks: contents vs shell (assembled), lever travel, USB plug, min wall thickness.
Usage: .venv/bin/python check_p3.py [fdm|mjf]
"""
import sys
import math
from build123d import Compound, Cylinder, Pos, extrude, RectangleRounded, Rot, Align, Location

VARIANT = globals().get("VARIANT") or (sys.argv[1] if len(sys.argv) > 1 else "fdm")
import os
P3 = globals().get("P3") or os.environ.get("P3", "v1")          # v1 | v2
SRC = {"v1": "enclosure_p3.py", "v2": "enclosure_p3_v2.py", "v3": "enclosure_p3_v3.py", "v4": "enclosure_p3_v4.py", "v5": "enclosure_p3_v5.py", "v5_1": "enclosure_p3_v5_1.py", "v5_2": "enclosure_p3_v5_2.py", "v5_3": "enclosure_p3_v5_3.py", "v5_4": "enclosure_p3_v5_4.py", "v5_5": "enclosure_p3_v5_5.py", "v5_6": "enclosure_p3_v5_6.py", "v5_5L": "enclosure_p3_v5_5L.py"}[P3]
g = {"VARIANT": VARIANT}
exec(open(SRC).read().replace('if "show_object" in globals():', 'if False:'), g)
globals().update({k: v for k, v in g.items() if not k.startswith('__')})


def holes(shape, pts, top_z, d=None):
    for x, y in pts:
        shape -= bore(x, y, d or m2_hole_d, top_z - 5, 10)
    return shape


tip_y = south_surface_y(sw_zc) - lever_proud
crush = max(g.get('sw_crush', 0) - g.get('sw_end_gap', 0), 0) + 0.001   # v2 crush bumps grip the switch ends
bc = g.get('board_crush', 0) + (0.001 if 'board_crush' in g else 0)   # v5.4 board clip bumps grip the PCB edges
sd_wr = g.get('sd_w_real', sd_w)                  # v5.5: measured board widths (clips only); older: nominal
mb_c, mb_wr = mb_y0 + mb_yl / 2, g.get('mb_w_real', mb_yl)
bcm = g.get('mb_crush', g.get('board_crush', 0)) + (0.001 if 'board_crush' in g else 0)   # v5.5: mic board knob
tubes = [bore(x, y, tube_id + 2 * tube_wall + 0.1, 0, pm_face) for x, y in leds]
parts = block(0, 29.9, 0, pm_w, pm_face - pm_parts_h, pm_face)
for t in tubes:
    parts -= t
env = {
    'promicro pcb': block(0, pm_l, 0, pm_w, pm_face, pm_top),
    'promicro parts (<=1.0)': parts,
    'leds': Compound([block(x - 0.8, x + 0.8, y - 0.4, y + 0.4, pm_face - led_h, pm_face) for x, y in leds]),
    'antenna': block(*antenna, pm_face - 1.0, pm_face),
    'usb-c': block(usb_front, usb_front + usb_len, yc - usb_w / 2, yc + usb_w / 2, usb_zc - usb_h / 2, usb_zc + usb_h / 2),
    'back kapton+foam': block(0, pm_l, 0, pm_w, pm_top, bat_z0),
    'battery': Pos(bat_x0 + bat_l / 2, yc, bat_z0) * extrude(RectangleRounded(bat_l, bat_w, 1.0), amount=bat_t),
    'battery lead bend (SW)': block(*g.get('lead_zone', (-1.5, 0, -0.5, 2.0)), floor + floor_chamfer if 'lead_zone' not in g else pm_top, bat_z1),
    'top foam': block(bat_x0, bat_x0 + bat_l, yc - bat_w / 2, yc + bat_w / 2, bat_z1, front_z0),
    'sd socket': block(*sock, front_z0, sd_z0),
    'sd pcb': holes(block(sd_x0, sd_x0 + sd_l, yc - sd_wr / 2 + bc, yc + sd_wr / 2 - bc, sd_z0, board_top), sd_holes, board_top),
    'sd header joints': block(sd_hdr_x - 0.8, sd_hdr_x + 0.8, 2.1, 16.4, board_top, board_top + joint_h),
    'tf card': block(*card, sd_z0 - card_mid - card_t / 2, sd_z0 - card_mid + card_t / 2),
    'mic pcb': holes(block(mb_x0, mb_x0 + mb_xl, mb_c - mb_wr / 2 + bcm, mb_c + mb_wr / 2 - bcm, sd_z0, mb_top), mb_holes, mb_top,
                     g.get('mb_hole_d', m2_hole_d)),
    'mic package': block(*mic_pkg, mb_top, mic_top),
    'mic caps (h=%.2f)' % cap_h: Compound([block(*c, mb_top, mb_top + cap_h) for c in mic_caps]),
    'mic resistor': block(*mic_res, mb_top, mb_top + res_h),
    'mic pad joints': block(mb_x0 + 0.77, mb_x0 + 2.37, mb_y0 + 0.5, mb_y0 + mb_yl - 0.5, mb_top, mb_top + joint_h),
    'switch body': block(sw_x0 + crush, sw_x1 - crush, sw_y0, sw_y1, sw_z0, sw_z0 + sw_h),
    'switch pins+joints': block(sw_xc - g.get('sw_pins_half', 3.2), sw_xc + g.get('sw_pins_half', 3.2), sw_y1, sw_y1 + 1.0, sw_zc - 0.5, sw_zc + 0.5),
    'north wire band': block(groove[0], groove[1], sock[3] + 0.1, iy1, front_z0, sd_z0),
    'north wire groove': block(groove[0], groove[1], iy1, iy1 + groove[2], g.get('groove_z0', floor + floor_chamfer), sd_z0),
    'south wire channel': block(max(0.5, ix0 + cav_r), 25.5, iy0 + 0.05, -0.15, floor + floor_chamfer, pm_top),   # v5 (v1-v4: rail there)
}
for name, sgn in (('lever ON', sw_on_side), ('lever OFF', -sw_on_side)):
    x = sw_xc + sgn * lever_off
    env[name] = block(x - lever_w / 2, x + lever_w / 2, tip_y, sw_y0, sw_zc - lever_t / 2, sw_zc + lever_t / 2)
# USB-C plug fully inserted: overmould face plug_gap before the receptacle front (v1-v3: 0.7; v4: 0, touching),
# metal shell 8.4 x 2.6 reaching 6.5 into the receptacle
if 'lanyard_holes' in g:     # v5.5L: a D1.5 cord through both holes and along the inside of the end wall
    lz, cr = (front_z0 + min(sd_z0, z_p - lip_below)) / 2, 0.75
    ya, yb = lanyard_holes
    env['lanyard cord D1.5'] = Pos(ix1 - cr - 0.05, (ya + yb) / 2, lz) * Rot(90, 0, 0) * Cylinder(cr, yb - ya) \
        + Compound([Pos(ix1 - cr - 0.05, y, lz) * Rot(0, 90, 0) * Cylinder(cr, wall_e + 2.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
                    for y in (ya, yb)])
plug_x1 = usb_front - g.get('plug_gap', 0.7)
env['usb plug overmould'] = Pos(plug_x1 - 15, yc, usb_zc) * Rot(0, 90, 0) * extrude(
    RectangleRounded(g.get('plug_h', 6.5), g.get('plug_w', 12.0), 1.5), amount=15)
env['usb plug shell'] = Pos(plug_x1, yc, usb_zc) * Rot(0, 90, 0) * extrude(RectangleRounded(2.6, 8.4, 1.25), amount=6.5)

if __name__ == "__main__":
    bad = []
    for n, e in env.items():
        for pn, part in (('base', base), ('lid', lid_assembled)):
            i = part & e
            v = i.volume if i else 0
            if v > 1e-4:
                bad.append((n, pn, round(v, 4)))
    print(VARIANT, 'content collisions:', bad or 'none')
    ov = base & lid_assembled
    print('base/lid overlap:', round(ov.volume, 5) if ov else 0)
    if bc > 0.01:
        for nm, bx in (('sd', holes(block(sd_x0, sd_x0 + sd_l, yc - sd_wr / 2, yc + sd_wr / 2, sd_z0, board_top), sd_holes, board_top)),
                       ('mic', holes(block(mb_x0, mb_x0 + mb_xl, mb_c - mb_wr / 2, mb_c + mb_wr / 2, sd_z0, mb_top), mb_holes, mb_top, g.get('mb_hole_d', m2_hole_d)))):
            iv = bx & lid_assembled
            print('%s board clip bumps: %.2f into each long edge, %.2f mm3 total (by design)' % (
                nm, board_crush if nm == 'sd' else g.get('mb_crush', board_crush), iv.volume if iv else 0))
    if crush > 0.01:
        sw = block(sw_x0, sw_x1, sw_y0, sw_y1, sw_z0, sw_z0 + sw_h) & lid_assembled
        print('switch crush bumps: %.2f per end, %.2f mm3 total (by design)' % (crush, sw.volume if sw else 0))
    if 'latch_x' in g:      # v5.5 lid lock
        eng = latch_bump - fit
        tana = math.tan(math.radians(latch_angle))
        gam = math.atan2(latch_lead, latch_bump)                    # lead-in face from horizontal
        zt = lip_z0 + latch_lead + latch_flat
        L = zt - latch_z0                                           # tab root to the hook
        E, mu = (3500.0, 0.3) if VARIANT == 'fdm' else (1700.0, 0.3)   # PLA / PA12 MPa (rough), friction
        t_top, t_root = latch_t - inset_at(z_p - 0.3), latch_t - inset_at(latch_z0)
        k = 3 * E * (latch_w * latch_t ** 3 / 12) / L ** 3
        f_tab = k * eng
        f_close = f_tab * (1 + mu * math.tan(gam)) / (math.tan(gam) - mu)
        f_open = f_tab * (1 + mu * tana) / (tana - mu)
        print('latch: 2 x (x %.1f, south + north), tab %.1f wide x %.2f-%.2f thick x %.1f long (slots %.1f), hook %.2f past the wall, '
              'retention face %.0f deg, lead-in %.0f deg; root strain %.2f%%' % (latch_x, latch_w, t_top, t_root, L, latch_slot, eng,
              latch_angle, math.degrees(gam), 150 * latch_t * eng / L ** 2))
        print('latch force estimate (E %.0f MPa, mu %.1f): tab %.1f N, close %.1f N, pull-off %.1f N per latch' % (E, mu, f_tab, f_close, f_open))
        seat = [min(s_.distance_to(base) for s_ in (lid_assembled & block(latch_x - 2, latch_x + 2, ya, yb, lip_z0 - 0.1, z_p)).solids())
                for ya, yb in ((iy0 - 1, iy0 + fit - 0.01), (iy1 - fit + 0.01, iy1 + 1))]
        print('latch hooks closed: gap to the catch %.2f / %.2f (south / north)' % tuple(seat))
        # closing stroke: lid lowered onto the base; outside the latch tabs nothing may touch
        tab_x = (latch_x - latch_w / 2 - latch_slot, latch_x + latch_w / 2 + latch_slot)
        worst_tab, other = 0.0, []
        for dz in (2.2, 1.9, 1.6, 1.3, 1.1, 0.9, 0.7, 0.5, 0.3, 0.15, 0.05):
            ov = lid_assembled.moved(Location((0, 0, dz))) & base
            for s_ in (ov.solids() if ov else []):
                bb = s_.bounding_box()
                if bb.min.X >= tab_x[0] - 0.01 and bb.max.X <= tab_x[1] + 0.01:
                    worst_tab = max(worst_tab, bb.max.Y - bb.min.Y)
                elif s_.volume > 1e-5:
                    other.append((dz, round(s_.volume, 4), [round(v, 2) for v in (bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z)]))
        print('closing stroke (lid 2.2 -> 0.05 above seated): tabs pushed out by up to %.2f (= hook %.2f past the wall), '
              'other contact: %s' % (worst_tab, eng, other or 'none'))
        # SD lip pushed out by the full clip interference behind each bump: clearance to the base wall
        d_lip = []
        for x in sd_clip_x:
            for y0, y1, dy in ((iy0 + fit - 0.2, iy0 + fit + lip_t, -board_crush), (iy1 - fit - lip_t, iy1 - fit + 0.2, board_crush)):
                piece = lid_assembled & block(x - sd_relief_w, x + sd_relief_w, y0, y1, lip_z0, z_p - 0.02)
                d_lip.append(min(s_.moved(Location((0, dy, 0))).distance_to(base) for s_ in piece.solids()))
        print('SD lip pushed out %.2f behind each clip bump: clearance to the base wall %.2f..%.2f (closed lid unaffected)' % (
            board_crush, min(d_lip), max(d_lip)))
        x_out = ox1 - inset_at(z_p)
        print('nail notch %.1f wide x %.1f tall x %.1f deep at the antenna end; lid wall left %.2f' % (
            notch_w, notch_h, notch_d, (x_out - notch_d) - (ix1 - fit - lip_t)))
    if 'lanyard_front' in g:    # v5.6 lanyard tunnel walls
        r_l = lanyard_d / 2
        x_t = ox1 - inset_at(lanyard_z) - lanyard_front - r_l
        bx0, bz0, bz1 = lanyard_boss
        tun = Pos(x_t, yc, lanyard_z) * Rot(90, 0, 0) * Cylinder(r_l, iy1 - iy0 + 2.0)     # inside the outline
        inner = cavity_solid() - block(bx0 - 0.01, ix1 + 0.02, iy0 - 0.02, iy1 + 0.02, bz0, bz1)
        flat = tun & block(ix1 - 5, ox1 + 1, oy0 + plan_r, oy1 - plan_r, -1, H + 1)     # along the flat end face
        outside = block(ox0 - 2, ox1 + 3, oy0 - 2, oy1 + 2, -2, H + 2) - outer_shell()
        print('lanyard: D %.1f tunnel along the antenna end at z %.1f, out at both corners; walls: inner %.2f, front %.2f '
              '(flat end %.1f long); battery to boss %.2f; case length %.2f' % (lanyard_d, lanyard_z, tun.distance_to(inner),
              flat.distance_to(outside), oy1 - oy0 - 2 * plan_r, bx0 - (bat_x0 + bat_l), (base + lid_assembled).bounding_box().size.X))
    gaps = {n: min(base.distance_to(env[n]), lid_assembled.distance_to(env[n])) for n in (
        'mic pcb', 'mic pad joints', 'mic package', [k for k in env if k.startswith('mic caps')][0], 'sd pcb',
        'sd header joints', 'switch body', 'switch pins+joints', 'lever ON', 'lever OFF', 'usb-c', 'battery')}
    if 'lanyard_holes' in g:
        cord = env['lanyard cord D1.5']
        print('lanyard cord D1.5 (holes D%.1f at y %s, z %.2f): to battery top foam %.2f, SD board %.2f, lid (closed) %.2f, '
              'battery end %.2f; end wall between the holes %.2f thick x %.1f long' % (lanyard_hd, lanyard_holes, lz,
              cord.distance_to(env['top foam']), cord.distance_to(env['sd pcb']), cord.distance_to(lid_assembled),
              cord.distance_to(env['battery']), wall_e - inset_at(lz), lanyard_holes[1] - lanyard_holes[0] - lanyard_hd))
    print('smallest gaps to the case:', {k: round(v, 2) for k, v in gaps.items()})

    # clearances that matter
    print('mic top -> lid ceiling (earplug squeezed to):', round(lid_z - mic_top, 2),
          '| caps top -> ceiling:', round(lid_z - mb_top - cap_h, 2),
          '| pocket rim -> mic top:', pocket_clear, '| pocket outer R:', pocket_d / 2 + pocket_wall)
    print('lever: slot x %.2f..%.2f z %.2f..%.2f, lever cut length from body %.2f (tip %.1f past surface)' % (
        slot_x0, slot_x1, slot_z0, slot_z1, sw_y0 - tip_y, lever_proud))
    print('usb: receptacle z %.2f..%.2f, opening z %.2f..%.2f, front %.2f behind the counterbore floor (skin %.1f), top %.2f below battery' % (
        usb_zc - usb_h / 2, usb_zc + usb_h / 2, usb_zc - usb_hole_h / 2, usb_zc + usb_hole_h / 2,
        usb_front - g.get('usb_cb_x1', ix0 - usb_skin), usb_skin, bat_z0 - (usb_zc + usb_h / 2)))
    print('lip bottom z %.2f, parting z %.2f, detent z %.2f' % (lip_z0, z_p, z_det))

    # wall thickness: inner surface (cavity + wire groove) to outer surface (incl. reveal, counterbore, recess, dot)
    outer = outer_shell()
    ring = (outer - outer_shell(reveal_d)) & block(ox0 - 1, ox1 + 1, oy0 - 1, oy1 + 1, z_p - reveal_h / 2, z_p + reveal_h / 2)
    cb_x0 = ox0 - tool_extra
    skin = outer - ring - Pos(cb_x0, yc, usb_zc) * Rot(0, 90, 0) * extrude(
        RectangleRounded(usb_cb_h, usb_cb_w, usb_cb_r), amount=g.get('usb_cb_x1', ix0 - usb_skin) - cb_x0)
    ys = south_surface_y(sw_zc)
    rl, rh = slot_x1 - slot_x0 + 2 * recess_margin, slot_z1 - slot_z0 + 2 * recess_margin
    from build123d import SlotOverall
    skin -= Pos(sw_xc, ys + recess_depth, sw_zc) * Rot(90, 0, 0) * extrude(SlotOverall(rl, rh), amount=recess_depth + 1)
    faces = Compound(skin.faces())
    # base: cavity + groove up to the parting plane; lid: the lip's inner face above it (lip runs all round there)
    inner_base = (cavity_solid() + block(groove[0], groove[1], iy1 - 0.01, iy1 + groove[2], g.get('groove_z0', floor + floor_chamfer), sd_z0)) \
        & block(ox0, ox1, oy0, oy1, -1, z_p)
    li = fit + lip_t
    inner_lid = rounded_prism(ix0 + li, ix1 - li, iy0 + li, iy1 - li, 0.3, z_p, lid_z - z_p)
    pts = []
    for solid in (inner_base, inner_lid):
        for f in solid.faces():
            n = 9
            for u in range(n):
                for v in range(n):
                    try:
                        p = f.position_at(u / (n - 1), v / (n - 1))
                    except Exception:
                        continue
                    if f.distance_to(p) < 1e-3:      # planar faces: skip points of the untrimmed surface
                        pts.append(p)
    d = sorted((faces.distance_to(p), (round(p.X, 1), round(p.Y, 1), round(p.Z, 1))) for p in pts)
    zr = g.get('usb_rim', 0.6) + 0.15           # v4: points inside the USB frame are not cavity surface any more
    usb_zone = lambda p: p[0] < ix0 + 1.0 and abs(p[1] - yc) < usb_cb_w / 2 + zr and p[2] < usb_zc + usb_cb_h / 2 + zr
    if 'usb_rim' in g:
        print('usb frame: pocket %.1f x %.1f to x %.2f, plate %.2f thick, rim %.1f (south %.1f), receptacle %.1f proud of plate' % (
            usb_cb_w, usb_cb_h, usb_cb_x1, -pm_gap - usb_cb_x1, usb_rim, usb_rim_s, usb_cb_x1 - usb_front))
    print('min wall, USB skin (by design %.1f):' % usb_skin, [(round(a, 2), b) for a, b in d if usb_zone(b)][:2])
    print('min wall elsewhere:', [(round(a, 2), b) for a, b in d if not usb_zone(b)][:5])
    print('floor %.2f, lid plate %.2f (+crown %.1f), lip %.1f, pocket wall %.1f, posts SD Ø%.1f mic Ø%.1f' % (
        floor, H - lid_z, crown, lip_t, pocket_wall, post_d, g.get('mb_post_d', post_d)))
    print('size', [round(v, 2) for v in (base + lid_assembled).bounding_box().size],
          'base %.1f g lid %.1f g' % tuple(p.volume / 1000 * (1.24 if VARIANT == 'fdm' else 1.01) for p in (base, lid_assembled)))
