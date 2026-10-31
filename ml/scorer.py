
import json
import math
import struct

NAN = float("nan")


def f32(v):
    try:
        return struct.unpack("f", struct.pack("f", v))[0]
    except (OverflowError, struct.error):
        return v


class FraudScorer:
    def __init__(self, path):
        with open(path) as f:
            m = json.load(f)
        self.features = m["features"]
        p = m["base_score"]
        self.margin0 = math.log(p / (1 - p))     # base score as a logit
        self.trees = [
            (t["left"], t["right"], [f32(c) for c in t["cond"]],
             t["feat"], t["dl"])
            for t in m["trees"]
        ]

    @staticmethod
    def _tree_value(tree, vals):
        left, right, cond, feat, dl = tree
        i = 0
        while left[i] != -1:                    # -1 means "this node is a leaf"
            v = vals[feat[i]]
            if v != v:                          # missing / NaN
                i = left[i] if dl[i] else right[i]
            else:
                i = left[i] if v < cond[i] else right[i]
        return cond[i]                          # a leaf stores its value here

    def score(self, x):
        """x: dict of feature name -> value. Returns fraud probability."""
        vals = [NAN if x.get(n) is None else f32(x[n]) for n in self.features]
        margin = self.margin0 + sum(self._tree_value(t, vals) for t in self.trees)
        return 1.0 / (1.0 + math.exp(-margin))