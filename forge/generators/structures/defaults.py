"""Default rules: the sizes nobody states.

"Dining chair, seat 420 by 420, seat height 450, back height 900" says nothing
about how thick the legs are. Each function here answers one such question.

THESE ARE OUR OWN DESIGN CHOICES. They are not taken from any furniture
standard or catalogue; they were picked so that the results look like ordinary
wooden furniture, and they are listed in README.md so that they can be reviewed and
changed. Changing a rule changes every structure generated afterwards (the
generator version in each row changes with it).

Every rule takes numbers and returns a number in millimetres (or a count), and
its docstring says the rule in one line. The README table is checked against
this file by a test, so a rule cannot be added here without being listed there.
"""

from __future__ import annotations

from forge.generators.families._common import floor_to


def round_to(value: float, step: float) -> float:
    """Nearest multiple of `step`; halves round up, so the result never surprises."""
    return float(int(value / step + 0.5) * step)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --- boards ---------------------------------------------------------------------------------

def top_thickness(span: float) -> float:
    """Seat or table top: 20 if its longer side is under 400, 30 under 1200, else 40."""
    return 20.0 if span < 400 else 30.0 if span < 1200 else 40.0


def board_thickness(span: float) -> float:
    """Panel, shelf or side board: 18 if the unit's longest side is under 1000, else 25."""
    return 18.0 if span < 1000 else 25.0


def thin_panel_thickness(span: float) -> float:
    """Back panel of a shelf unit: 6 if the unit's longest side is under 1000, else 10."""
    return 6.0 if span < 1000 else 10.0


# --- legs and the rails between them --------------------------------------------------------

def leg_thickness(width: float, depth: float) -> float:
    """Leg section from the mean side m of the top: 30 (m<350), 40 (<600), 50 (<1100),
    60 (<1500), else 80."""
    mean = (width + depth) / 2
    return (30.0 if mean < 350 else 40.0 if mean < 600 else 50.0 if mean < 1100
            else 60.0 if mean < 1500 else 80.0)


def rail_thickness(leg: float) -> float:
    """Aprons, stretchers and back rails are half as thick as the leg."""
    return leg / 2


def apron_height(leg: float) -> float:
    """An apron is one and a half leg thicknesses tall."""
    return 1.5 * leg


def stretcher_height(leg: float) -> float:
    """A stretcher is as tall as the leg is thick."""
    return float(leg)


def stretcher_top(leg_length: float) -> float:
    """Upper face of the stretchers: 35% of the leg length above the floor, to the nearest 10."""
    return round_to(0.35 * leg_length, 10)


def round_leg_spread(diameter: float) -> float:
    """Four legs under a round top stand in a square 0.7 of the top's diameter, down to 10."""
    return floor_to(0.7 * diameter, 10)


def leg_circle(diameter: float, leg: float) -> float:
    """Three legs under a round top stand on a circle as wide as keeps them under the top:
    top diameter - 1.5 x leg thickness, down to 10."""
    return floor_to(diameter - 1.5 * leg, 10)


# --- backs and arms -------------------------------------------------------------------------

def top_rail_height(post: float) -> float:
    """The top rail of a back is two post thicknesses tall."""
    return 2.0 * post


def lower_rail_height(post: float) -> float:
    """The lower rail of a back is one post thickness tall."""
    return float(post)


def lower_rail_top(seat_top: float, back_top: float, rail_height: float) -> float:
    """The lower rail starts a quarter of the back's rise above the seat (to the nearest 10)."""
    return seat_top + round_to(0.25 * (back_top - seat_top), 10) + rail_height


def back_panel_height(seat_top: float, back_top: float) -> float:
    """A solid back panel covers the top 60% of the back's rise (to the nearest 10)."""
    return round_to(0.6 * (back_top - seat_top), 10)


def slat_count(clear_width: float) -> int:
    """One back slat per 110 of clear width between the posts, at least 2 and at most 9."""
    return int(_clamp(round_to(clear_width / 110, 1), 2, 9))


def slat_width(post: float) -> float:
    """A back slat is as wide as the post is thick."""
    return float(post)


def slat_thickness(rail: float) -> float:
    """A back slat is 0.6 of the rail's thickness."""
    return round_to(0.6 * rail, 1)


def arm_post_top(seat_top: float, back_top: float) -> float:
    """Arm posts rise 45% of the back's rise above the seat (down to 10)."""
    return seat_top + floor_to(0.45 * (back_top - seat_top), 10)


def arm_width(post: float) -> float:
    """An armrest is as wide as the post is thick."""
    return float(post)


