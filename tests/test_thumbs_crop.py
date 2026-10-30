from PIL import Image

from clipper import thumbs

FACE = {"x": 0.55, "y": 0.35, "width": 0.12, "height": 0.2}  # a face right of center
SIZE = (470, 594)


def face_px(win: tuple, fw: int, fh: int, b: dict = FACE) -> tuple[float, float, float, float]:
    """The face box in output pixels for crop window win."""
    x0, y0, w, _ = win
    s = SIZE[0] / w
    fx0, fy0 = (b["x"] * fw - x0) * s, (b["y"] * fh - y0) * s
    return fx0, fy0, fx0 + b["width"] * fw * s, fy0 + b["height"] * fh * s


def overlap(a: tuple, b: tuple) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def test_default_crop_is_unchanged():
    # The crop before avoid= existed: head at 72% of the height, face 45% down, kept inside.
    fw, fh, tw, th = 1920, 1080, *SIZE
    cx, cy = (FACE["x"] + FACE["width"] / 2) * fw, (FACE["y"] + FACE["height"] / 2) * fh
    h = min(fh, max(FACE["height"] * fh / 0.72, th / 2.0))
    w = h * tw / th
    old = (min(max(cx - w / 2, 0), fw - w), min(max(cy - h * 0.45, 0), fh - h), w, h)
    assert thumbs.crop_window(fw, fh, FACE, SIZE, 0.72, 2.0) == old


def test_nothing_in_the_way_keeps_the_default():
    win = thumbs.crop_window(1920, 1080, FACE, SIZE, 0.72, 2.0)
    assert thumbs.clear_window(1920, 1080, FACE, SIZE, 0.72, 2.0, [(0, 0, 5, 5)]) == win


def test_the_face_is_moved_out_from_under_an_inset():
    # The Enquirer cover's main photo (470 x 594) and its reaction inset, bottom right.
    fw, fh, inset = 1920, 1080, (273, 415, 470, 594)
    low = {"x": 0.58, "y": 0.6, "width": 0.08, "height": 0.16}
    face = thumbs.crop_window(fw, fh, low, SIZE, 0.72, 2.0)
    assert overlap(face_px(face, fw, fh, low), inset) > 0
    moved = thumbs.clear_window(fw, fh, low, SIZE, 0.72, 2.0, [inset])
    assert overlap(face_px(moved, fw, fh, low), inset) == 0


def test_face_crop_with_avoid_gives_the_size_asked(tmp_path):
    src = tmp_path / "frame.jpg"
    Image.new("RGB", (1920, 1080), (90, 60, 40)).save(src)
    out = thumbs.face_crop(src, FACE, SIZE, avoid=[(273, 415, 470, 594)])
    assert out.size == SIZE
