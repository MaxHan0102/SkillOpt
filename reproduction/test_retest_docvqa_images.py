"""Protect image-limit handling and exact task replacement in the retest."""
import pytest

from reproduction.retest_docvqa_images import IDS, merge, patches, resize_image


@pytest.mark.parametrize("size", [(5367, 7184), (6915, 5386)])
def test_oversized_documents_fit_with_margin_without_cropping(size, tmp_path):
    from PIL import Image

    source, target = tmp_path / "original.png", tmp_path / "resized.png"
    Image.new("RGB", size, "white").save(source)
    before = source.read_bytes()
    info = resize_image(source, target)
    assert info["original_patches_32px"] > 30000
    assert info["resized_patches_32px"] <= 28000
    with Image.open(target) as image:
        assert image.size == (int(size[0] * 0.85), int(size[1] * 0.85))
        assert abs(image.width / image.height - size[0] / size[1]) < 0.001
    assert source.read_bytes() == before


def test_small_image_is_not_downscaled(tmp_path):
    from PIL import Image

    source, target = tmp_path / "original.png", tmp_path / "resized.png"
    Image.new("RGB", (400, 500), "white").save(source)
    info = resize_image(source, target)
    assert info["resized_size"] == [400, 500]
    assert patches(400, 500) == info["resized_patches_32px"]


def test_merge_replaces_only_authorized_failures_and_keeps_new_wrong_answers():
    original = [{"id": key, "hard": 0, "soft": 0} for key in ["ordinary_wrong", *IDS]]
    fresh = [{"id": key, "hard": int(i == 0), "soft": int(i == 0)} for i, key in enumerate(IDS)]
    combined = merge(original, fresh, [row["id"] for row in original])
    assert len(combined) == 4
    assert combined[0] == {**original[0], "provenance": "original"}
    assert [row["hard"] for row in combined] == [0, 1, 0, 0]
    assert all(row["hard"] == 0 for row in original)
    with pytest.raises(ValueError):
        merge(original, fresh[:-1], [row["id"] for row in original])
