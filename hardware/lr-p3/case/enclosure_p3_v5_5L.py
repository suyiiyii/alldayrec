"""LR-P3 enclosure v5.5L, millimetres. Two parts: base and lid.
FDM 36.63 x 24.7 x 18.6, MJF 36.63 x 25.3 x 19.1. Designed for FDM at 0.12 mm layers.

- Lid lock: a lip inside the base wall plus two spring latches, one per long side at x 17: a tab cut in the base
  wall (two 0.6 through-slots, 1.2 thick, rooted at z 6) flexes outward while a 0.5 hook on the stiff lip slides
  past, then its top band drops over the hook (40-degree retention face, 0.35 engagement). Open with a fingernail in
  the notch at the antenna end.
- Clips: vertical crush bumps press the SD module and mic board edges (board_crush / mb_crush) so both boards snap
  into the lid and stay there when it is opened; the slide switch sits in a hood on the lid with crush bumps at its
  ends (sw_grip). Interference values were picked with a printed calibration coupon at 0.12 mm layers; printed at
  0.2 mm layers this version will be tight.
- Lanyard: two D1.8 holes through the antenna-end wall of the base, 10 apart, at z 13.05. Inside, the cord runs along
  the end wall in the free strip above the battery (top foam at z 12.1) and below the SD board / lid lip (z 14.0 /
  14.1): 1.9 tall, so cords up to ~1.5. The cord pulls the end wall between the holes outward: horizontal, along the
  layers of the upright-printed base.
- USB end: the USB-C receptacle is flush with the outer end face; a shallow pocket (open at the bottom, 45-degree
  lead-in) takes the plug overmould. The receptacle sits 0.5 deep in a floor pocket so the board stays at pm_lift.
- Mic: D1.6 sound hole with an outer chamfer above the mic port, a D5 earplug-foam pocket around it (5 x 5 x 2 piece,
  squeezed to foam_gap), cut where the 1206 caps would hit it.

Frame, seen from the lid: x along the long side, ProMicro PCB edge at the USB end is x=0, +x toward the antenna;
y across, ProMicro B+ edge y=0, B- edge y=pm_w (north); z from the bottom face up.
Stack, floor up: ProMicro component face DOWN (USB-C + LEDs toward the floor) -> kapton + foam ->
602030 -> soft foam -> SD module (socket down) beside the mic board (parts up) -> lid.
The SD module and the mic board hang from four lid posts, so mic-to-lid (= earplug foam squeeze) is set
by the lid alone. The lip is cut where the switch and the mic board come close to the wall.

Every size is a parameter. Values marked MEASURE are estimates, not calipered.
Build: P3=v5_5L python export_p3.py (STL/STEP), P3=v5_5L python check_p3.py fdm|mjf (fit checks).
"""
import math
from build123d import *

VARIANT = globals().get("VARIANT", "fdm")      # "fdm" (Bambu, lid face on the bed) or "mjf" (JLC PA12)