def arm_height(post: float) -> float:
    """An armrest is half a post thickness tall."""
    return post / 2


# --- shelf units, desks ---------------------------------------------------------------------

def shelf_count(height: float) -> int:
    """Shelves in a shelf unit: one per 350 of height plus one, at least 2 and at most 7."""
    return int(_clamp(round_to(height / 350, 1) + 1, 2, 7))


def plinth_height(height: float) -> float:
    """Plinth under a shelf unit: 60 if the unit is under 1200 tall, else 80."""
    return 60.0 if height < 1200 else 80.0


def lower_shelf_top(height: float) -> float:
    """Upper face of a lower shelf between panel ends: 30% of the height (to the nearest 10)."""
    return round_to(0.3 * height, 10)


def modesty_height(height: float) -> float:
    """A desk's modesty panel hangs 45% of the desk's height (to the nearest 10)."""
    return round_to(0.45 * height, 10)


def pedestal_width(desk_width: float) -> float:
    """A desk pedestal is 28% of the desk's width (to the nearest 10), from 300 to 450."""
    return _clamp(round_to(0.28 * desk_width, 10), 300, 450)


def inner_panels_spread(desk_width: float, thickness: float) -> float:
    """With a pedestal at each end, the two inner panels leave the middle open: their
    pedestal-side faces are desk width - 2 x pedestal width + 2 x panel thickness apart."""
    return desk_width - 2 * pedestal_width(desk_width) + 2 * thickness


def inner_panel_x(desk_width: float, thickness: float, side: int) -> float:
    """Centre of the one inner panel of a single pedestal: a pedestal width in from the
    desk's end (side -1 is the left end, +1 the right)."""
    return side * (desk_width / 2 - pedestal_width(desk_width) + thickness / 2)


def pedestal_shelf_count() -> int:
    """A desk pedestal has 3 shelves."""
    return 3


def pedestal_floor_gap() -> float:
    """The lowest pedestal shelf is 50 above the floor."""
    return 50.0


# --- frames and crates ----------------------------------------------------------------------

def bar_size(span: float) -> float:
    """Square frame bar: 20 if the frame's longest side is under 500, 30 under 1000,
    40 under 1500, else 50."""
    return 20.0 if span < 500 else 30.0 if span < 1000 else 40.0 if span < 1500 else 50.0


def foot_length(height: float) -> float:
    """Feet of an upright frame: 30% of its height (to the nearest 10), at least 200."""
    return max(200.0, round_to(0.3 * height, 10))


def crate_board_thickness(span: float) -> float:
    """Crate boards: 12 if the crate's longest side is under 450, 15 under 650, else 18."""
    return 12.0 if span < 450 else 15.0 if span < 650 else 18.0


def crate_post_thickness(span: float) -> float:
    """Corner posts of a slatted crate: 30 if its longest side is under 450, else 40."""
    return 30.0 if span < 450 else 40.0


def crate_slat_count(wall_height: float) -> int:
    """Slats up each side of a crate: one per 110 of wall height, at least 2 and at most 5."""
    return int(_clamp(round_to(wall_height / 110, 1), 2, 5))


def crate_slat_height(wall_height: float, count: int) -> float:
    """Crate slats cover three quarters of the wall: 0.75 x wall / count, down to 5."""
    return floor_to(0.75 * wall_height / count, 5)


def frame_foot_height(bar: float) -> float:
    """A short foot under each corner of a flat frame is three bar sizes tall."""
    return 3.0 * bar


def mid_rail_top(height: float) -> float:
    """Upper face of a frame's mid rail: half the frame's height (to the nearest 10)."""
    return round_to(0.5 * height, 10)


def skid_count(width: float) -> int:
    """Skids under a crate: 2 if it is under 600 long, else 3."""
    return 2 if width < 600 else 3


def skid_height(span: float) -> float:
    """Crate skids: 30 tall if the crate's longest side is under 450, else 40."""
    return 30.0 if span < 450 else 40.0


def cleat_height(span: float) -> float:
    """A cleat handle on a crate's end: 30 tall if its longest side is under 450, else 40."""
    return 30.0 if span < 450 else 40.0


def cleat_length(depth: float) -> float:
    """A cleat handle runs 60% of the crate's width (to the nearest 10)."""
    return round_to(0.6 * depth, 10)


# --- doors and drawer fronts ----------------------------------------------------------------

def door_count(width: float) -> int:
    """Doors across an opening: 1 if it is under 500 wide, else a pair."""
    return 1 if width < 500 else 2


