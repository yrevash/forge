"""The random plan generator (arithmetic tier: no CAD kernel is started)."""

import json

from forge.plan import parse_plan
from forge.plans_data import config, coverage, fast
from forge.plans_data.random_gen import Balance, Maker, all_shape_cells
from forge.plans_data.record import NoKernel, solution, solution_of

SEEDS = range(12)


def make(seed: int, **kw):
    return Maker(f"test:{seed}", "arithmetic", NoKernel(), **kw).make()


def test_every_kept_plan_is_accepted_and_complete() -> None:
    made = [m for m in (make(seed) for seed in SEEDS) if m is not None]
    assert len(made) >= 10
    for plan in made:
        assert plan.plan.accepted
        assert plan.resolution.complete
        assert all(reply.built for reply in plan.resolution.replies)
        assert 2 <= len(plan.plan.lines) <= config.MAX_TRAIN_LINES
        assert plan.text.endswith("done\n")


def test_a_fresh_resolution_of_the_text_gives_the_same_result() -> None:
    """The plan was resolved line by line while candidates came and went, with shared
    bodies. Reading and resolving the finished text from scratch, without that short-cut,
    must give exactly the stored replies and parts."""
    fast.share_bodies()
    made = [m for m in (make(seed) for seed in SEEDS) if m is not None]
    fast.copy_bodies()
    for plan in made:
        judge = NoKernel()
        _, _, fresh = solution_of(plan.text, judge)
        assert judge.asked == 0
        assert json.dumps(fresh) == json.dumps(solution(plan.resolution))


def test_the_same_seed_gives_the_same_plan() -> None:
    first, second = make(3), make(3)
    assert first.text == second.text
    assert make(4).text != first.text


def test_a_negative_plan_keeps_a_rejected_line_with_its_reply() -> None:
    made = [m for m in (make(seed, negative=True) for seed in range(20)) if m is not None]
    assert made
    kinds = set()
    for plan in made:
        replies = [reply.reply for reply in plan.resolution.replies]
        assert not plan.resolution.complete
        assert any(reply != "built" for reply in replies)
        _, _, fresh = solution_of(plan.text, NoKernel())
        assert [reply["reply"] for reply in fresh["replies"]] == replies
        kinds |= {coverage.reply_word(reply) for reply in replies}
    assert len(kinds) >= 3      # more than one kind of rejection among twenty plans


def test_a_long_plan_is_longer_than_any_training_plan() -> None:
    made = next(m for m in (make(seed, long=True) for seed in range(6)) if m is not None)
    assert config.LONG_LINES[0] <= len(made.plan.lines) <= config.LONG_LINES[1]
    assert len(made.plan.lines) > config.MAX_TRAIN_LINES


def test_no_part_takes_the_name_of_a_copy_of_a_set() -> None:
    """The resolver calls the copies of `pegs` "pegs 1", "pegs 2"; a part called "pegs 2"
    would replace one of them. The generator uses one base word once per plan."""
    for seed in SEEDS:
        made = make(seed)
        if made is None:
            continue
        names = [line.name for line in made.plan.lines if line.kind in ("part", "group")]
        bodies = [body.name for body in made.resolution.bodies]
        assert len(bodies) == len(set(bodies))
        assert not [name for name in names if name not in bodies and name in
                    {body for body in bodies if body[-1].isdigit()} - set(names)]


def test_single_part_plans_cover_every_shape_and_turn_without_the_kernel() -> None:
    judge = NoKernel()
    seen = set()
    for number, cell in enumerate(all_shape_cells()):
        made = Maker(f"single:{number}", "arithmetic", judge).make_single(cell)
        if made is None:
            continue
        assert made.plan.accepted and made.resolution.complete
        cells = coverage.cells_of(made.plan.lines, ["built", "built"])
        seen |= {c for c in cells if c.startswith("shape:")}
    assert judge.asked == 0
    assert len(seen) >= len(all_shape_cells()) - 2


def test_balance_prefers_the_least_used_choice() -> None:
    import random
    balance = Balance()
    balance.counts[("family", "a")] = 5
    assert balance.least("family", ["a", "b"], random.Random(0)) == "b"


def test_reader_and_generator_agree_on_plural_names() -> None:
    plan = parse_plan("base: box 100 by 100 by 10, on ground\n"
                      "pegs: cylinder 10 by 20, on top of base, at each corner of base\n"
                      "cap: box 100 by 100 by 5, on top of pegs, across pegs, flush with pegs' left\n"
                      "done\n")
    assert plan.accepted


def test_a_featured_part_has_one_part_and_features_on_its_faces() -> None:
    """One part, several faces. The judge here answers "one solid" without the kernel
    (parts.TrustStored), which is enough to test how the lines are written."""
    from forge.plans_data.featured import FeaturedMaker
    from forge.plans_data.parts import TrustStored

    made = [m for m in (FeaturedMaker(f"test:{seed}", "kernel", TrustStored()).make()
                        for seed in range(8)) if m is not None]
    assert len(made) >= 5
    faces = set()
    for plan in made:
        assert plan.plan.accepted and plan.resolution.complete
        assert len(plan.resolution.bodies) == 1
        kinds = [line.kind for line in plan.plan.lines]
        assert kinds[0] == "part" and kinds[-1] == "done"
        assert set(kinds[1:-1]) == {"feature"}
        faces |= {line.face for line in plan.plan.lines if line.kind == "feature"}
    assert len(faces) >= 4


def _fake_judge():
    """Stands in for the kernel when only the WRITING of lines is tested: everything touches."""
    from forge.resolve.judge import KernelJudge, View

    class Fake(KernelJudge):
        def __init__(self) -> None:
            super().__init__(sandbox=None)

        def look(self, bodies):
            names = [body.name for body in bodies]
            return View(solids=dict.fromkeys(names, 1),
                        touching={frozenset((a, b)) for a in names for b in names if a < b})

        def close(self) -> None:
            pass

    return Fake()


def test_the_kernel_top_ups_write_lines_the_reader_accepts() -> None:
    """ktopup and ktopup2 aim at inside / through / around / spans, inner faces and
    features on a bottom face. With a stand-in judge this checks the wording only."""
    from forge.plans_data.ktopup import KernelTopUp
    from forge.plans_data.ktopup2 import KernelTopUp2

    cells: set[str] = set()
    for maker in (KernelTopUp, KernelTopUp2):
        for seed in range(25):
            run = maker(f"test:{seed}", "kernel", _fake_judge())
            made = run.make()
            assert run.stats.get("not understood", 0) <= 2
            if made is not None:
                assert made.plan.accepted
                cells |= set(coverage.cells_of(made.plan.lines,
                                               [r.reply for r in made.resolution.replies]))
    families = {cell.split("|")[0] for cell in cells}
    for wanted in ("place:spans", "place:inside", "place:through", "place:around",
                   "size:default", "feat:rounded corners"):
        assert wanted in families, wanted
    assert any(cell.startswith("align-inner:") for cell in cells)
    assert any(cell.startswith("hollow:open at") for cell in cells)


def test_the_second_arithmetic_top_up_writes_reference_amounts() -> None:
    from forge.plans_data.topup2 import TopUp2Maker

    cells: list[str] = []
    for seed in range(15):
        made = TopUp2Maker(f"test:{seed}", "arithmetic", NoKernel()).make()
        if made is not None:
            assert made.plan.accepted and made.resolution.complete
            cells += coverage.cells_of(made.plan.lines, ["built"] * len(made.plan.lines))
    assert cells.count("amount:same as") >= 5
    assert any(cell.startswith("group:placed|") for cell in cells)