# ---- contents. MEASURE = not calipered yet ----
pm_l, pm_w, pm_t = 33.0, 18.0, 1.0   # ProMicro PCB; est 32.7 x 17.3, nominal 33 x 18, t 1.0-1.2. MEASURE
usb_h, usb_w, usb_len = 3.2, 8.94, 7.35  # USB-C receptacle height, width, length (standard part)
usb_below = 3.2            # v5.1: ordinary top-mount receptacle on the chip face: full height below the face
usb_sink = 0.5             # v5.1: the 3.2 receptacle sits in a 0.5 pocket in the floor (0.5 floor left under it)
pm_lift = usb_below - usb_sink   # 2.7: floor top to chip face, 1.7 under the parts for the wires (enough)
usb_overhang = 1.6         # receptacle front past the PCB edge (est). MEASURE
pm_parts_h = 1.0           # other parts on the component face (est)
leds = ((7.9, 15.2), (7.9, 3.7))   # the two 0603 LEDs on the component face
led_h = 0.6
antenna = (30.5, 32.3, 6.2, 13.0)  # chip antenna on the component face
back_stack = 0.9           # clipped joints 0.3 + kapton + 0.5 foam on the flat PCB back (as built)
bat_l, bat_w, bat_t = 31.0, 20.0, 6.2   # 602030 incl. protection board; t 6.2 measured
bat_x0 = 1.7               # v5.2: 0 -> 1.7; leads go down to B+/B- on the PCB back in the gap x 0..1.7
top_foam = 0.3             # soft foam on the battery (swell allowance)
front_dx = 2.65            # v5.2: 1.1 -> 2.65 (end wall 1.55 closer): mic board 1.2 off the wall; SD edge x 31.7
sd_w, sd_l, sd_t = 18.0, 18.1, 1.6       # SD module PCB (y, x, t); est. MEASURE
sd_x0 = 10.95 + front_dx
sock = (8.85 + front_dx, 21.05 + front_dx, 1.9, 16.0)   # card socket x0,x1,y0,y1 under the PCB (incl. mouth)
sock_h = 1.9               # socket height (est 1.9; module total ~3.5). MEASURE
card = (1.75 + front_dx, 8.85 + front_dx, 2.9, 13.9)   # protruding part of the TF card
card_t, card_mid = 0.8, 0.95             # card thickness, its mid-plane below the SD PCB underside (est)
sd_holes = ((24.65 + front_dx, 1.85), (24.65 + front_dx, 16.5))   # M2 (est ±0.5). MEASURE
sd_hdr_x = 27.85 + front_dx
mb_x0, mb_y0, mb_xl, mb_yl, mb_t = -1.6 + front_dx, 3.8, 12.55, 12.5, 1.6   # mic board (est ±0.1). MEASURE t
mb_holes = ((8.4 + front_dx, 5.9), (8.4 + front_dx, 14.4))   # M3, pitch 8.50 (est)
mic_port = (4.8 + front_dx, 9.9)      # WMM7040 sound port
mic_pkg = (3.6 + front_dx, 7.5 + front_dx, 8.3, 11.15)        # package (est); 4.0 x 3.0 x 1.0 datasheet
mic_h = 1.0
mic_caps = ((2.43 + front_dx, 5.58 + front_dx, 14.4, 15.8), (2.99 + front_dx, 6.13 + front_dx, 11.97, 13.46))  # 1206
mic_res = (3.26 + front_dx, 4.47 + front_dx, 5.3, 7.6)        # 103 resistor
cap_h = 1.6                # 1206 MLCC worst case; may be 0.85-1.25. MEASURE
res_h = 0.55
joint_h = 0.5              # solder joints on the top of the front-layer boards
m2_hole_d = 2.2            # SD module M2 holes (est). MEASURE
mb_hole_d = 3.2            # mic board M3 holes (est ~2.9-3.2). MEASURE
sw_l, sw_d, sw_h = 8.8, 3.8, 3.8         # switch body x, y, z (calipers ±0.1)
sw_x0 = 1.55               # v5.2: 0 -> 1.55 (end wall moved in; switch and hood rib clear of the R1.5 cavity corner)
lever_w, lever_t = 1.5, 1.5  # lever x, z. MEASURE t
lever_off = 1.1            # lever centre offset from body centre in each position (est). MEASURE
lever_proud = 0.4          # lever tip past the outer surface after trimming
sw_pins_half = 3.2          # v5.5: the 3 pin joints span sw_xc +- this (as the check envelope)

