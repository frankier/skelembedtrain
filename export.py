from typing import Any, Dict

import click
import orjson

from skeldump.skelgraphs.openpose import (
    BODY_25,
    BODY_25_HANDS,
    LEFT_HAND_IN_BODY_25,
    RIGHT_HAND_IN_BODY_25,
    UPPER_BODY_25,
    UPPER_BODY_25_HANDS,
    UPPER_BODY_25_LEFT_HAND,
    UPPER_BODY_25_RIGHT_HAND,
)
from skeldump.skelgraphs.utils import kp_pairs

EXPORT_SKELS = [
    (("BODY_25", BODY_25),),
    (("UPPER_BODY_25", UPPER_BODY_25),),
    (("BODY_25_HANDS", BODY_25_HANDS),),
    (("UPPER_BODY_25_HANDS", UPPER_BODY_25_HANDS),),
    (
        ("UPPER_BODY_25_LEFT_HAND", UPPER_BODY_25_LEFT_HAND),
        ("UPPER_BODY_25_RIGHT_HAND", UPPER_BODY_25_RIGHT_HAND),
    ),
    (
        ("LEFT_HAND_IN_BODY_25", LEFT_HAND_IN_BODY_25),
        ("RIGHT_HAND_IN_BODY_25", RIGHT_HAND_IN_BODY_25),
    ),
]


def default(obj):
    if isinstance(obj, set):
        return list(obj)
    raise TypeError


@click.command()
@click.argument("outf", type=click.File("wb"))
def export(outf):
    dump: Dict[str, Any] = {
        "__WARNING__": "This file is automatically generated by skelshop --- please do not manually edit!"
    }
    dump["__KP_PAIRS__"] = list(kp_pairs(BODY_25_HANDS.lines))
    for skels in EXPORT_SKELS:
        for idx, (skel_name, skel_type) in enumerate(skels):
            exported = skel_type.export()
            if len(skels) == 2:
                exported["flipped"] = skels[1 - idx][0]
            dump[skel_name] = exported
    outf.write(orjson.dumps(dump, option=orjson.OPT_NON_STR_KEYS, default=default))


if __name__ == "__main__":
    export()
