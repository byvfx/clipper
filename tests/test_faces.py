import numpy as np
import pytest

from clipper import faces, jsonstate
from clipper.errors import ClipperError

FC = {"cluster": 0.4, "min_faces": 3}


def people(n: int, per: int, seed: int = 0) -> np.ndarray:
    """per embeddings around each of n random directions (near 0.9 to their own, ~0 to others)."""
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(n, 128))
    out = [faces.unit(c + rng.normal(scale=0.25, size=128)) for c in centres for _ in range(per)]
    return np.array(out, dtype=np.float32)


def rows(n: int) -> list[dict]:
    return [{"t": float(k), "i": k + 1, "box": [0.4, 0.3, 0.1, 0.2], "px": 100 + k, "sharp": 50.0,
             "frontal": 0.9} for k in range(n)]


def test_cluster_separates_people_and_subsamples():
    emb = people(3, 20)
    lab = faces.cluster(emb, 0.4)
    assert len({tuple(lab[k * 20:(k + 1) * 20]) for k in range(3)}) == 3
    assert all(len(set(lab[k * 20:(k + 1) * 20])) == 1 for k in range(3))
    sub = faces.cluster(emb, 0.4, max_n=20)  # most join by centroid
    assert all(len(set(sub[k * 20:(k + 1) * 20])) == 1 for k in range(3))


def test_build_groups_biggest_first_and_drops_small():
    emb = np.concatenate([people(1, 10, seed=1), people(1, 4, seed=2), people(1, 2, seed=3)])
    groups, fs = faces.build_groups(rows(len(emb)), emb, FC)
    assert list(groups) == ["F01", "F02"]
    assert groups["F01"]["n"] == 10 and groups["F02"]["n"] == 4
    assert len(fs) == 14 and [f["t"] for f in fs] == sorted(f["t"] for f in fs)
    assert len(groups["F01"]["centroid"]) == 128
    assert all(f["sim"] > 0.5 for f in fs)


def test_names_hand_wins_and_threshold():
    emb = people(2, 6)
    groups, _ = faces.build_groups(rows(12), emb, FC)
    a, b = groups["F01"]["centroid"], groups["F02"]["centroid"]
    groups["F02"].update(name="Andy", by="hand")
    faces.name_groups(groups, {"Jer": [a], "Don": [list(-np.array(a))]}, 0.7)
    assert groups["F01"]["name"] == "Jer" and groups["F01"]["by"] == "face"
    assert groups["F02"]["name"] == "Andy"  # hand name kept
    faces.name_groups(groups, {"Jer": [b]}, 0.7)  # no longer like anyone known
    assert "name" not in groups["F01"] and groups["F01"]["closest"] == "Jer"


def test_rescan_keeps_hand_names():
    emb = people(2, 6)
    old, _ = faces.build_groups(rows(12), emb, FC)
    old["F02"].update(name="PPP", by="hand")
    new, _ = faces.build_groups(rows(12), emb[::-1].copy(), FC)
    faces.carry_names(new, old)
    named = [k for k, g in new.items() if g.get("name") == "PPP"]
    assert len(named) == 1
    assert np.dot(new[named[0]]["centroid"], old["F02"]["centroid"]) > 0.99


def test_best_faces_skips_strays_and_ranks():
    data = {"groups": {"F01": {"name": "Jer", "floor": 0.6}, "F02": {"name": "Don", "floor": 0.0}},
            "faces": [
                {"t": 10, "group": "F01", "sim": 0.8, "sharp": 50, "frontal": 1.0, "px": 100},
                {"t": 20, "group": "F01", "sim": 0.8, "sharp": 90, "frontal": 1.0, "px": 200},
                {"t": 30, "group": "F01", "sim": 0.5, "sharp": 999, "frontal": 1.0, "px": 300},  # a stray
                {"t": 40, "group": "F02", "sim": 0.9, "sharp": 999, "frontal": 1.0, "px": 300},
            ]}
    assert [f["t"] for f in faces.best_faces(data, "jer", gap=5)] == [20, 10]
    assert [f["t"] for f in faces.best_faces(data, "jer")] == [20]  # 10 is the same moment
    assert [f["t"] for f in faces.best_faces(data, "Jer", a=15, b=25)] == [20]
    assert faces.best_faces(data, "Nobody") == []


def test_set_names_and_enroll(tmp_path, defaults):
    cfg = {**defaults, "paths": {**defaults["paths"], "faces": str(tmp_path / "known")}}
    groups, fs = faces.build_groups(rows(12), people(2, 6), FC)
    jsonstate.write(tmp_path / "faces.json", {"model": faces.MODEL, "groups": groups, "faces": fs})
    with pytest.raises(ClipperError):
        faces.set_names(tmp_path, {"F09": "Jer"})
    with pytest.raises(ClipperError):
        faces.enroll(tmp_path, cfg, ["F01"])  # not named yet
    faces.set_names(tmp_path, {"F01": "Jer"})
    faces.enroll(tmp_path, cfg, ["F01"])
    found = faces.apply(tmp_path, cfg)
    assert found["F01"]["name"] == "Jer" and found["F01"]["by"] == "hand"
    assert faces.load_known(cfg)["people"]["Jer"] == [groups["F01"]["centroid"]]
    faces.set_names(tmp_path, {"F01": ""})  # cleared: now named by the enrolled face
    assert faces.apply(tmp_path, cfg)["F01"]["by"] == "face"