# ---- fits and shell ----
fit = 0.15                 # lip slide fit per side
lip_t = 1.0
lip_board_clear = 0.15     # lip inner face to the 18-wide boards
lip_below = 2.0            # lip reaches this far below the parting plane
detent_bump, detent_w, detent_ramp, detent_seat_clear = 0.3, 1.6, 0.4, 0.05
detents = ()               # v5.5: the v3 detents ((s 17, s 28, n 6, n 28), ~0.15 engagement) are replaced by latches
usb_wall_gap = 0.1         # receptacle front to the inner end-wall face
usb_skin = 0.6             # wall left around the USB opening (counterbore floor); receptacle 0.7 behind outside
usb_hole_w, usb_hole_h = 9.4, 4.6   # v4: 4.0 -> 4.6 (±0.7 on the receptacle height position)
usb_cb_w, usb_cb_h, usb_cb_r = 12.8, 7.4, 1.6   # v4: overmould pocket (plug overmould ~12.4 x 6.5: 0.2 / 0.45 per side)
usb_lead = 0.6             # v5: 45-degree lead-in round the pocket mouth
usb_proud = 0.6            # v4: receptacle front stands this far out of the pocket floor (plate)
usb_rim, usb_rim_s = 1.0, 0.6   # v4: frame wall around the in-cavity part of the pocket (south thinner: battery leads)
plug_w, plug_h, plug_gap = 12.4, 6.5, 0.0   # check envelope: overmould touching the receptacle face
end_gap = 0.3              # v5.1: 0.15 -> 0.3; ProMicro far end to the end wall (the stop for USB insertion)
pm_gap = 0.15             # v5.1: 0.1 -> 0.15; locating blocks / USB plate to the PCB edges
floor = 1.0                # v5: 1.2 -> 1.0
foam_gap = 1.0             # lid ceiling above the mic top = squeezed earplug thickness
lid_t = 1.0                # v5: 1.2 -> 1.0
wall_w, wall_e, wall_s, wall_n = 1.8, 1.55, 2.0, 2.1   # v5: east 2.0 -> 1.75, north 2.2 -> 2.1 (wire groove needs it); south kept (switch)
groove = (2.0, 25.0, 0.8)  # north wire groove x0, x1, depth
groove_z0 = 1.7            # v5: groove starts here (keeps 1.0 wall above the bottom rounding)
plan_r = 3.8                # v5.2: 4.0 -> 3.8 keeps 1.0 at the corners next to the thinner end wall
cav_r = 1.5
edge_rb, edge_rt = 2.0, 2.5     # bottom / top perimeter radii (45-degree start, as v4)
bulge = 0.3
reveal_h, reveal_d = 0.5, 0.3   # v5.3: 0.4 -> 0.3
floor_chamfer = 0.5
light_d = 1.0              # light points in the floor
tube_id, tube_wall = 1.6, 0.5   # light tube from the floor up to just under each LED (fill with clear UV resin)
pocket_d, pocket_wall = 5.0, 0.6   # earplug pocket around the mic hole; cut where taller parts sit
pocket_clear = 0.3         # pocket rim above the mic top
mic_hole_d, mic_hole_cs = 1.6, 0.3   # sound hole + 45-degree outer chamfer
post_d, shoulder_d = 1.9, 3.2        # SD module (M2)
mb_post_d, mb_shoulder_d = 2.9, 4.4  # mic board (M3)
sw_end_gap, sw_grip = 0.1, 0.25     # switch hood: end-rib gap, bump interference per end (0.12 mm calibration coupon: 25, the tightest level)
board_crush = 0.20                   # SD clip bumps press this far into each PCB edge (0.12 mm calibration coupon: 20)
mb_crush = 0.20                      # mic board clip bumps (0.12 mm calibration coupon: 20)
sd_w_real = 18.0                     # v5.5: MEASURED SD PCB width; moves only the SD clip crests (cavity stays on sd_w)
mb_w_real = 12.5                     # v5.5: MEASURED mic board N-S width; clip crests about the board centre line
sd_clip_x = (22.0, 26.0, 31.0)       # v5.5: + x 26 (clear of the M2 shoulders and the latch hook)
sd_relief_w = 1.2                    # v5.5: lip outer face relieved board_crush deep, +-this round each SD bump
mb_clip_x = (5.5, 7.4, 9.3)          # v5.5: + x 9.3 (clear of the north M3 shoulder); rib x 4.5-9.8
mb_bar_clip_x = (2.1, 5.95, 9.8)     # v5.5: + x 5.95 (starts at the bar bottom, above the centre pin joint)
latch_x = 17.0                       # v5.5 lid lock: one spring latch on each long side, at mid length
latch_w, latch_slot = 5.0, 0.6       # tab width between the two through-slots (FDM keeps a 0.6 slot open)
latch_z0 = 6.0                       # tab root (slots end here; above the board, battery side is clear)
latch_t = 1.2                        # tab thickness (inner face recessed: south 0.8, north 0.9 incl. the wire groove)
latch_bump, latch_bump_w = 0.5, 3.5  # hook on the lip outer face: projection (0.35 past the base wall), width
latch_lead, latch_flat = 0.7, 0.1    # hook lead-in height (bottom face), flat
latch_angle = 40.0                   # retention face from horizontal (smaller = harder to pull off; <17 self-locks)
latch_seat = 0.05                    # hook top to the tab's catch face when closed
notch_w, notch_h, notch_d = 6.0, 0.8, 0.8   # fingernail notch in the lid edge at the antenna end (lid wall left 1.75)
lanyard_holes = (4.0, 14.0)          # v5.5L: hole y (end-face centre yc 9 +- 5, inside the flat part 0.5-17.6)
lanyard_hd = 1.8                     # hole diameter (cord up to ~1.5)
lanyard_cs = 0.4                     # 45-degree flare inside and outside (the cord bends round the inner edge)
tongue_gap = 0.1                     # lid tongue to the lever-notch sides
slot_clear, slot_clear_z = 0.4, 0.3
recess_margin, recess_depth = 0.7, 0.6
dot_d, dot_depth = 0.8, 0.4
sw_on_side = -1            # -1: lever toward the USB end = ON (dot there); +1: the other end
crown = 0.0
if VARIANT == "mjf":
    # JLC MJF PA12 (as v4): wall >=1.0, clips >=1.5 preferred, emboss/deboss >=0.8, ±0.3, small holes shrink
    fit = 0.25
    lip_t = 1.2
    detent_bump = 0.5
    light_d = 1.2
    reveal_h, reveal_d = 0.8, 0.4   # v5.3: 0.5 -> 0.4
    pocket_wall = 0.8
    sw_end_gap, sw_grip = 0.15, 0.15
    board_crush = mb_crush = 0.15
    latch_bump = 0.6                 # fit 0.25: same 0.35 engagement
    tongue_gap = 0.2
    dot_d, dot_depth = 1.0, 0.8
    crown = 0.5
