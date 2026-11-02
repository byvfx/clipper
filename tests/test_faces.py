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


def test_head_box_grows_to_the_head_and_follows_the_16_9_crop():
    assert faces.head_box([0.4, 0.4, 0.1, 0.2]) == {"x": 0.38, "y": 0.29, "width": 0.14, "height": 0.35}
    assert faces.head_box([0.0, 0.0, 0.1, 0.1])["x"] == 0.0  # clamped into the frame
    # 4:3 source: grab keeps the middle 16:9 band (rows 1/8 to 7/8)
    b = faces.head_box([0.4, 0.5, 0.1, 0.1], size=(1440, 1080))
    assert b["y"] == pytest.approx((0.5 - 0.055 - 0.125) / 0.75, abs=1e-3)


def test_local_time_and_video_faces():
    parts = [(100.0, 200.0, 0.0), (500.0, 600.0, 100.0)]
    assert faces.local_time(parts, 150) == 50 and faces.local_time(parts, 550) == 150
    assert faces.local_time(parts, 300) is None
    data = {"groups": {"F01": {"name": "Jer", "floor": 0.0}, "F02": {"name": "Andy", "floor": 0.0},
                       "F03": {"floor": 0.0}},
            "faces": [
                {"t": 120, "group": "F01", "sim": 0.9, "sharp": 50, "frontal": 1, "px": 200, "box": [0.1, 0.1, 0.1, 0.1]},
                {"t": 120, "group": "F02", "sim": 0.9, "sharp": 10, "frontal": 1, "px": 200, "box": [0.6, 0.1, 0.1, 0.1]},
                {"t": 520, "group": "F01", "sim": 0.9, "sharp": 90, "frontal": 1, "px": 200, "box": [0.1, 0.1, 0.1, 0.1]},
                {"t": 300, "group": "F01", "sim": 0.9, "sharp": 999, "frontal": 1, "px": 200, "box": [0.1, 0.1, 0.1, 0.1]},
                {"t": 130, "group": "F03", "sim": 0.9, "sharp": 999, "frontal": 1, "px": 200, "box": [0.1, 0.1, 0.1, 0.1]},
            ]}
    got = faces.video_faces(data, parts, people=4, per=2)
    assert [t for t, _ in got] == [520, 120]  # 300 is cut out, F03 has no name
    assert sorted(x["person"] for x in got[1][1]) == ["Andy", "Jer"]
    assert [t for t, _ in faces.video_faces(data, parts, people=1, per=1)] == [520]


def test_cluster_sample_doesnt_follow_order_within_a_frame():
    # two people per frame, always in the same order: every 2nd would only see the first
    a, b = people(2, 1, seed=5)
    rng = np.random.default_rng(1)
    emb = np.array([faces.unit(v + rng.normal(scale=0.02, size=128)) for _ in range(40) for v in (a, b)],
                   dtype=np.float32)
    lab = faces.cluster(emb, 0.4, max_n=40)
    assert len(set(lab[0::2])) == 1 and len(set(lab[1::2])) == 1 and lab[0] != lab[1] and 0 not in lab


def test_video_faces_skips_strays_and_faces_outside_the_crop():
    f = {"sharp": 50, "frontal": 1, "px": 200}
    data = {"groups": {"F01": {"name": "Jer", "floor": 0.6}, "F02": {"name": "Andy", "floor": 0.0}},
            "size": [1440, 1080],  # 4:3: grab keeps rows 1/8 to 7/8
            "faces": [{**f, "t": 10, "group": "F01", "sim": 0.9, "box": [0.4, 0.4, 0.1, 0.1]},
                      {**f, "t": 10, "group": "F02", "sim": 0.9, "box": [0.1, 0.0, 0.05, 0.04]},  # above the crop
                      {**f, "t": 20, "group": "F01", "sim": 0.3, "sharp": 999, "box": [0.4, 0.4, 0.1, 0.1]}]}
    got = faces.video_faces(data, [(0.0, 100.0, 0.0)], people=4, per=2)
    assert [(t, [x["person"] for x in found]) for t, found in got] == [(10, ["Jer"])]


def test_scan_save_keeps_names_set_meanwhile(tmp_path, defaults):
    # what scan() does as it saves: hand names come from faces.json as it is then
    groups, fs = faces.build_groups(rows(12), people(2, 6), FC)
    jsonstate.write(tmp_path / "faces.json", {"model": faces.MODEL, "groups": groups, "faces": fs})
    faces.set_names(tmp_path, {"F02": "PPP"})  # e.g. while a rescan runs
    new = {k: dict(g) for k, g in groups.items()}
    with jsonstate.update(tmp_path / "faces.json", {}) as cur:
        faces.carry_names(new, cur["groups"])
    assert new["F02"]["name"] == "PPP"
    cfg = {**defaults, "paths": {**defaults["paths"], "faces": str(tmp_path / "known")}}
    assert faces.add_known(cfg, "PPP", groups["F02"]["centroid"]) == 1
    assert faces.add_known(cfg, "PPP", groups["F02"]["centroid"]) == 2
