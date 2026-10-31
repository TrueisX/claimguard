
import json
import math


class FraudScorer:
    def __init__(self, path):
        with open(path) as f:
            m = json.load(f)
        self.features = m["features"]
        p = m["base_score"]
        self.margin0 = math.log(p / (1 - p))     # base score as a logit
        self.trees = m["trees"]

    def _leaf_value(self, node, x):
        while "leaf" not in node:
            name = node["split"]
            if name not in x and name[0] == "f" and name[1:].isdigit():
                name = self.features[int(name[1:])]     # f3 -> feature #3
            v = x.get(name)
            if v is None or v != v:                      # missing / NaN
                target = node["missing"]
            else:
                target = node["yes"] if v < node["split_condition"] else node["no"]
            for child in node["children"]:
                if child["nodeid"] == target:
                    node = child
                    break
        return node["leaf"]

    def score(self, x):
        """x: dict of feature name -> value. Returns fraud probability."""
        margin = self.margin0 + sum(self._leaf_value(t, x) for t in self.trees)
        return 1.0 / (1.0 + math.exp(-margin))