tool_extra = 1.0
print_spacing = 10.0

# ---- derived ----
yc = pm_w / 2
sw_crush = sw_end_gap + sw_grip            # switch hood bump radius
pm_face = floor + pm_lift                  # component face (down)
pm_top = pm_face + pm_t
bat_z0 = pm_top + back_stack
bat_z1 = bat_z0 + bat_t
front_z0 = bat_z1 + top_foam
sd_z0 = front_z0 + sock_h                  # SD + mic PCB underside (coplanar)
board_top = sd_z0 + sd_t
mb_top = sd_z0 + mb_t
mic_top = mb_top + mic_h
lid_z = mic_top + foam_gap
H = lid_z + lid_t
z_p = H - edge_rt                          # parting plane
lip_z0 = z_p - lip_below
z_det = z_p - lip_below / 2
usb_z0 = pm_face - usb_below               # receptacle underside (v5.1: floor - usb_sink)
usb_zc = usb_z0 + usb_h / 2
usb_front = -usb_overhang
usb_cb_x1 = usb_front + usb_proud          # v4: pocket floor = plate outer face
side_gap = fit + lip_t + lip_board_clear
ix0, ix1 = -pm_gap, pm_l + end_gap          # v5.2: end wall inner face at the PCB edge stop
lead_zone = (0.0, bat_x0, 0.0, 2.2)       # check: battery leads drop to B+ here (B- mirrored at the north)
iy0, iy1 = yc - sd_w / 2 - side_gap, yc + sd_w / 2 + side_gap
assert bat_w / 2 + 0.15 <= sd_w / 2 + side_gap, "battery does not fit the cavity width"
iw, il = ix1 - ix0, iy1 - iy0
cx, cy = (ix0 + ix1) / 2, (iy0 + iy1) / 2
ox0, ox1, oy0, oy1 = ix0 - wall_w, ix1 + wall_e, iy0 - wall_s, iy1 + wall_n
ocx, ocy = (ox0 + ox1) / 2, (oy0 + oy1) / 2
hx, hy = (ox1 - ox0) / 2, (oy1 - oy0) / 2
sw_x1 = sw_x0 + sw_l
sw_xc = (sw_x0 + sw_x1) / 2
sw_y0, sw_y1 = iy0, iy0 + sw_d             # body against the south wall
sw_z0 = front_z0
sw_zc = sw_z0 + sw_h / 2
slot_x0 = sw_xc - lever_off - lever_w / 2 - slot_clear
slot_x1 = sw_xc + lever_off + lever_w / 2 + slot_clear
slot_z0, slot_z1 = sw_zc - lever_t / 2 - slot_clear_z, sw_zc + lever_t / 2 + slot_clear_z
recess_l, recess_h = slot_x1 - slot_x0 + 2 * recess_margin, slot_z1 - slot_z0 + 2 * recess_margin


def block(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def rounded_prism(x0, x1, y0, y1, radius, z0, height):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, z0) * extrude(RectangleRounded(x1 - x0, y1 - y0, radius), amount=height)


def bore(x, y, diameter, z0, height):
    return Pos(x, y, z0) * Cylinder(diameter / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN))


def ramp(x, y, z, sign, projection, width, half_height):
    # Triangular prism on a y-facing wall, <=45-degree slopes (v4).
    vertices = [(x - width / 2, y, z - half_height), (x - width / 2, y + sign * projection, z),
                (x - width / 2, y, z + half_height)]
    return extrude(Face(Wire.make_polygon(vertices, close=True)), amount=width, dir=(1, 0, 0))


# ---- v4 pebble shell ----
def prism_yz(x0, x1, pts):
    # v5.5: (y, z) polygon extruded along x from x0 to x1
    return extrude(Face(Wire.make_polygon([(x0, y, z) for y, z in pts], close=True)), amount=x1 - x0, dir=(1, 0, 0))