def drawer_count(opening: float) -> int:
    """Drawers up the front of a chest: one per 200 of opening height, at least 2 and at most 6."""
    return int(_clamp(round_to(opening / 200, 1), 2, 6))


# --- cabinets and chests --------------------------------------------------------------------

def cabinet_leg_thickness(width: float) -> float:
    """Short legs under a cabinet or chest: 40 square if it is under 900 wide, else 50."""
    return 40.0 if width < 900 else 50.0


def cabinet_leg_height(height: float) -> float:
    """Legs under a cabinet or chest: 100 tall if the unit is under 1200 tall, else 150."""
    return 100.0 if height < 1200 else 150.0


def cabinet_shelf_count(opening: float) -> int:
    """Shelves inside a cabinet: one per 350 of opening height less one, at least 1 and at
    most 5."""
    return int(_clamp(round_to(opening / 350, 1) - 1, 1, 5))


# --- beds -----------------------------------------------------------------------------------

def bed_slat_count(length: float) -> int:
    """Bed slats: one per 160 of the length they cover, at least 6 and at most 16."""
    return int(_clamp(round_to(length / 160, 1), 6, 16))


def bed_slat_width(width: float) -> float:
    """A bed slat is 70 wide if the bed is under 1200 wide, else 90."""
    return 70.0 if width < 1200 else 90.0


def bed_slat_thickness(width: float) -> float:
    """A bed slat is 20 thick if the bed is under 1200 wide, else 25."""
    return 20.0 if width < 1200 else 25.0


def footboard_top(deck_top: float, headboard_rise: float) -> float:
    """A footboard rises 40% as far above the bed's deck as the headboard does (to the
    nearest 10), at least 100."""
    return deck_top + max(100.0, round_to(0.4 * headboard_rise, 10))


def headboard_rail_count(rise: float) -> int:
    """Rails in an open headboard: one per 170 of its rise above the deck, at least 2 and
    at most 4."""
    return int(_clamp(round_to(rise / 170, 1), 2, 4))


def headboard_rail_bottom(deck_top: float, rise: float) -> float:
    """The lowest headboard rail starts 35% of the headboard's rise above the deck (to the
    nearest 10): above where the mattress ends."""
    return deck_top + round_to(0.35 * rise, 10)


# --- workbenches ----------------------------------------------------------------------------

def back_board_height(depth: float) -> float:
    """The board along the back of a workbench top is a quarter of the top's depth tall
    (to the nearest 10)."""
    return round_to(0.25 * depth, 10)


# --- ladders --------------------------------------------------------------------------------

def stile_thickness(height: float) -> float:
    """A ladder's side rail is 25 thick if the ladder is under 2500 tall, else 30."""
    return 25.0 if height < 2500 else 30.0


def stile_depth(height: float) -> float:
    """A ladder's side rail is 70 deep if the ladder is under 2500 tall, else 90."""
    return 70.0 if height < 2500 else 90.0


def rung_count(height: float) -> int:
    """Rungs of a ladder: one per 280 of height, less one, at least 2."""
    return int(max(2, round_to(height / 280, 1) - 1))


def rung_diameter(height: float) -> float:
    """A round rung is 30 across if the ladder is under 2500 tall, else 35."""
    return 30.0 if height < 2500 else 35.0


def tread_thickness(height: float) -> float:
    """A flat step of a ladder is 25 thick if the ladder is under 2500 tall, else 30."""
    return 25.0 if height < 2500 else 30.0


def stabiliser_width(width: float) -> float:
    """The stabiliser bar under a ladder is 1.8 times the ladder's width (to the nearest 10)."""
    return round_to(1.8 * width, 10)


# --- pallets --------------------------------------------------------------------------------

def pallet_board_thickness(span: float) -> float:
    """Pallet boards: 18 thick if the pallet's longer side is under 1000, else 22."""
    return 18.0 if span < 1000 else 22.0


def pallet_board_width(span: float) -> float:
    """Pallet boards: 80 wide if the pallet's longer side is under 1000, else 100."""
    return 80.0 if span < 1000 else 100.0


def deck_board_count(length: float) -> int:
    """Deck boards of a pallet: one per 160 of its length, at least 4 and at most 11."""
    return int(_clamp(round_to(length / 160, 1), 4, 11))


def bottom_board_count(length: float) -> int:
    """Bottom boards of a pallet: 3 if it is under 1200 long, else 5."""
    return 3 if length < 1200 else 5


