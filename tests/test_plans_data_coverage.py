"""The coverage table and the split rules of forge/plans_data (no CAD kernel)."""

from forge.plan import grammar as g
from forge.plan import parse_plan
from forge.plans_data import config, coverage, splits

PLAN = """\
base: box 400 by 300 by 20, on ground
legs: cylinder 20 by 60, on top of base, inset 10, at each corner of base
deck: box rest by rest by 10, on top of legs, across legs
tiles: box 20 by 5 by 20, in front of base, grid 3 by 1
rail: box 10 by 10 by rest lying along depth, between base and deck, offset 5 to the left
done
"""


def cells_of(text: str) -> list[str]:
    plan = parse_plan(text)
    assert plan.accepted
    return coverage.cells_of(plan.lines, ["built"] * len(plan.lines))


def test_universe_holds_every_shape_in_every_turn() -> None:
    table = coverage.universe()
    shapes = [cell for cell in table if cell.startswith("shape:")]
    # box, cylinder, tube, prism x 3; five bar profiles x 3; sphere; cone, dome, tapered box x 6;
    # wedge: 6 directions x 4 flat faces
    assert len(shapes) == 4 * 3 + 5 * 3 + 1 + 3 * 6 + 24
    for placement in g.FACE_PLACEMENTS:
        assert f"place:{placement}" in table
        for option in ("gap", "sunk", "across"):
            assert f"option:{option}|{placement}" in table
    for face in g.FACES:
        assert f"align:flush|{face}" in table and f"align:offset|{face}" in table
    for name in g.FEATURES:
        assert f"feat:{name}|top" in table


def test_minimums_follow_the_tier() -> None:
    table = coverage.universe()
    assert table["shape:box|standing"] == {"kernel": False, "minimum": config.MIN_ARITHMETIC}
    assert table["shape:cone|pointing down"]["kernel"] is True
    assert table["feat:slot|front"]["minimum"] == config.MIN_KERNEL
    assert table["reply:overlaps"]["minimum"] == config.MIN_REPLY


def test_an_impossible_pair_is_not_required() -> None:
    table = coverage.universe()
    assert "pair:place×rep|left of|corner" not in table       # a single corner needs a top or bottom
    assert "pair:place×rep|on top of|corner" in table
    assert coverage.impossible_pair("pair:place×align|on top of|down to ground")


def test_cells_are_read_off_the_structured_plan() -> None:
    cells = cells_of(PLAN)
    for cell in ("shape:box|standing", "shape:cylinder|standing", "shape:box|lying along depth",
                 "place:on ground", "place:on top of", "place:in front of", "place:between|depth",
                 "align:inset|corner", "rep:each corner|top-bottom face", "option:across|on top of",
                 "size:rest|across", "size:rest|between", "rep:grid|front-back face",
                 "align:offset|left", "pair:place×target|on top of|cylinder",
                 "pair:place×rep|in front of|grid", "pair:place×align|between|offset",
                 "control:done"):
        assert cell in cells, cell
    assert cells.count("reply:built") == 6


def test_a_rejected_line_counts_only_as_its_reply() -> None:
    plan = parse_plan(PLAN)
    replies = ["built", "rejected: overlaps base", "built", "built", "built", "built"]
    cells = coverage.cells_of(plan.lines, replies)
    assert "reply:overlaps" in cells
    assert "rep:each corner|top-bottom face" not in cells


def test_held_out_combinations_are_found() -> None:
    plan = parse_plan(PLAN)
    replies = ["built"] * len(plan.lines)
    found = splits.held_out_combinations(plan.lines, replies)
    assert found == ["align:offset + place:between", "rep:grid + place:in front of"]
    split, _ = splits.assign("random", "x", plan.lines, replies)
    assert split == "combo"


def test_two_held_out_feature_kinds_on_one_part() -> None:
    plan = parse_plan("plate: box 100 by 80 by 10, on ground\n"
                      "on plate: boss diameter 10, height 5, at (20, 0)\n"
                      "on plate: slot length 20, width 5, depth 2, at (-20, 0)\ndone\n")
    replies = ["built"] * 4
    assert splits.held_out_combinations(plan.lines, replies) == ["feature:boss + feature:slot"]


def test_split_order_kinds_long_combo_then_hash() -> None:
    plain = parse_plan("base: box 400 by 300 by 20, on ground\ndone\n")
    replies = ["built", "built"]
    assert splits.assign("structures", "x", plain.lines, replies, kind="stool",
                         held_out_kinds=config.HELD_OUT_KINDS)[0] == "kinds"
    long = parse_plan("base: box 400 by 300 by 20, on ground\n"
                      + "".join(f"p{i}: box 5 by 5 by 5, on top of base\n" for i in range(30)) + "done\n")
    assert splits.assign("random", "x", long.lines, ["built"] * len(long.lines))[0] == "long"
    seen = {splits.assign("random", f"id{i}", plain.lines, replies)[0] for i in range(400)}
    assert seen == {"train", "iid"}
    share = sum(splits.assign("random", f"id{i}", plain.lines, replies)[0] == "iid"
                for i in range(4000)) / 4000
    assert 0.03 < share < 0.07


def test_held_out_pairs_are_not_required_cells() -> None:
    """A combination held out of training on purpose must have a train count of zero, so
    its pair cell cannot also have a minimum."""
    from forge.plans_data import table

    held = table.held_out_cells()
    assert "pair:place×rep|in front of|grid" in held
    assert "pair:place×target|behind|prism" in held
    assert "pair:place×align|between|offset" in held
    rows = table.rows({}, {})
    assert not set(held) & set(rows)
    assert "pair:place×rep|behind|grid" in rows