def _edge(d, R):
    a = R * (1 - 1 / 2 ** 0.5)
    if d >= R:
        return 0.0
    if d <= a:
        return 2 * a - d
    return R - (R * R - (R - d) ** 2) ** 0.5


def inset_at(z, extra=0.0):
    return _edge(z, edge_rb) + _edge(H - z, edge_rt) + bulge * ((z - H / 2) / (H / 2)) ** 2 + extra


# v5.2: west wall thickness so the outer end face meets the receptacle front at the receptacle centre height
wall_w = ix0 - (usb_front - inset_at(usb_zc))
ox0 = ix0 - wall_w
ocx = (ox0 + ox1) / 2
hx = (ox1 - ox0) / 2
usb_cb_x1 = usb_front + 0.1                # overmould face cut 0.1 behind the receptacle front (check uses it)


def _zs():
    out = set()
    for R, flip in ((edge_rb, False), (edge_rt, True)):
        a = R * (1 - 1 / 2 ** 0.5)
        for d in [0, a / 2, a] + [a + (R - a) * k / 5 for k in range(1, 6)]:
            out.add(round(H - d if flip else d, 5))
    for k in range(1, 6):
        out.add(round(edge_rb + (H - edge_rt - edge_rb) * k / 6, 5))
    return sorted(out)


def _sec(z, i):
    return Pos(ocx, ocy, z) * RectangleRounded(2 * (hx - i), 2 * (hy - i), max(plan_r - i, 0.5))


def outer_shell(extra=0.0):
    body = loft([_sec(z, inset_at(z, extra)) for z in _zs()])
    if crown > 0:
        import math
        i0 = inset_at(H, extra)
        t_max = hy - 1.5
        caps = [_sec(H - 0.05 + (crown + 0.05) * math.sin(math.pi / 2 * u) ** 2, i0 + (t_max - i0) * u)
                for u in [k / 8 for k in range(9)]]
        body = body + loft(caps)
    return body


def south_surface_y(z):
    return oy0 + inset_at(z)


def recess_tool():
    # pill-shaped recess around the lever slot on the south face
    return Pos(sw_xc, south_surface_y(sw_zc) + recess_depth, sw_zc) * Rot(90, 0, 0) * extrude(
        SlotOverall(recess_l, recess_h), amount=recess_depth + tool_extra)


def cavity_solid():
    cav = rounded_prism(ix0, ix1, iy0, iy1, cav_r, floor, lid_z - floor)
    cav = chamfer([e for e in cav.edges() if abs(e.center().Z - floor) < 1e-6], floor_chamfer)
    # flat floor under the USB receptacle (it rests on the floor right at the end wall)
    cav += block(ix0 - 0.01, ix0 + 1.0, yc - usb_hole_w / 2 - 0.6, yc + usb_hole_w / 2 + 0.6, floor, floor + 1.0)
    return cav


