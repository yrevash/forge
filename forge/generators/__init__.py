"""Part-family generators. `FAMILIES` is the registry everything else reads."""

from forge.generators.families.blocks import BLOCK_FAMILIES
from forge.generators.families.composed import COMPOSED_FAMILIES
from forge.generators.families.fasteners import (
    HexBolt,
    HexNut,
    PlainWasher,
    ShaftKey,
    SocketHeadCapScrew,
    SquareNut,
)
from forge.generators.families.flat_plates import FLAT_PLATE_FAMILIES
from forge.generators.families.plates import LBracket, PlateWithHoles, RoundFlange, Spacer
from forge.generators.families.sections import SECTION_FAMILIES
from forge.generators.families.shapes import (
    BlockWithFeatures,
    ChamferedBlock,
    CounterboredBlock,
    IBeam,
    MountingPlate,
    OpenBox,
    PolygonPrism,
    SlottedLink,
    SteppedShaft,
    TaperedBlock,
    TSection,
    UChannel,
)
from forge.generators.families.standard_parts import STANDARD_PART_FAMILIES
from forge.generators.families.turned import TURNED_FAMILIES

FAMILIES = {
    family.NAME: family
    for family in (
        HexNut(), SquareNut(), PlainWasher(), HexBolt(), SocketHeadCapScrew(), ShaftKey(),
        Spacer(), PlateWithHoles(), RoundFlange(), LBracket(),
        MountingPlate(), PolygonPrism(), SlottedLink(), UChannel(), TSection(), IBeam(),
        SteppedShaft(), BlockWithFeatures(), ChamferedBlock(), OpenBox(), TaperedBlock(),
        CounterboredBlock(),
        # Added after the first 22. Each file lists its own families in a tuple.
        *TURNED_FAMILIES, *FLAT_PLATE_FAMILIES, *SECTION_FAMILIES, *BLOCK_FAMILIES,
        *STANDARD_PART_FAMILIES,
        # Random structure instead of a fixed recipe: one family per base shape.
        *COMPOSED_FAMILIES,
    )
}