def stringer_count(width: float) -> int:
    """Stringers of a pallet: 3 if it is under 1100 wide, else 4."""
    return 3 if width < 1100 else 4


def stringer_width(span: float) -> float:
    """A pallet stringer is 45 wide if the pallet's longer side is under 1000, else 60."""
    return 45.0 if span < 1000 else 60.0


def stringer_height(span: float) -> float:
    """A pallet stringer is 90 tall if the pallet's longer side is under 1000, else 100."""
    return 90.0 if span < 1000 else 100.0


# --- carts ----------------------------------------------------------------------------------

def wheel_diameter(length: float) -> float:
    """Cart wheels: 100 across if the deck is under 800 long, 125 under 1000, else 160."""
    return 100.0 if length < 800 else 125.0 if length < 1000 else 160.0


def wheel_width(diameter: float) -> float:
    """A cart wheel is 0.3 of its diameter wide (down to 5)."""
    return floor_to(0.3 * diameter, 5)


def axle_size(diameter: float) -> float:
    """A cart's square axle beam is 0.3 of the wheel diameter (down to 5)."""
    return floor_to(0.3 * diameter, 5)


def handle_diameter(post: float) -> float:
    """A round handle bar is 0.8 of the thickness of the posts that hold it (down to 5)."""
    return floor_to(0.8 * post, 5)


def cart_side_height(length: float) -> float:
    """Side boards of a cart are 15% of the deck's length tall (to the nearest 10)."""
    return round_to(0.15 * length, 10)


def cart_shelf_top(deck_top: float, handle_top: float) -> float:
    """The upper shelf of a cart is half-way between the deck and the handle (to the
    nearest 10)."""
    return round_to((deck_top + handle_top) / 2, 10)


# --- plates on standoffs --------------------------------------------------------------------

def plate_thickness(span: float) -> float:
    """A plate on standoffs: 3 thick if its longer side is under 100, 5 under 200, else 8."""
    return 3.0 if span < 100 else 5.0 if span < 200 else 8.0


def standoff_diameter(span: float) -> float:
    """A standoff: 6 across if the plate's longer side is under 100, 8 under 200, else 12."""
    return 6.0 if span < 100 else 8.0 if span < 200 else 12.0


def standoff_spread(side: float, standoff: float) -> float:
    """Standoffs stand one standoff width in from each edge of the plate: side - 2 x standoff
    between their outer faces."""
    return side - 2 * standoff


def standoff_circle(diameter: float, standoff: float) -> float:
    """Standoffs under a round plate stand on a circle 3 standoff widths smaller than the
    plate."""
    return diameter - 3 * standoff


def standoff_count(diameter: float) -> int:
    """Standoffs under a round plate: 3 if it is under 120 across, else 4."""
    return 3 if diameter < 120 else 4


# --- fences ---------------------------------------------------------------------------------

def fence_post_thickness(height: float) -> float:
    """A fence post is 80 square if the fence is under 1300 tall, else 100."""
    return 80.0 if height < 1300 else 100.0


def fence_rail_count(height: float) -> int:
    """Rails of a fence panel: 2 if it is under 1300 tall, else 3."""
    return 2 if height < 1300 else 3


def fence_rail_bottom(height: float) -> float:
    """The lowest fence rail starts 15% of the fence's height above the ground (to the
    nearest 10); the highest ends as far below the top of the posts."""
    return round_to(0.15 * height, 10)


def picket_count(clear_width: float) -> int:
    """Fence pickets: one per 130 of the width between the posts, at least 3."""
    return int(max(3, round_to(clear_width / 130, 1)))


def picket_width(height: float) -> float:
    """A fence picket is 70 wide if the fence is under 1300 tall, else 90."""
    return 70.0 if height < 1300 else 90.0


def picket_thickness(post: float) -> float:
    """A fence picket is a quarter as thick as the post."""
    return post / 4


def picket_gap() -> float:
    """Pickets end 50 above the ground and 50 below the top of the posts."""
    return 50.0


def cap_rail_height(post: float) -> float:
    """A cap rail lying on the fence posts is 0.4 of a post thickness tall."""
    return 0.4 * post


# --- toolboxes ------------------------------------------------------------------------------

def tote_side_height(height: float) -> float:
    """The long sides of an open toolbox are 45% of its overall height tall (to the
    nearest 10)."""
    return round_to(0.45 * height, 10)


def tote_handle_size(depth: float) -> float:
    """The handle of an open toolbox is 25 across if the box is under 220 deep, else 30."""
    return 25.0 if depth < 220 else 30.0