def make_parts():
    outer = outer_shell()
    ring = (outer - outer_shell(reveal_d)) & block(ox0 - 1, ox1 + 1, oy0 - 1, oy1 + 1,
                                                    z_p - reveal_h / 2, z_p + reveal_h / 2)
    shell = outer - cavity_solid() - ring
    below = block(ox0 - 1, ox1 + 1, oy0 - 1, oy1 + 1, -1, z_p)
    base = shell & below
    lid = shell - below

    # ---- base: ProMicro location (sides only; wires go into holes 0.8 mm from the edges from below) ----
    rail_top = pm_top + 0.5
    base += block(26.0, 30.0, iy0 - 0.01, -pm_gap, floor - 0.01, rail_top)          # v5: south block (was a rail)
    base += block(26.0, 30.0, pm_w + pm_gap, iy1 + 0.01, floor - 0.01, rail_top)    # north block
    for y0, y1 in ((2.5, 4.4), (pm_w - 4.4, pm_w - 2.5)):                           # antenna-end supports
        base += block(30.0, 32.5, y0, y1, floor - 0.01, pm_face)
    # v3: pad under the receptacle (it no longer reaches the floor); carries the USB end of the board
    # v5.1: floor pocket for the receptacle (0.5 deep, 0.25 clearance round it)
    base -= block(usb_cb_x1, usb_front + usb_len + 0.3, yc - usb_w / 2 - 0.25, yc + usb_w / 2 + 0.25,
                  usb_z0, floor + 0.01)
    if usb_z0 - 0.1 > floor + 0.3:          # pad only when the receptacle does not reach the floor
        base += block(usb_front + 0.1, usb_front + usb_len - 0.6, yc - usb_w / 2 + 0.4, yc + usb_w / 2 - 0.4,
                      floor - 0.01, usb_z0 - 0.1)
    for x, y in leds:                                                               # light tubes
        tube_top = pm_face - led_h - 0.2
        base += bore(x, y, tube_id + 2 * tube_wall, floor - 0.01, tube_top - floor + 0.01)
        base -= bore(x, y, tube_id, floor, tube_top - floor + 0.01)
        base -= bore(x, y, light_d, -tool_extra, floor + tool_extra + 0.01)
    # north wire groove (wires climb the north wall to the front layer)
    base -= block(groove[0], groove[1], iy1 - 0.01, iy1 + groove[2], groove_z0, sd_z0)
    # USB-C: opening + counterbore for the plug overmould
    # v5.2: flush port. Hole through the end wall (open at the bottom, the receptacle sits in the floor pocket);
    # the end wall's inner face is the unplug stop. A shallow face for the overmould, open at the bottom.
    cb_x0 = ox0 - tool_extra
    base -= block(cb_x0, ix0 + 0.01, yc - usb_hole_w / 2, yc + usb_hole_w / 2, -tool_extra, usb_zc + usb_hole_h / 2)
    base -= Pos(cb_x0, yc, usb_zc) * Rot(0, 90, 0) * extrude(RectangleRounded(usb_cb_h, usb_cb_w, usb_cb_r),
                                                            amount=usb_cb_x1 - cb_x0)
    base -= block(cb_x0, usb_cb_x1, yc - usb_cb_w / 2, yc + usb_cb_w / 2, -tool_extra, usb_zc)
    # switch lever slot, a shallow pill recess around it, ON dot under the ON lever position
    # v2: the slot is a notch open to the parting plane; the lid tongue fills it above the lever
    base -= block(slot_x0, slot_x1, oy0 - tool_extra, iy0 + 0.01, slot_z0, z_p + 0.01)
    base -= recess_tool()
    x_on = sw_xc + sw_on_side * lever_off
    z_dot = sw_zc - recess_h / 2 - 0.9
    base -= Pos(x_on, south_surface_y(z_dot) + dot_depth, z_dot) * Rot(90, 0, 0) * Cylinder(
        dot_d / 2, dot_depth + tool_extra, align=(Align.CENTER, Align.CENTER, Align.MIN))

    # v5.5L lanyard holes through the antenna-end wall; the cord runs inside along the wall above the battery
    lanyard_z = (front_z0 + min(sd_z0, z_p - lip_below)) / 2      # middle of the free strip (12.1 .. 14.0)
    x_face = ox1 - inset_at(lanyard_z)
    for yh in lanyard_holes:
        base -= Pos(ix1 - 0.5, yh, lanyard_z) * Rot(0, 90, 0) * Cylinder(lanyard_hd / 2, wall_e + 2,
                                                                          align=(Align.CENTER, Align.CENTER, Align.MIN))
        h_ = lanyard_cs + 0.3
        for xf, s_ in ((x_face, 1), (ix1, -1)):
            base -= Pos(xf, yh, lanyard_z) * Rot(0, 90 * s_, 0) * Cone(lanyard_hd / 2, lanyard_hd / 2 + h_, h_,
                                                                      align=(Align.CENTER, Align.CENTER, Align.CENTER))

    # ---- lid: v3 lip + detents, cut where the switch and the mic board sit near the wall ----
    lip = rounded_prism(ix0 + fit, ix1 - fit, iy0 + fit, iy1 - fit, cav_r - fit, lip_z0, lid_z - lip_z0)
    lip -= rounded_prism(ix0 + fit + lip_t, ix1 - fit - lip_t, iy0 + fit + lip_t, iy1 - fit - lip_t, 0.3,
                         lip_z0 - tool_extra, lid_z - lip_z0 + 2 * tool_extra)
    lip_in = fit + lip_t + 0.01
    lip -= block(ix0 + lip_in, sw_x1 + 0.15, iy0 - tool_extra, iy0 + lip_in, lip_z0 - tool_extra, z_p)
    lip -= block(ix0 - tool_extra, ix0 + lip_in, sw_y1 + 0.1, mb_y0 + mb_yl + 0.2, lip_z0 - tool_extra, z_p)
    lip += rounded_prism(ix0, ix1, iy0, iy1, cav_r, z_p, lid_z - z_p) - rounded_prism(
        ix0 + fit + 0.01, ix1 - fit - 0.01, iy0 + fit + 0.01, iy1 - fit - 0.01, cav_r, z_p - 0.1, lid_z - z_p + 0.2)
    for side, x in detents:
        sign, y = (-1, iy0 + fit) if side == "s" else (1, iy1 - fit)
        lip += ramp(x, y, z_det, sign, detent_bump, detent_w, detent_ramp)
        base -= ramp(x, y, z_det, sign, detent_bump + detent_seat_clear,
                     detent_w + 2 * detent_seat_clear, detent_ramp + detent_seat_clear)
    # v5.5 lid lock: a spring tab in each long base wall (two through-slots, inner face recessed to latch_t) flexes
    # OUTWARD while a hook on the lip (stiff: the SD board sits 0.15 inside it) slides past, then the tab's top band
    # drops over the hook. u = distance inward from the base wall plane.
    tana = math.tan(math.radians(latch_angle))
    zf = lip_z0 + latch_lead
    zt = zf + latch_flat
    ceil = lambda u: zt + latch_seat + (u - (fit - latch_bump)) * tana   # catch face, parallel to the hook top
    for sign, y_wall, depth in ((1, iy0, wall_s - latch_t), (-1, iy1, wall_n - latch_t)):
        yz = lambda pts: [(y_wall + sign * u, z) for u, z in pts]
        lip += prism_yz(latch_x - latch_bump_w / 2, latch_x + latch_bump_w / 2, yz([
            (fit + 0.3, lip_z0), (fit, lip_z0), (fit - latch_bump, zf), (fit - latch_bump, zt),
            (fit, zt + latch_bump * tana), (fit + 0.3, zt + latch_bump * tana)]))
        xa, xb = latch_x - latch_w / 2 - latch_slot, latch_x + latch_w / 2 + latch_slot
        base -= prism_yz(xa, xb, yz([(0.01, latch_z0 - depth), (-depth, latch_z0), (-depth, ceil(-depth)),
                                     (0.01, ceil(0.01))]))
        base -= prism_yz(latch_x - latch_w / 2, latch_x + latch_w / 2,
                         yz([(0.01, z_p - 0.3), (0.01, z_p + 0.01), (-0.31, z_p + 0.01)]))   # lead-in chamfer
        for x0 in (xa, latch_x + latch_w / 2):
            ya, yb = y_wall + sign * 0.01, y_wall - sign * (max(wall_s, wall_n) + 1.0)
            base -= block(x0, x0 + latch_slot, min(ya, yb), max(ya, yb), latch_z0, z_p + 0.01)
    lid += lip

    # posts with shoulders: SD module and mic board hang from the lid (post shorter than the PCB)
    for holes, top, pd, sd in ((sd_holes, board_top, post_d, shoulder_d), (mb_holes, mb_top, mb_post_d, mb_shoulder_d)):
        for x, y in holes:
            lid += bore(x, y, sd, top, lid_z - top + 0.01)
            lid += bore(x, y, pd, sd_z0 + 0.1, top - sd_z0 - 0.1 + 0.01)
    # earplug pocket around the mic hole; the ring is cut where a part on the mic board reaches its rim
    pocket_z0 = mic_top + pocket_clear
    pocket = bore(*mic_port, pocket_d + 2 * pocket_wall, pocket_z0, lid_z - pocket_z0 + 0.01)
    pocket -= bore(*mic_port, pocket_d, pocket_z0 - tool_extra, lid_z - pocket_z0 + tool_extra)
    for (x0, x1, y0, y1), h in [(c, cap_h) for c in mic_caps] + [(mic_res, res_h)]:
        if mb_top + h + 0.2 > pocket_z0:
            pocket -= block(x0 - 0.3, x1 + 0.3, y0 - 0.3, y1 + 0.3, pocket_z0 - tool_extra, lid_z - 0.01)
    lid += pocket
    # switch hood: top plate, end ribs with crush bumps, bar behind the body above the pin joints
    lid += block(sw_x0 + 0.3, sw_x1 - 0.3, iy0 + fit, sw_y1, sw_z0 + sw_h + 0.1, lid_z + 0.01)
    rib_z0 = sw_z0 + 0.3
    for x0, x1, sgn in ((sw_x0 - sw_end_gap - 0.6, sw_x0 - sw_end_gap, 1), (sw_x1 + sw_end_gap, sw_x1 + sw_end_gap + 1.0, -1)):
        lid += block(x0, x1, iy0 + fit, sw_y1 - 0.1, rib_z0, lid_z + 0.01)
        xf = x1 if sgn > 0 else x0                  # rib face toward the switch
        lid += Pos(xf, (iy0 + sw_y1) / 2, rib_z0) * Cylinder(sw_crush, z_p - rib_z0,
                                                              align=(Align.CENTER, Align.CENTER, Align.MIN)) \
            & block(min(xf, xf + sgn), max(xf, xf + sgn), iy0, sw_y1, rib_z0, z_p)
    lid += block(sw_x0 + 0.1, sw_x1 - 0.1, sw_y1 + 0.05, sw_y1 + 1.0, sw_zc + 0.6, lid_z + 0.01)
    # v5.4: board clips (crush bumps, like the switch hood) so the SD module and the mic board stay on the lid
    def bump(x, y_face, sign, p, z0, z1):
        return Pos(x, y_face, z0) * Cylinder(p, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN)) \
            & block(x - p, x + p, min(y_face, y_face + sign * p), max(y_face, y_face + sign * p), z0, z1)
    clip_z0 = sd_z0 + 0.1                              # above the socket / wire band, below the PCB top
    lip_in = fit + lip_t
    sd_s, sd_n = yc - sd_w_real / 2 + board_crush, yc + sd_w_real / 2 - board_crush   # v5.5: crest lines
    mb_c = mb_y0 + mb_yl / 2
    mb_s, mb_n = mb_c - mb_w_real / 2 + mb_crush, mb_c + mb_w_real / 2 - mb_crush
    mb_y1 = mb_y0 + mb_yl
    rib_y0 = mb_y1 + 0.15                              # mic board: north rib with bumps
    bar_y = sw_y1 + 1.0                                # mic board: bumps on the switch hood's back bar
    assert min(sd_s - (iy0 + lip_in), (iy1 - lip_in) - sd_n, rib_y0 - mb_n, mb_s - bar_y) > 0.05, \
        "measured board too wide for the clip faces: widen the cavity (sd_w / mb_yl) instead"
    for x in sd_clip_x:                                # SD: on the south and north lips
        lid += bump(x, iy0 + lip_in, 1, sd_s - (iy0 + lip_in), clip_z0, z_p)
        lid += bump(x, iy1 - lip_in, -1, (iy1 - lip_in) - sd_n, clip_z0, z_p)
        for y0, y1 in ((iy0 + fit - 0.1, iy0 + fit + board_crush), (iy1 - fit - board_crush, iy1 - fit + 0.1)):
            lid -= block(x - sd_relief_w, x + sd_relief_w, y0, y1, lip_z0 - tool_extra, z_p)   # v5.5: lip relief
    lid += block(min(mb_clip_x) - 1.0, max(mb_clip_x) + 0.5, rib_y0, rib_y0 + 0.8, clip_z0, lid_z + 0.01)
    for x in mb_clip_x:
        lid += bump(x, rib_y0, -1, rib_y0 - mb_n, clip_z0, lid_z)
    for x in mb_bar_clip_x:                            # v5.5: bumps over the pin joints start at the bar bottom
        p = mb_s - bar_y
        lid += bump(x, bar_y, 1, p, clip_z0 if abs(x - sw_xc) - p >= sw_pins_half + 0.1 else sw_zc + 0.6, lid_z)
    # tongue: the wall above the lever notch, outer face incl. the recess, hanging from the lid
    tongue = (outer - cavity_solid() - ring - recess_tool()) & block(
        slot_x0 + tongue_gap, slot_x1 - tongue_gap, oy0 - tool_extra, iy0 + 0.01, slot_z1, z_p + 0.05)
    lid += tongue

    top = H + crown + tool_extra
    # v5.5: fingernail notch in the lid edge at the antenna end (pry here; the latches cam out)
    x_out = ox1 - inset_at(z_p)
    lid -= block(x_out - notch_d, ox1 + tool_extra, yc - notch_w / 2, yc + notch_w / 2, z_p - 0.01, z_p + notch_h)
    lid -= bore(*mic_port, mic_hole_d, lid_z - 0.5, top - lid_z + 0.5)
    lid -= Pos(*mic_port, H + crown + 0.01) * Cone(mic_hole_d / 2, mic_hole_d / 2 + mic_hole_cs + 0.01,
                                                   mic_hole_cs + 0.01, align=(Align.CENTER, Align.CENTER, Align.MAX))
    return base, lid


def print_lid(shape):
    # FDM: lid face down. MJF needs no orientation.
    return shape.rotate(Axis.X, 180).translate((0, 2 * oy1 + print_spacing, H + crown))


base, lid_assembled = make_parts()
lid = print_lid(lid_assembled)
if "show_object" in globals():
    show_object(base, id="base", options={"color": "steelblue"})
    show_object(lid, id="lid", options={"color": "orange"})
