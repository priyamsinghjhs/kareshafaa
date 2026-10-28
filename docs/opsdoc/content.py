"""Document body. Every fact comes from the KareShafaa code and docs at master aac2f3d; where the solution design
differs, the code is followed."""
from content_a import part_a
from content_b import part_b
from content_c import part_c
from content_c2 import part_c2
from content_d import part_d
from content_e import part_e
from content_f import part_f


def build(g):
    for part in (part_a, part_b, part_c, part_c2, part_d, part_e, part_f):
        part(g)
