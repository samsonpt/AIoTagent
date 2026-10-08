import zlib

import numpy as np


def make_rng(seed: int, stream: str) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(stream.encode("utf-8"))]))
